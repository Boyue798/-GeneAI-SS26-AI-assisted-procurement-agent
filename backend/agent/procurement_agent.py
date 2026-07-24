from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from agent.parser import IntentParser
from agent.ranker import LLMRanker
from agent.retriever import SupplierRetriever
from web_research.researcher import SearXNGSearchProvider, SearchResult, StaticPageFetcher, WebResearcher
from web_research.idealo_scraper import search_idealo
from web_research.marketplace import MarketplaceSearchLayer
from web_research.wlw_scraper import search_wlw
from database import query_suppliers_sync, query_products_sync

BASE_DIR = Path(__file__).resolve().parents[1]


class ProcurementAgent:
    QUOTE_WEB_BLOCKED_DOMAINS = (
        "temu.com",
        "play.google.com",
        "chromewebstore.google.com",
        "kimi.com",
        "claude.com",
        "gemini.google.com",
        "tinypng.com",
        "stitch.withgoogle.com",
        "google.com",
        "youtube.com",
        "facebook.com",
        "instagram.com",
        "pinterest.com",
        "wikipedia.org",
        "reddit.com",
        "linkedin.com",
        "deutschepost.de",
        "rechneronline.de",
        "omnicalculator.com",
        "euroshop-online.de",
        "preis.de",
        "bitpanda.com",
        "dockers.com",
        "dockers.eu",
        "dockandbay.com",
        "dockandbay.eu",
        "ebay.de",
        "ebay.com",
        "chip.de",
        "garden-dock.com",
    )
    QUOTE_WEB_STOPWORDS = {
        "supplier", "price", "germany", "b2b", "quote", "quotes", "buy", "shop",
        "online", "standard", "product", "products", "hardware", "office", "search",
        "采购", "供应商", "标准品", "报价", "德国", "价格", "比价",
    }

    def __init__(self):
        self.llm = self._create_llm()
        self.suppliers = query_suppliers_sync()
        self.quotes = query_products_sync()
        self.parser = IntentParser(self.llm)
        chroma_collection = self._create_chroma_collection()
        self.retriever = SupplierRetriever(chroma_collection, self.suppliers, llm=self.llm)
        self.ranker = LLMRanker(self.llm)
        self.quote_search_provider = SearXNGSearchProvider()
        self.quote_page_fetcher = StaticPageFetcher()
        # External marketplace APIs are optional. With no configured credentials
        # this layer is inert and the existing Idealo/web-search path is used.
        self.marketplace_search = MarketplaceSearchLayer()
        self.web_researcher = WebResearcher(
            search_provider=self.quote_search_provider,
            page_fetcher=self.quote_page_fetcher,
            llm=self.llm,
        )

    def refresh_local_catalog(self) -> None:
        """Reload editable supplier and quote data without restarting the API.

        The supplier-directory API calls this after a successful write.  It is
        deliberately synchronous because the data access layer is synchronous
        at startup too; failed or unavailable database connections simply yield
        an empty local catalogue and leave web research available.
        """
        self.suppliers = query_suppliers_sync()
        self.quotes = query_products_sync()
        self.retriever = SupplierRetriever(
            self._create_chroma_collection(), self.suppliers, llm=self.llm
        )

    async def search_suppliers(
        self,
        query: str,
        progress=None,
        structured: dict | None = None,
        criteria: list[dict] | None = None,
    ) -> dict:
        """Full pipeline: parse → local DB → WLW B2B → WebResearcher → merge → rank.

        Pipeline stages:
        1. Parse user intent (LLM)
        2. Search local supplier database (Chroma)
        3. If local results < threshold, search WLW.de B2B directory
        4. If WLW returns < threshold, fall back to WebResearcher (DDG + LLM)
        5. Merge all sources, rank, filter by matchScore >= 60

        structured, when provided, contains B2B-procurement fields from the
        frontend's structured form; these override LLM-parsed intent values.
        criteria contains user-defined evaluation dimensions and weights; it
        only changes ranking when explicitly supplied.
        """
        if progress:
            progress("parse", "正在解析您的采购需求，提取品类、地区、预算等关键信息...", 10)

        intent = await self.parser.parse(query)

        # ── Apply structured form overrides (double-check) ─────────────
        if structured:
            if structured.get("category"):
                intent.category = structured["category"]
            if structured.get("targetRegion") or structured.get("country"):
                intent.country = structured.get("targetRegion") or structured["country"]
            certification_text = ",".join(
                value for value in [structured.get("certifications"), structured.get("standards")]
                if value
            )
            if certification_text:
                certs = [c.strip().upper() for c in certification_text.split(",") if c.strip()]
                intent.certifications = list(dict.fromkeys([*certs, *intent.certifications]))
            sf_keywords = []
            for sf_field in [
                "productName", "quantity", "brand", "model", "specifications",
                "minOrderQuantity", "qualityRequirements", "environmentalRequirements",
            ]:
                val = structured.get(sf_field)
                if val and val not in sf_keywords:
                    sf_keywords.append(val)
            if structured.get("unit"):
                sf_keywords.append(structured["unit"])
            if sf_keywords:
                intent.keywords = list(dict.fromkeys([*sf_keywords, *intent.keywords]))

        category = intent.category or "general procurement"
        country = intent.country or "any target country"
        max_price = intent.max_price or 0

        if progress:
            budget_detail = f"预算 €{max_price}" if max_price else "未设置预算"
            progress("parse", f"已理解需求：品类「{category}」、目标地区「{country}」、{budget_detail}。", 18)

        # ── ✨ Translate to German BEFORE local DB and web search ─────
        # WLW.de and WebResearcher search German sites. Translate the Chinese
        # query to German/English keywords and inject them into intent.keywords
        # so the Chroma retriever and downstream steps all benefit.
        german_supplier_phrase = ""
        if self.llm:
            if progress:
                progress("parse", "正在将中文需求翻译为德语/英语，以便匹配德文供应商数据库...", 22)
            german_kw = await self._llm_search_keywords_async(query)
            if german_kw:
                german_supplier_phrase = german_kw
                de_terms = [t for t in german_kw.split() if len(t) >= 2]
                intent.keywords = list(dict.fromkeys([*de_terms, *(intent.keywords or [])]))
                if progress:
                    progress("parse", f"已翻译为德语搜索词：「{german_supplier_phrase}」。", 26)

        # ── Phase 1: Local + Web search in PARALLEL ──────────────────
        if progress:
            progress("retrieve", "同时启动本地数据库和网络搜索，并行执行以最大程度缩短等待时间...", 30)

        async def _run_local():
            return await self.retriever.search(intent, query=query, progress=progress)

        async def _run_web():
            # DeepSeek web search — WLW.de results come through
            # site:wlw.de and site:europages.de queries in researcher.
            try:
                return await self._normalize_web_suppliers(
                    await self.web_researcher.research(intent, max_suppliers=10, progress=progress),
                    intent,
                )
            except Exception as e:
                if progress:
                    progress("web", f"WebResearcher 失败: {e}", 52)
                return []

        # Execute local + web in parallel
        local_task = asyncio.create_task(_run_local())
        web_task = asyncio.create_task(_run_web())

        local_candidates = await local_task
        web_candidates = await web_task
        # Both sources now conform to the same comparison-table contract.
        local_candidates = [
            self._ensure_supplier_contract(candidate, intent, default_source="database")
            for candidate in local_candidates
        ]
        web_candidates = [
            self._ensure_supplier_contract(candidate, intent, default_source="web")
            for candidate in web_candidates
        ]

        if progress:
            progress("retrieve", f"本地数据库：找到 {len(local_candidates)} 家候选供应商。", 44)

        # ── Phase 4: Merge all sources ─────────────────────────────────
        all_candidates = self._merge_supplier_candidates(local_candidates, web_candidates)

        local_count = sum(1 for c in all_candidates if c.get("source") in (None, "database"))
        web_count = sum(1 for c in all_candidates if c.get("source") == "web")
        if progress:
            progress("retrieve", f"候选汇总：本地 {local_count} 家 + 网络 {web_count} 家，共 {len(all_candidates)} 家。", 68)

        # ── Phase 5: Rank & filter ─────────────────────────────────────
        if progress:
            progress("rank", "正在根据采购需求对候选供应商进行智能排序和质量过滤...", 82)

        ranked = [
            supplier
            for supplier in await self.ranker.rank_suppliers(query, all_candidates, criteria=criteria)
            if int(supplier.get("matchScore", 0) or 0) >= 50  # 轻量模式兼容：词法匹配产生较低分数
        ]

        top = ranked[0]["name"] if ranked else "未找到匹配供应商"
        if progress:
            local_ranked = sum(1 for r in ranked if r.get("source") in (None, "database"))
            web_ranked = sum(1 for r in ranked if r.get("source") == "web")
            progress("rank", f"排序完成！共 {len(ranked)} 家（本地 {local_ranked} + 网络 {web_ranked}），最佳匹配：{top}。", 95)

        intent_payload = intent.model_dump()
        intent_payload["appliedCriteria"] = criteria or []
        return {"intent": intent_payload, "results": ranked}

    # ── Supplier web search helpers ──────────────────────────────────

    @classmethod
    def _supplier_search_phrase(cls, query: str, intent) -> str:
        """Extract a search-friendly phrase from intent for WLW / B2B directory search."""
        keywords = getattr(intent, "keywords", []) or []
        category = getattr(intent, "category", None) or ""
        # Prefer explicit keywords, fall back to category + query words
        if keywords:
            return " ".join([str(k) for k in keywords[:4] if str(k).strip()])
        # Strip constraint words, keep product/business nouns
        cleaned = re.sub(r'[\d\s]*(预算|不超过|以内|欧元|台|个|元)[\d\s]*', ' ', query)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if category and category not in cleaned.lower():
            cleaned = f"{cleaned} {category}"
        return cleaned[:80]

    async def _normalize_web_suppliers(
        self, web_results: list[dict], intent
    ) -> list[dict]:
        """Normalize WLW / WebResearcher output to unified supplier format.

        Each result gets the stable comparison-table fields, even where a web
        page did not expose them.  Missing information stays ``None`` / an
        explicit manual-check label rather than being guessed by the model.
        """
        normalized = []
        for item in web_results:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            name = item.get("name") or item.get("company_name") or item.get("title", "")
            website = item.get("website") or item.get("url", "")
            if not name and not website:
                continue
            page_text = " ".join(
                str(item.get(key) or "")
                for key in ("description", "title", "evidenceSnippets")
            )
            if (
                item.get("is_supplier") is False
                or self._is_supplier_noise_page(name, item)
                or WebResearcher.is_non_supplier_page(
                    title=name,
                    url=website,
                    text=page_text,
                )
            ):
                continue
            # Directory landing pages are product leads, not supplier records.
            # Individual company URLs from the same directory remain eligible.
            if self._is_supplier_directory_page(website):
                continue
            products = self._string_values(item.get("products") or item.get("matched_products"))
            certifications = self._string_values(item.get("certifications"))
            standards = self._string_values(item.get("standards"))
            # Product proof is a minimum requirement for a new web supplier.
            # Keep locally curated records even if incomplete, but do not add
            # a generic directory/company page to the decision table merely
            # because it exposes a contact address.
            if not products:
                continue
            source_urls = self._string_values(item.get("sourceUrls")) or ([website] if website else [])
            evidence = self._string_values(item.get("evidenceSnippets"))
            verification_status, verification_notes = self._supplier_verification(
                website=website,
                products=products,
                certifications=certifications,
                phone=item.get("phone"),
                email=item.get("email"),
                required_certifications=getattr(intent, "certifications", None),
            )
            # Keep the public provenance vocabulary small and stable.  The web
            # researcher uses more specific internal labels (for example
            # ``web-research-llm``); exposing those values made the frontend
            # classify valid web rows as local database records.
            raw_source = str(item.get("source") or "").strip().casefold()
            public_source = "database" if raw_source in {"database", "local"} else "web"
            source_detail = item.get("sourceDetail") or raw_source or "web"
            normalized.append({
                **item,
                "id": item.get("id", f"web-{abs(hash(website or name)) % 10_000_000}"),
                "name": name or "Web Supplier",
                "website": website,
                "category": item.get("category") or getattr(intent, "category", None) or "general",
                "country": item.get("country") or item.get("location", ""),
                "city": item.get("city", ""),
                "description": item.get("description", ""),
                "products": products,
                "productName": item.get("productName") or item.get("product") or (products[0] if products else None),
                "brand": item.get("brand"),
                "model": item.get("model"),
                "specifications": item.get("specifications") or item.get("specification"),
                "standards": standards,
                "capabilities": self._string_values(item.get("capabilities") or item.get("supplier_type")),
                "certifications": certifications,
                "matchScore": item.get("matchScore") or item.get("score", 60),
                "phone": item.get("phone", ""),
                "email": item.get("email", ""),
                "contactPerson": item.get("contactPerson") or item.get("contact_person", ""),
                "employees": item.get("employees") or item.get("employee_count", ""),
                "source": public_source,
                "sourceDetail": source_detail,
                "sourceUrls": source_urls,
                "evidenceSnippets": evidence,
                "is_supplier": item.get("is_supplier", True),
                "unitPriceEur": item.get("unitPriceEur"),
                "unitLabel": item.get("unitLabel") or "需人工核价",
                "quoteConditions": item.get("quoteConditions"),
                "deliveryDays": item.get("deliveryDays"),
                "deliveryLabel": item.get("deliveryLabel") or item.get("delivery_range") or "需确认交期",
                "paymentTerm": item.get("paymentTerm"),
                "paymentLabel": item.get("paymentLabel") or "需确认付款方式",
                # Crawler-provided verification flags are not trusted.  The
                # deterministic evidence check below is the source of truth.
                "verificationStatus": verification_status,
                "verificationNotes": verification_notes,
                # Extra fields from WLW / web extraction.
                "supplier_type": item.get("supplier_type", []),
                "founding_year": item.get("founding_year"),
                "delivery_range": item.get("delivery_range", ""),
            })
        return [
            self._ensure_supplier_contract(candidate, intent, default_source="web")
            for candidate in normalized
        ]

    @staticmethod
    def _string_values(value) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def _ensure_supplier_contract(cls, item: dict, intent, default_source: str) -> dict:
        """Fill stable supplier-result fields without fabricating missing data."""
        result = dict(item)
        products = cls._string_values(result.get("products") or result.get("matched_products"))
        certifications = cls._string_values(result.get("certifications"))
        standards = cls._string_values(result.get("standards"))
        website = str(result.get("website") or result.get("url") or "")
        source_urls = cls._string_values(result.get("sourceUrls")) or ([website] if website else [])
        evidence = cls._string_values(result.get("evidenceSnippets"))
        verification_status, verification_notes = cls._supplier_verification(
            website=website,
            products=products,
            certifications=certifications,
            phone=result.get("phone"),
            email=result.get("email"),
            required_certifications=getattr(intent, "certifications", None),
        )
        # Do not expose page numbers, SKUs or URL fragments as contact data.
        # They remain available in the evidence/source page for manual review,
        # but the structured contact columns should only contain usable values.
        phone = result.get("phone") if cls._valid_phone(result.get("phone")) else ""
        email = result.get("email") if cls._valid_email(result.get("email")) else ""
        is_web_result = default_source == "web" or str(result.get("source") or "").casefold().startswith("web")
        unit_price = result.get("unitPriceEur")
        if unit_price is None:
            unit_price = result.get("unitPrice")
        if unit_price is None:
            unit_price = result.get("price")
        # Supplier pages often expose an explicit EUR price in the captured
        # evidence even when the extractor did not map it to a field. Promote
        # only that explicit evidence; never infer a currency or invent a
        # quote from a phone number, ID, or vague price text.
        price_label = None
        if unit_price is None and is_web_result:
            evidence_text = " ".join(
                [*evidence, str(result.get("description") or ""), str(result.get("quoteConditions") or "")]
            )
            range_match = re.search(
                r"([0-9][0-9.,]*)\s*[-–]\s*([0-9][0-9.,]*)\s*(?:EUR|Euro|€)",
                evidence_text,
                flags=re.IGNORECASE,
            )
            if range_match:
                low = cls._parse_price_number(range_match.group(1))
                high = cls._parse_price_number(range_match.group(2))
                if low is not None and high is not None and 0.08 < low <= high < 100000:
                    unit_price = high
                    price_label = f"€ {low:.2f}–{high:.2f}"
            if unit_price is None:
                unit_price = cls._extract_eur_price(evidence_text)
        price_confidence = result.get("priceConfidence")
        if not price_confidence:
            price_confidence = "extracted" if unit_price is not None else "unknown"
        delivery_label = result.get("deliveryLeadTime") or result.get("deliveryLabel") or result.get("delivery_range") or "需确认交期"
        payment_terms = result.get("paymentTerms") or result.get("paymentLabel") or result.get("paymentTerm") or "需确认付款方式"
        valid_contact = cls._has_valid_contact(result.get("phone"), result.get("email"))
        completeness_fields = [
            bool(website), bool(products), bool(result.get("phone") or result.get("email")),
            bool(certifications), unit_price is not None,
            delivery_label != "需确认交期", payment_terms != "需确认付款方式",
        ]
        completeness_fields[2] = valid_contact
        result.update({
            "id": result.get("id") or f"{default_source}-{abs(hash(website or result.get('name', 'supplier'))) % 10_000_000}",
            "name": result.get("name") or result.get("company_name") or "Supplier",
            "category": result.get("category") or getattr(intent, "category", None) or "general",
            "country": result.get("country") or result.get("location") or "",
            "city": result.get("city") or "",
            "address": result.get("address") or "",
            "website": website,
            "products": products,
            "productName": result.get("productName") or result.get("product") or (products[0] if products else None),
            "brand": result.get("brand"),
            "model": result.get("model"),
            "specifications": result.get("specifications") or result.get("specification"),
            "standards": standards,
            "capabilities": cls._string_values(result.get("capabilities") or result.get("supplier_type")),
            "certifications": certifications,
            "contactPerson": result.get("contactPerson") or result.get("contact_person") or "",
            "phone": phone,
            "email": email,
            "employees": result.get("employees") or result.get("employee_count") or "",
            "annualRevenue": result.get("annualRevenue") or "",
            "established": result.get("established") or result.get("founding_year"),
            "unitPriceEur": unit_price,
            # Compatibility names used by the sourcing comparison table and
            # its Excel export.  Keep the canonical *Eur fields too.
            "unitPrice": unit_price,
            "currency": result.get("currency") or "EUR",
            "unitLabel": (
                price_label
                or (f"€ {float(unit_price):.2f}" if unit_price is not None and result.get("unitLabel") in (None, "", "需人工核价") else None)
                or result.get("unitLabel")
                or "需人工核价"
            ),
            "priceConfidence": price_confidence,
            "quoteConditions": result.get("quoteConditions"),
            "deliveryDays": result.get("deliveryDays"),
            "deliveryLabel": delivery_label,
            "deliveryLeadTime": delivery_label,
            "paymentTerm": result.get("paymentTerm"),
            "paymentLabel": payment_terms,
            "paymentTerms": payment_terms,
            "source": result.get("source") or default_source,
            "sourceDetail": result.get("sourceDetail") or default_source,
            "sourceUrls": source_urls,
            "evidenceSnippets": evidence,
            # For web candidates, an upstream "verified" flag must not mask a
            # malformed URL/page-id contact value. Local database provenance is
            # preserved, while still falling back to deterministic evidence.
            "verificationStatus": verification_status if is_web_result else result.get("verificationStatus") or verification_status,
            "verificationNotes": verification_notes if is_web_result else result.get("verificationNotes") or verification_notes,
            "dataCompleteness": round(sum(completeness_fields) / len(completeness_fields) * 100),
            "matchScore": result.get("matchScore") or result.get("score") or 60,
        })
        if default_source == "database":
            result.setdefault("repurchasePriority", "database")
        return result

    @staticmethod
    def _valid_phone(value) -> bool:
        """Accept formatted phone-like values, not URLs or numeric IDs."""
        if not isinstance(value, str):
            return False
        text = value.strip()
        lowered = text.casefold()
        if (
            not text
            or lowered.startswith(("www.", "mailto:"))
            or "://" in text
            or "/" in text
            or "\\" in text
            or "@" in text
            or re.search(r"\b(?:id|page|number|no|sku|listing)\b", lowered)
            or re.fullmatch(r"20\d{2}\s*[-–/]\s*20\d{2}", text)
        ):
            return False
        digits = re.sub(r"\D", "", text)
        if not 7 <= len(digits) <= 15:
            return False
        # A bare numeric token is indistinguishable from a crawler record ID;
        # require a phone separator or international prefix as evidence.
        return bool(re.search(r"[+()\-.,\s]", text))

    @staticmethod
    def _valid_email(value) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        return bool(
            re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                text,
            )
        )

    @classmethod
    def _has_valid_contact(cls, phone, email) -> bool:
        return cls._valid_phone(phone) or cls._valid_email(email)

    @staticmethod
    def _is_supplier_directory_page(value: object) -> bool:
        """Recognize directory/search landing pages that are not companies."""
        if not isinstance(value, str) or not value.strip():
            return False
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        path = (parsed.path or "").casefold()
        if not any(
            marker in host
            for marker in (
                "europages", "wlw.", "werliefertwas", "kompass", "industrystock",
                "lieferanten.", "directindustry",
            )
        ):
            return False
        return any(
            marker in path
            for marker in (
                "/showroom/", "/search/", "/suche/", "/recherche/", "/companies/",
                "/unternehmen/", "/suppliers/", "/lieferanten/", "/tag/",
            )
        )

    @staticmethod
    def _is_supplier_noise_page(name: object, item: dict) -> bool:
        """Reject maintenance/login/error pages masquerading as suppliers."""
        normalized_name = str(name or "").strip().casefold()
        if normalized_name in {"web supplier", "supplier", "unknown supplier", "supplier directory"}:
            return True
        website = str(item.get("website") or item.get("url") or "").casefold()
        if any(host in website for host in ("apps.apple.com", "play.google.com", "appstore.com")):
            return True
        text = " ".join(
            str(item.get(key) or "")
            for key in ("description", "title", "evidenceSnippets")
        ).casefold()
        return any(
            marker in text
            for marker in (
                "site under maintenance", "scheduled maintenance", "access denied",
                "captcha verification", "request unsuccessful", "enable javascript",
            )
        )

    @classmethod
    def _supplier_verification(
        cls,
        *,
        website: str,
        products: list[str],
        certifications: list[str],
        phone,
        email,
        required_certifications: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Return transparent, deterministic verification metadata for web rows."""
        missing: list[str] = []
        if not website:
            missing.append("缺少官网链接")
        if not products:
            missing.append("缺少产品证据")
        has_valid_contact = cls._has_valid_contact(phone, email)
        if not has_valid_contact:
            missing.append("缺少可验证联系方式")
        provided_certifications = {
            re.sub(r"[^a-z0-9]", "", str(cert).casefold())
            for cert in certifications
            if str(cert).strip()
        }
        requested = [
            str(cert).strip()
            for cert in (required_certifications or [])
            if str(cert).strip()
        ]
        if requested:
            for certification in requested:
                normalized = re.sub(r"[^a-z0-9]", "", certification.casefold())
                if normalized and normalized not in provided_certifications:
                    missing.append(f"未找到所需认证：{certification}")
        elif not certifications:
            missing.append("未发现认证信息")
        # A contact value is a hard verification gate.  A URL, page number, or
        # bare numeric ID must never turn a crawler row into "verified" merely
        # because the other fields happen to be present.
        return ("verified" if has_valid_contact and not missing else "needs-review", missing)

    @staticmethod
    def _merge_supplier_candidates(
        local: list[dict], web: list[dict]
    ) -> list[dict]:
        """Merge local + web candidates and preserve local provenance on duplicates."""
        merged: dict[str, dict] = {}
        for item in [*local, *web]:
            key = ProcurementAgent._supplier_identity_key(item)
            if not key:
                key = str(len(merged))
            existing = merged.get(key)
            # Directory profiles regularly use a different domain from the
            # local company record. When their leading legal entity matches,
            # merge the web evidence into the local supplier instead of
            # showing the same company twice in the decision table.
            if existing is None and ProcurementAgent._is_directory_supplier_profile(item):
                entity_key = ProcurementAgent._supplier_entity_name_key(item)
                if entity_key:
                    for known_key, candidate in merged.items():
                        if (
                            candidate.get("source") in (None, "database")
                            and ProcurementAgent._supplier_entity_name_key(candidate) == entity_key
                        ):
                            key = known_key
                            existing = candidate
                            break
            if existing is None:
                merged[key] = item
                continue
            existing_is_local = existing.get("source") in (None, "database")
            item_is_local = item.get("source") in (None, "database")
            if existing_is_local != item_is_local:
                local_item = dict(existing if existing_is_local else item)
                web_item = item if existing_is_local else existing
                # Keep the authoritative company record visibly local, while
                # enriching it with contact/product evidence found online.
                for field in (
                    "website", "address", "phone", "email", "contactPerson", "description",
                    "productName", "brand", "model", "specifications", "unitPriceEur",
                    "quoteConditions", "deliveryDays", "deliveryLabel", "paymentTerm", "paymentLabel",
                ):
                    if not local_item.get(field) and web_item.get(field):
                        local_item[field] = web_item[field]
                for list_field in (
                    "products", "capabilities", "certifications", "standards", "sourceUrls", "evidenceSnippets",
                ):
                    combined = [*(local_item.get(list_field) or []), *(web_item.get(list_field) or [])]
                    local_item[list_field] = list(dict.fromkeys(str(value) for value in combined if value))
                local_item["matchScore"] = max(
                    int(local_item.get("matchScore", 0) or 0), int(web_item.get("matchScore", 0) or 0)
                )
                local_item["source"] = "database"
                local_detail = local_item.get("sourceDetail") or "database"
                local_item["sourceDetail"] = (
                    local_detail if "web" in str(local_detail).casefold() else f"{local_detail}+web"
                )
                if web_item.get("verificationStatus") == "verified":
                    local_item["verificationStatus"] = "verified"
                merged[key] = local_item
                continue
            # Prefer the entry with higher matchScore or more evidence
            existing_score = int(existing.get("matchScore", 0) or 0)
            item_score = int(item.get("matchScore", 0) or 0)
            existing_evidence = bool(existing.get("evidenceSnippets") or existing.get("description"))
            item_evidence = bool(item.get("evidenceSnippets") or item.get("description"))
            if item_score > existing_score or (item_score == existing_score and item_evidence and not existing_evidence):
                merged[key] = item
        return list(merged.values())

    @staticmethod
    def _supplier_identity_key(item: dict) -> str:
        website = str(item.get("website") or item.get("url") or "").strip()
        if website:
            parsed = urlparse(website if "://" in website else f"https://{website}")
            host = (parsed.hostname or "").casefold().removeprefix("www.")
            if host:
                # Directory pages host many unrelated companies.  Keying only
                # by the directory domain collapses all of them into one row.
                # Real company sites still merge by domain so local + web
                # enrichment continues to work as before.
                directory_host = any(
                    marker in host
                    for marker in (
                        "europages", "wlw.", "werliefertwas", "lieferanten.",
                        "industrystock", "kompass", "supplier-directory",
                    )
                )
                if not directory_host:
                    return f"site:{host}"
                name = re.sub(r"[^\w]+", "", str(item.get("name") or "").casefold())
                return f"site:{host}|name:{name}" if name else f"site:{host}|url:{parsed.path.casefold()}"
        name = re.sub(r"[^\w]+", "", str(item.get("name") or "").casefold())
        return f"name:{name}" if name else ""

    @staticmethod
    def _is_directory_supplier_profile(item: dict) -> bool:
        website = str(item.get("website") or item.get("url") or "").strip()
        parsed = urlparse(website if "://" in website else f"https://{website}")
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        return any(
            marker in host
            for marker in (
                "europages", "wlw.", "werliefertwas", "lieferanten.",
                "industrystock", "kompass", "directindustry",
            )
        )

    @staticmethod
    def _supplier_entity_name_key(item: dict) -> str:
        """Return a cautious leading legal-entity key for directory enrichment."""

        name = str(item.get("name") or "").casefold()
        # Search-title suffixes (for example "in Heidelberg, ... auf
        # europages") are descriptive, not part of the legal company name.
        name = re.split(r"\b(?:in|auf|at|from|on)\b", name, maxsplit=1)[0]
        ignored = {
            "ag", "co", "company", "gmbh", "kg", "kgaa", "llc", "ltd",
            "sarl", "spa", "bv", "inc", "und", "and", "the", "de", "der",
        }
        tokens = [
            token
            for token in re.findall(r"[a-z0-9äöüß]+", name)
            if token not in ignored and len(token) >= 5
        ]
        return tokens[0] if tokens else ""

    async def search_quotes(
        self,
        query: str,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        delivery_time: Optional[str] = None,
        weights: Optional[dict] = None,
        progress=None,
        criteria: Optional[list[dict]] = None,
        country: Optional[str] = None,
    ) -> dict:
        """Full pipeline for standard-product quote comparison.

        Pipeline: parse → translate to German → local DB → idealo → DDG → merge → rank.
        Key insight: the database is in German, so we translate the Chinese query to
        German/English keywords FIRST and inject them into intent for all downstream steps.
        """
        if progress:
            progress("parse", "正在解析标准品比价需求，合并自然语言、前置过滤条件和权重偏好...", 10)

        intent = await self.parser.parse(query)
        # The comparison form can supply a target market explicitly. Apply it
        # before the marketplace stage so SerpApi receives the intended Google
        # Shopping country even when natural-language parsing is ambiguous.
        if country and country.strip():
            intent.country = country.strip()
        max_delivery_days = self._delivery_time_to_days(delivery_time) or intent.max_delivery_days
        effective_max_price = max_price if max_price is not None else intent.max_price
        category = intent.category or "all standard products"
        delivery_label = f"{max_delivery_days} 天内" if max_delivery_days else "不限时效"
        weight_text = ""
        if weights:
            weight_text = (
                f"；权重：价格 {weights.get('price', 40)}%、"
                f"交付 {weights.get('delivery', 35)}%、评价 {weights.get('rating', 25)}%"
            )
        if criteria:
            weight_text += f"；自定义维度 {len(criteria)} 项"

        if progress:
            budget_detail = f"€{min_price or 0}–€{effective_max_price}" if effective_max_price else f"最低 €{min_price}" if min_price else "未设置预算"
            progress("parse", f"已理解需求：品类「{category}」、预算「{budget_detail}」、交付「{delivery_label}」{weight_text}。", 18)

        # ── ✨ STEP 0: Translate to German/English IMMEDIATELY ──────────
        # The database and websites are in German. We must translate the user's
        # Chinese query right away and inject German keywords into intent so
        # ALL downstream steps (local DB filter, web search, LLM filter) benefit.
        german_phrase = ""
        groups = self._quote_required_term_groups(query, intent)
        if not groups and self.llm:
            if progress:
                progress("parse", "正在将中文需求翻译为德语/英语关键词，以便匹配德文数据库和德国电商网站...", 21)
            german_kw = await self._llm_search_keywords_async(query)
            if german_kw:
                # LLM keyword cleanup can remove separators from exact models
                # such as ``XC2011/01``. Keep those identifiers verbatim so
                # marketplace APIs search the intended SKU/model, not merely
                # the broad product category.
                model_terms = self._quote_model_identifiers(query)
                german_phrase = " ".join(dict.fromkeys([german_kw, *model_terms]))
                # Inject German terms into intent.keywords so local DB filter can use them
                de_terms = [t for t in german_kw.split() if len(t) >= 2]
                intent.keywords = list(dict.fromkeys([*de_terms, *(intent.keywords or [])]))
                if progress:
                    progress("parse", f"已翻译为德语搜索词：「{german_phrase}」，将用于本地数据库匹配和网站搜索。", 24)

        if progress:
            progress("retrieve", "第一步先检索本地标准品/报价数据库，确认是否已有可比价商品...", 28)

        # 本地候选：优先按 category 精确匹配，匹配不到则不过滤
        local_candidates = [
            {**quote, "source": quote.get("source", "database"), "sourceDetail": quote.get("sourceDetail", "database")}
            for quote in self.quotes
            if self._quote_has_decision_vendor(quote)
            if intent.category is None
            or not quote.get("category")
            or quote.get("category") == intent.category
        ]
        # 如果 category 精确匹配返回 0（如 DB 里是 "office" 但 parser 提取了 "paper"），
        # 回退到不过滤 category，靠后续关键词和 _is_relevant_quote_item 来做相关性判断
        if not local_candidates:
            local_candidates = [
                {**quote, "source": quote.get("source", "database"), "sourceDetail": quote.get("sourceDetail", "database")}
                for quote in self.quotes
            ]
        # When category is unknown, pre-filter by keyword to avoid flooding
        # the pipeline with 200+ irrelevant items (e.g., A4 paper when user
        # searches for "人体工学椅"). The full _is_relevant_quote_item check
        # follows below but this quick gate keeps the candidate list manageable.
        if intent.category is None and len(local_candidates) > 30:
            kw_terms = [t for t in (getattr(intent, "keywords", []) or []) if len(str(t)) >= 2]
            if kw_terms:
                pre_filtered = []
                for quote in local_candidates:
                    text = " ".join(str(quote.get(k, "")) for k in ("product", "vendor", "description") if quote.get(k))
                    if any(str(kw).lower() in text.lower() for kw in kw_terms):
                        pre_filtered.append(quote)
                # Keep at least 10 even if no keyword matches (avoid empty list for common queries)
                if len(pre_filtered) >= 5:
                    local_candidates = pre_filtered
        local_candidates = [
            quote for quote in local_candidates
            if self._is_relevant_quote_item(quote, query, intent)
        ]

        if progress:
            progress("retrieve", f"本地标准品/报价库已检索完成：找到 {len(local_candidates)} 条本地候选，正在应用前置过滤条件...", 42)

        if progress:
            progress("web", "第二步开始联网搜索 — 优先检查已配置的快速市场 API，再补充 Idealo 和搜索引擎...", 50)
        
        # ── Determine search phrase (reuse German translation if available) ──
        if german_phrase:
            search_phrase = german_phrase
        else:
            search_phrase = self._quote_search_product_phrase(query, intent)
        
        # Phase A: optional fast marketplace APIs. They are intentionally opt-in
        # and can return stable, structured item prices without page scraping.
        marketplace_candidates: list[dict] = []
        marketplace_minimum = self._marketplace_short_circuit_minimum()
        marketplace_search = getattr(self, "marketplace_search", None)
        if marketplace_search is not None:
            try:
                if progress and getattr(marketplace_search, "enabled", True):
                    progress(
                        "web",
                        "正在查询已配置的市场 API（仅纳入明确 EUR 价格；其他币种继续由 Idealo/网页来源补充，不自动换汇）...",
                        51,
                    )
                marketplace_candidates = await marketplace_search.search(
                    search_phrase,
                    country=getattr(intent, "country", None),
                    limit=8,
                    min_priced_results=marketplace_minimum,
                )
                marketplace_candidates = [
                    candidate
                    for candidate in marketplace_candidates
                    if self._is_relevant_quote_item(candidate, query, intent)
                ]
            except Exception:
                marketplace_candidates = []

        marketplace_priced = sum(
            1 for candidate in marketplace_candidates if candidate.get("unitPriceEur") is not None
        )
        if marketplace_priced >= marketplace_minimum:
            # The API has enough priced product candidates; avoid slower page
            # scraping for this request. Relevance is checked again by the final
            # LLM/rule filter before ranking.
            web_candidates = marketplace_candidates
            if progress:
                progress(
                    "web",
                    f"市场 API 已返回 {marketplace_priced} 条带价候选，跳过本次 Idealo/网页抓取以缩短等待时间。",
                    64,
                )
        else:
            # API candidates remain usable even when they do not yet meet the
            # desired comparison-table depth.  Supplement them for a bounded
            # interval instead of allowing a slow crawler/LLM path to hold the
            # whole request open indefinitely.
            fallback_timeout = self._quote_web_fallback_timeout_seconds(
                has_marketplace_prices=marketplace_priced > 0,
            )
            web_task = asyncio.create_task(
                self._search_web_quotes(
                    query,
                    intent,
                    max_results=8,
                    progress=progress,
                    pre_translated_phrase=search_phrase,
                )
            )
            use_idealo = self._should_search_idealo_for_country(getattr(intent, "country", None))
            if use_idealo:
                if progress:
                    progress("web", f"正在并行查询 idealo.de 和网页搜索：「{search_phrase}」...", 52)
                # Idealo is a German price-comparison source. It is only used
                # for Germany/default requests, never as a hidden substitute
                # for a buyer-selected foreign market.
                idealo_task = asyncio.create_task(
                    search_idealo(search_phrase, limit=4, timeout=self._idealo_timeout_seconds())
                )
                try:
                    idealo_result, web_result = await asyncio.wait_for(
                        asyncio.gather(idealo_task, web_task, return_exceptions=True),
                        timeout=fallback_timeout,
                    )
                except asyncio.TimeoutError:
                    idealo_task.cancel()
                    web_task.cancel()
                    await asyncio.gather(idealo_task, web_task, return_exceptions=True)
                    idealo_result, web_result = [], []
                    if progress:
                        progress(
                            "web",
                            f"网页补充搜索超过 {fallback_timeout:.0f} 秒，已保留已验证的市场 API/本地报价继续生成比价表。",
                            66,
                        )
                idealo_candidates = idealo_result if isinstance(idealo_result, list) else []
            else:
                market_label = self._quote_market_label(getattr(intent, "country", None))
                if progress:
                    progress(
                        "web",
                        f"目标市场「{market_label}」不使用德国 idealo.de，正在执行该市场的网页价格搜索...",
                        52,
                    )
                try:
                    web_result = await asyncio.wait_for(web_task, timeout=fallback_timeout)
                except asyncio.TimeoutError:
                    web_task.cancel()
                    await asyncio.gather(web_task, return_exceptions=True)
                    web_result = []
                    if progress:
                        progress(
                            "web",
                            f"网页补充搜索超过 {fallback_timeout:.0f} 秒，已保留已验证的市场 API/本地报价继续生成比价表。",
                            66,
                        )
                idealo_candidates = []
            searched_candidates = web_result if isinstance(web_result, list) else []
            if progress and idealo_candidates:
                progress("web", f"idealo.de 返回 {len(idealo_candidates)} 条比价候选（含商店/价格/评分）。", 55)
            web_candidates = self._merge_quote_candidates(marketplace_candidates, idealo_candidates)
            web_candidates = self._merge_quote_candidates(web_candidates, searched_candidates)

        if progress:
            price_known = sum(1 for item in web_candidates if item.get("unitPriceEur") is not None)
            progress("web", f"网络搜索完成：找到 {len(web_candidates)} 条网络候选，其中 {price_known} 条提取到明确价格，其余标记为需人工核价。", 64)

        all_candidates = self._merge_quote_candidates(local_candidates, web_candidates)
        # Historical quote rows can contain the same marketplace seller again
        # even after the live provider has collapsed its own response. Apply
        # the seller-level collapse at the final boundary so the comparison
        # table consistently stays one row per marketplace seller.
        all_candidates = MarketplaceSearchLayer._collapse_duplicate_sellers(all_candidates)
        all_candidates = self._prefer_priced_quote_candidates(all_candidates)

        # A marketplace fast path has already passed the deterministic
        # product-level gate above and carries explicit API price evidence.
        # Avoid a second LLM round-trip here: it adds latency without adding
        # material signal for structured product offers. Web/page fallbacks
        # keep the LLM review because their snippets are much noisier.
        if marketplace_priced >= marketplace_minimum:
            all_candidates = [
                candidate
                for candidate in all_candidates
                if self._is_relevant_quote_item(candidate, query, intent)
            ]
            if progress:
                progress("web", f"市场 API 候选已通过产品匹配校验，跳过额外 LLM 复核并保留 {len(all_candidates)} 条。", 78)
        else:
            if progress and len(all_candidates) > 0:
                progress("web", f"正在用 LLM 对 {len(all_candidates)} 条候选做精准产品相关性判断（替代关键词匹配）...", 72)
            try:
                all_candidates = await asyncio.wait_for(
                    self._llm_filter_relevant_quotes(
                        query,
                        all_candidates,
                        intent,
                        german_search_terms=german_phrase,
                    ),
                    timeout=self._quote_llm_filter_timeout_seconds(),
                )
            except asyncio.TimeoutError:
                # Deterministic product gates already ran before this optional
                # semantic review.  Returning them is safer than losing a
                # valid comparison table solely because the LLM was slow.
                all_candidates = [
                    candidate
                    for candidate in all_candidates
                    if self._is_relevant_quote_item(candidate, query, intent)
                ]
                if progress:
                    progress("web", "语义复核超时，已按产品型号/品类的确定性规则保留有效候选。", 78)
            if progress:
                progress("web", f"LLM 相关性过滤完成：保留 {len(all_candidates)} 条真正匹配「{query}」的候选商品。", 78)

        ranked = await self.ranker.rank_quotes(
            query,
            all_candidates,
            min_price=min_price,
            max_price=effective_max_price,
            max_delivery_days=max_delivery_days,
            weights=weights,
            criteria=criteria,
        )

        # Fallback: if ranker eliminated everything, return LLM-filtered candidates unsorted
        if len(ranked) == 0 and len(all_candidates) > 0:
            ranked = sorted(all_candidates, key=lambda c: c.get("unitPriceEur") or 999999)[:20]
            if progress:
                progress("rank", f"硬筛选后无候选，已降级为价格排序展示前 {len(ranked)} 条以保持决策表不为空。", 82)
        
        local_ranked = sum(1 for item in ranked if item.get("source") == "database")
        web_ranked = sum(1 for item in ranked if item.get("source") == "web")
        if progress and len(ranked) > 0:
            local_has_price = sum(1 for item in ranked if item.get("source") == "database" and item.get("unitPriceEur") is not None)
            web_has_price = sum(1 for item in ranked if item.get("source") == "web" and item.get("unitPriceEur") is not None)
            progress("rank", f"已根据预算、交付、评价和权重偏好筛选出 {len(ranked)} 条候选（本地 {local_ranked} 条其中 {local_has_price} 有价格，网络 {web_ranked} 条其中 {web_has_price} 有价格），正在生成推荐排序...", 82)

        top = ranked[0].get("vendor") if ranked else "未找到匹配标准品"
        if progress:
            progress("rank", f"标准品比价完成！共 {len(ranked)} 条候选，当前推荐：{top}。", 95)

        intent_payload = intent.model_dump()
        intent_payload["appliedCriteria"] = criteria or []
        return {"intent": intent_payload, "results": ranked}

    async def _search_web_quotes(self, query: str, intent, max_results: int = 8, progress=None, pre_translated_phrase: str = "") -> list[dict]:
        """Search the public web for quote/product candidates and extract real prices.

        Uses targeted site-specific queries for German office-supply shops where
        prices are commonly listed. Runs searches in parallel batches of 3 to cut
        total network wait time by 60-70%.

        pre_translated_phrase: if provided (from caller's LLM translation), use it
        directly instead of re-translating — avoids duplicate LLM calls.
        """
        if pre_translated_phrase:
            product_phrase = pre_translated_phrase
        else:
            product_phrase = self._quote_search_product_phrase(query, intent)
            # For unknown product categories, use LLM to translate into German search terms
            groups = self._quote_required_term_groups(query, intent)
            if not groups and self.llm:
                llm_kw = await self._llm_search_keywords_async(query)
                if llm_kw:
                    product_phrase = llm_kw
                    if progress:
                        progress("web", f"LLM 将需求翻译为德语搜索词：「{product_phrase}」。", 51)
        candidates: list[dict] = []
        market_label = self._quote_market_label(getattr(intent, "country", None))
        if progress:
            progress(
                "web",
                f"Agent 将本次需求转成可搜索的商品短语：「{product_phrase}」，准备优先查目标市场「{market_label}」的商品页和价格片段。",
                52,
            )

        # Parallel helper
        async def _search_one(q: str):
            try:
                return q, await self.quote_search_provider.search(q, max_results=5)
            except Exception:
                return q, []

        # Phase 1: use German retailers only for Germany/default requests.
        # Other target markets receive country-qualified generic queries, so a
        # selection such as Poland cannot silently become a German web search.
        site_queries = self._quote_site_specific_queries(product_phrase, getattr(intent, "country", None))
        if progress:
            progress("web", f"并行启动 {len(site_queries)} 条网站搜索 + 价格搜索，每批 3 条并发，大幅缩短等待时间。", 53)

        q_idx = 0
        for batch_start in range(0, len(site_queries), 3):
            if q_idx >= 5 and sum(1 for item in candidates if item.get("unitPriceEur") is not None) >= 2:
                break
            batch = site_queries[batch_start:batch_start + 3]
            batch_results = await asyncio.gather(*(_search_one(q) for q in batch))
            for site_query, results in batch_results:
                if not results and not site_query.startswith("site:"):
                    continue
                if progress:
                    progress(
                        "web",
                        f"报价搜索 [{q_idx + 1}/{len(site_queries)}]：{site_query[:60]} -> {len(results)} 条。",
                        53 + int(q_idx * 1.2),
                    )
                more = await self._web_quote_candidates_from_results(results, intent, query, offset=len(candidates), progress=progress)
                candidates = self._merge_quote_candidates(candidates, more)
                q_idx += 1
                if sum(1 for item in candidates if item.get("unitPriceEur") is not None) >= 3:
                    break
            if sum(1 for item in candidates if item.get("unitPriceEur") is not None) >= 3:
                if progress:
                    progress("web", f"已收集足够带价候选 ({sum(1 for item in candidates if item.get('unitPriceEur') is not None)} 条)，提前结束搜索。", 63)
                break

        # Phase 2: extra price-oriented searches if still not enough
        if sum(1 for item in candidates if item.get("unitPriceEur") is not None) < 3:
            extra_queries = self._quote_price_search_queries(query, intent)
            seen_urls = {url for item in candidates for url in item.get("sourceUrls", [])}
            if progress:
                progress("web", f"可用价格还不够，Agent 正在追加目标市场「{market_label}」的价格搜索词来补价。", 63)
            extra_batch = extra_queries[:2]
            extra_results_list = await asyncio.gather(*(_search_one(q) for q in extra_batch))
            for extra_query, extra_results in extra_results_list:
                fresh = [r for r in extra_results if r.url not in seen_urls]
                if progress:
                    progress("web", f"追加价格搜索：{extra_query[:60]} -> 新URL {len(fresh)} 条。", 69)
                more = await self._web_quote_candidates_from_results(fresh, intent, query, offset=len(candidates), progress=progress)
                candidates = self._merge_quote_candidates(candidates, more)
                seen_urls.update(url for item in more for url in item.get("sourceUrls", []))
                if sum(1 for item in candidates if item.get("unitPriceEur") is not None) >= 3:
                    break
        return candidates
    @classmethod
    def _quote_site_specific_queries(cls, product_phrase: str, country: str | None = None) -> list[str]:
        """Return country-correct price-search queries for the selected market."""
        if not cls._should_search_idealo_for_country(country):
            market_label = cls._quote_market_label(country)
            return [
                f"{product_phrase} price {market_label}",
                f"{product_phrase} buy online {market_label}",
                f"{product_phrase} supplier price {market_label}",
                f"{product_phrase} EUR price {market_label}",
            ]

        # Detect if this is likely non-office (appliance, electronics, furniture)
        non_office_signals = ['kaffee', 'maschine', 'vollautomat', 'küche', 'herd', 
                              'kühlschrank', 'wasch', 'trockner', 'fernseher', 'staubsauger',
                              'möbel', 'stuhl', 'tisch', 'lampe', 'leuchte',
                              'iphone', 'smartphone', 'handy', 'telefon', 'laptop', 'notebook',
                              'thinkpad', 'elitebook', 'monitor', 'bildschirm', 'display',
                              'dock', 'docking', 'dockingstation', 'thunderbolt']
        is_non_office = any(s in product_phrase.lower() for s in non_office_signals)
        
        queries = [
            f"{product_phrase} kaufen Preis EUR",
            f"{product_phrase} online shop Deutschland Preis",
            f"{product_phrase} günstig bestellen",
        ]
        lower_phrase = product_phrase.lower()
        industrial_signals = ("festo", "steckanschluss", "anschluss", "pneumatik", "qs", "verschraubung")
        if any(signal in lower_phrase for signal in industrial_signals):
            queries = [
                f"{product_phrase} festo.com kaufen",
                f"{product_phrase} automation24.de Preis",
                f"{product_phrase} rs-online.com bestellen",
                f"{product_phrase} conrad.de kaufen",
                f"{product_phrase} voelkner.de Preis",
                f"{product_phrase} kaufen Preis EUR",
            ]
        elif is_non_office:
            # Target appliance/electronics/general retailers
            queries = [
                f"{product_phrase} kaufen Preis EUR",
                f"{product_phrase} online shop Deutschland",
                f"{product_phrase} idealo.de Preis",
                f"{product_phrase} notebooksbilliger.de kaufen",
                f"{product_phrase} cyberport.de Preis",
                f"{product_phrase} alternate.de bestellen",
                f"{product_phrase} mediamarkt.de Preis",
                f"{product_phrase} saturn.de kaufen",
                f"{product_phrase} amazon.de Preis EUR",
            ]
        else:
            queries = [
                f"{product_phrase} idealo.de Preis",
                f"{product_phrase} bueromarkt-ag.de kaufen",
                f"{product_phrase} schaefer-shop.de Preis",
                f"{product_phrase} viking.de bestellen",
                f"{product_phrase} amazon.de Preis EUR",
                f"{product_phrase} kaufen Preis EUR",
                f"{product_phrase} günstig bestellen",
            ]
        return queries

    # Track which listing URLs have been parsed to avoid redundant fetches
    _parsed_listing_urls: set[str] = set()

    async def _web_quote_candidates_from_results(
        self,
        results: list[SearchResult],
        intent,
        query: str,
        offset: int = 0,
        progress=None,
    ) -> list[dict]:
        candidates: list[dict] = []
        for idx, result in enumerate(results[:3]):
            host = self._hostname(result.url or "")
            # Deduplicate listing page parses
            listing_products: list[dict] = []
            url_key = (result.url or "").split("?")[0].rstrip("/")
            if url_key not in self._parsed_listing_urls:
                try:
                    listing_products = await self.quote_page_fetcher.fetch_products_from_listing(result.url)
                    if listing_products:
                        self._parsed_listing_urls.add(url_key)
                except Exception:
                    listing_products = []
            
            if listing_products:
                if progress:
                    progress("web", f"从商品列表页解析出 {len(listing_products)} 款单品：{host}。", 67)
                for lp_idx, prod in enumerate(listing_products):
                    vendor = self._vendor_from_host(host)
                    candidates.append({
                        "id": f"web-quote-{offset}-{idx}-{lp_idx}",
                        "vendor": vendor,
                        "platform": host,
                        "product": prod.get("product", result.title),
                        "category": intent.category or "web",
                        "description": "",
                        "matchScore": 76 if prod.get("unitPriceEur") else 58,
                        "unitPriceEur": prod.get("unitPriceEur"),
                        "unitLabel": prod.get("unitLabel", "需人工核价"),
                        "deliveryDays": prod.get("deliveryDays"),
                        "deliveryLabel": prod.get("deliveryLabel", "需确认交期"),
                        "paymentTerm": "prepayment",
                        "paymentLabel": "需确认付款方式",
                        "deliveryMethod": "需确认配送方式",
                        "rating": prod.get("rating", 0),
                        "reviews": prod.get("reviews", 0),
                        "source": "web",
                        "sourceDetail": "listing",
                        "sourceUrls": prod.get("sourceUrls", [result.url])[1:2] or prod.get("sourceUrls", [result.url])[:1],
                        "evidenceSnippets": prod.get("evidenceSnippets", []),
                        "priceConfidence": "extracted" if prod.get("unitPriceEur") else "unknown",
                    })
            else:
                candidate = await self._web_quote_candidate_from_result(result, intent, offset + idx, query, progress=progress)
                if candidate:
                    candidates.append(candidate)
        return candidates

    async def _web_quote_candidate_from_result(self, result: SearchResult, intent, idx: int, query: str, progress=None) -> dict | None:
        url = (result.url or "").strip()
        title = (result.title or "").strip()
        snippet = (result.snippet or "").strip()
        if not url or not title:
            return None
        host = self._hostname(url)
        if not host or self._is_blocked_quote_domain(host):
            return None
        text = f"{title} {snippet}"
        if self._is_quote_noise_result(text, url) or self._is_non_product_quote_page(title, snippet, url, intent):
            return None
        price = self._extract_eur_price(text)
        evidence_text = ""
        source_urls = [url]
        if price is not None and progress:
            progress("web", f"从搜索摘要直接抽到价格：{title[:60]} → € {price:.2f}。", 66)
        if price is None:
            try:
                if progress:
                    progress("web", f"摘要没有明确价格，正在打开商品页核价：{host} / {title[:60]}", 66)
                page = await self.quote_page_fetcher.fetch_page(url)
                if page.text:
                    evidence_text = page.text[:6000]
                    source_urls = [page.url or url]
                    price = self._extract_eur_price(f"{text} {evidence_text}")
                    if price is not None and progress:
                        progress("web", f"已从商品页结构化字段/正文抽到价格：{title[:60]} → € {price:.2f}。", 67)
                    elif progress:
                        progress("web", f"已打开商品页但没有可靠欧元价格：{title[:60]}，该候选会被降权或过滤。", 67)
                elif progress:
                    progress("web", f"商品页未返回可读正文，可能被验证码/反爬拦截：{host}。", 67)
            except Exception:
                evidence_text = ""
        # An LLM cannot serve as price evidence.  Keep this optional legacy
        # assist disabled by default, and never let a synchronous HTTP request
        # block FastAPI's event loop while a comparison job is running.
        if (
            price is None
            and evidence_text
            and os.getenv("ENABLE_LLM_PRICE_EXTRACTION", "").strip().casefold() in {"1", "true", "yes"}
            and os.getenv("OPENAI_API_KEY")
        ):
            try:
                import httpx
                dk_url = self._deepseek_chat_completions_url()
                headers = {
                    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                    "messages": [
                        {"role": "user", "content": f"Product: {title}.\\nPage text: {evidence_text[:2000]}.\\n\\nWhat is the unit price in EUR? Reply ONLY with the number, e.g. 12.99"}
                    ],
                    "temperature": 0,
                }
                timeout = self._llm_price_extraction_timeout_seconds()
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(dk_url, json=payload, headers=headers)
                if resp.status_code == 200:
                    raw = resp.json()["choices"][0]["message"]["content"].strip()
                    price = self._extract_eur_price(raw)
                    if price and progress:
                        progress("web", f"DeepSeek 提取到价格：{title[:30]} → €{price:.2f}", 67)
            except Exception:
                pass
        if not self._is_relevant_quote_result(f"{text} {evidence_text}", query, intent, price_found=price is not None):
            return None
        vendor = self._vendor_from_host(host)
        category = intent.category or "web"
        return {
            "id": f"web-quote-{idx}-{abs(hash(url)) % 10_000_000}",
            "vendor": vendor,
            "platform": host,
            "product": title,
            "category": category,
            "description": snippet,
            "matchScore": 76 if price is not None else 58,
            "unitPriceEur": price,
            "unitLabel": f"€ {price:.2f}" if price is not None else "需人工核价",
            "deliveryDays": None,
            "deliveryLabel": "需确认交期",
            "paymentTerm": "prepayment",
            "paymentLabel": "需确认付款方式",
            "deliveryMethod": "需确认配送方式",
            "rating": 0,
            "reviews": 0,
            "source": "web",
            "sourceDetail": "web-search",
            "sourceUrls": source_urls,
            "evidenceSnippets": self._quote_evidence_snippets(evidence_text or snippet),
            "priceConfidence": "extracted" if price is not None else "unknown",
        }

    @staticmethod
    def _deepseek_chat_completions_url() -> str:
        """Build OpenAI-compatible chat completions URL from OPENAI_BASE_URL.

        The Desktop launcher sets OPENAI_BASE_URL=https://api.deepseek.com.
        Posting to that root returns 404; price lookup must call /chat/completions.
        """
        base = (os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"

    @staticmethod
    def _idealo_timeout_seconds() -> float:
        """Bound direct Idealo result-page lookup while keeping an override."""
        try:
            configured = float(os.getenv("IDEALO_TIMEOUT_SECONDS", "20"))
        except (TypeError, ValueError):
            configured = 20.0
        return min(45.0, max(10.0, configured))

    @staticmethod
    def _quote_web_fallback_timeout_seconds(*, has_marketplace_prices: bool) -> float:
        """Cap slow supplementary web research without discarding API offers."""
        default = "22" if has_marketplace_prices else "35"
        try:
            configured = float(os.getenv("QUOTE_WEB_FALLBACK_TIMEOUT_SECONDS", default))
        except (TypeError, ValueError):
            configured = float(default)
        return min(60.0, max(8.0, configured))

    @staticmethod
    def _quote_llm_filter_timeout_seconds() -> float:
        try:
            configured = float(os.getenv("QUOTE_LLM_FILTER_TIMEOUT_SECONDS", "12"))
        except (TypeError, ValueError):
            configured = 12.0
        return min(30.0, max(3.0, configured))

    @staticmethod
    def _llm_price_extraction_timeout_seconds() -> float:
        try:
            configured = float(os.getenv("LLM_PRICE_EXTRACTION_TIMEOUT_SECONDS", "5"))
        except (TypeError, ValueError):
            configured = 5.0
        return min(15.0, max(1.0, configured))

    @staticmethod
    def _marketplace_short_circuit_minimum() -> int:
        """Number of priced API candidates required before skipping page scraping."""
        try:
            configured = int(os.getenv("MARKETPLACE_PRICED_SHORT_CIRCUIT_MIN", "3"))
        except (TypeError, ValueError):
            configured = 3
        return min(8, max(1, configured))

    @staticmethod
    def _quote_market_scope(country: str | None) -> str:
        """Classify a requested market without treating it as Germany by default."""

        raw = str(country or "").strip().casefold()
        if not raw or raw in {"de", "germany", "deutschland"}:
            return "germany"
        if raw in {"eu", "europe", "european union", "欧洲", "欧盟", "欧洲联盟"}:
            return "aggregate"
        return "country"

    @classmethod
    def _should_search_idealo_for_country(cls, country: str | None) -> bool:
        """Idealo is a Germany-only supplement for comparison requests."""

        return cls._quote_market_scope(country) == "germany"

    @classmethod
    def _quote_market_label(cls, country: str | None) -> str:
        if cls._quote_market_scope(country) == "germany":
            return "Germany"
        return str(country or "Europe").strip() or "Europe"

    @classmethod
    def _quote_price_search_queries(cls, query: str, intent) -> list[str]:
        terms = cls._quote_search_product_phrase(query, intent)
        country = getattr(intent, "country", None)
        market_label = cls._quote_market_label(country)
        if not cls._should_search_idealo_for_country(country):
            return [
                f"{terms} price {market_label}",
                f"{terms} buy online {market_label}",
                f"{terms} supplier price {market_label}",
                f"{terms} EUR price {market_label}",
            ]
        return [
            f"{terms} Preis €",
            f"{terms} günstig kaufen",
            f"{terms} online bestellen Preis",
            f"{terms} shop Germany",
        ]

    @classmethod
    def _quote_search_product_phrase(cls, query: str, intent) -> str:
        groups = cls._quote_required_term_groups(query, intent)
        if groups:
            preferred: list[str] = []
            for group in groups:
                for alias in (
                    "a4", "kopierpapier", "druckerpapier", "papier",
                    "steckanschluss", "anschluss", "qs", "sicherheitsschuh", "s3",
                    "spülmittel", "thunderbolt", "dock", "dockingstation",
                    "maus", "tastatur", "taschenrechner", "ordner", "heftklammern",
                    "schere", "klebestift", "edding", "post-it", "tesa", "papierkorb",
                    "aktenvernichter", "drucker", "monitor", "laptop", "iphone", "telefon",
                ):
                    if alias in group:
                        preferred.append(alias)
                        break
                else:
                    preferred.append(sorted(group, key=len)[0])
            phrase = " ".join(preferred)
            query_lower = query.lower()
            if any(term in phrase for term in ("laptop", "notebook")) and "thinkpad" in query_lower and "thinkpad" not in phrase:
                phrase = f"thinkpad {phrase}"
            if "monitor" in phrase:
                size = re.search(r'(\d{2})\s*(?:寸|zoll|inch|")', query_lower)
                if size and "zoll" not in phrase and "inch" not in phrase:
                    phrase = f"{size.group(1)} zoll {phrase}"
            if "dock" in phrase:
                extras = []
                for marker in ("hp", "thunderbolt", "usb-c", "usbc"):
                    if marker in query_lower and marker not in phrase:
                        extras.append("usb-c" if marker == "usbc" else marker)
                if extras:
                    phrase = " ".join([*extras, phrase])
            # Google Shopping needs the requested SKU/model in the query.  A
            # product-family-only query such as "maus" can return a full page
            # of unrelated mice even though a precise model was supplied.
            identifiers = cls._quote_model_identifiers(query)
            brands = cls._quote_brand_terms(query)
            qualifiers = [*brands, *identifiers]
            if qualifiers:
                # Search engines treat ``B100`` and ``b100`` identically, but
                # Python's normal dictionary de-duplication does not.  Keep
                # the buyer's original model spelling once while avoiding a
                # redundant term that can dilute a compact Shopping query.
                words: list[str] = []
                seen_words: set[str] = set()
                for word in [*qualifiers, *phrase.split()]:
                    normalized = word.casefold()
                    if normalized and normalized not in seen_words:
                        words.append(word)
                        seen_words.add(normalized)
                phrase = " ".join(words)
            return phrase
        # No known product group matched — strip constraint words, keep product nouns
        terms = sorted(cls._quote_relevance_terms(query, intent), key=len, reverse=True)
        keyword_str = " ".join(terms[:5])
        stripped = cls._strip_constraint_words(keyword_str)
        return stripped or query[:80]

    async def _llm_search_keywords_async(self, query: str) -> str:
        """Use LLM to extract 2-3 search-friendly German keywords from any query."""
        if not self.llm:
            return ""
        prompt = (
            f"Convert this procurement request into 2-3 German search keywords for German e-commerce sites:\n"
            f"{query[:200]}\n\n"
            f"Return ONLY the keywords separated by spaces. No other text.\n"
            f"Example: 'kaffeemaschine vollautomatisch bohnen'"
        )
        try:
            if hasattr(self.llm, "ainvoke"):
                response = await self.llm.ainvoke(prompt)
            elif hasattr(self.llm, "invoke"):
                import asyncio
                response = await asyncio.to_thread(self.llm.invoke, prompt)
            else:
                return ""
            text = str(getattr(response, "content", response)).strip()[:100]
            import re
            cleaned = re.sub(r'[^a-zA-Z0-9\s\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df]', '', text)
            return cleaned.strip()
        except Exception:
            return ""

    @staticmethod
    def _quote_model_identifiers(query: str) -> list[str]:
        """Return concrete SKU/model tokens that must survive search rewriting."""
        values: list[str] = []
        pattern = r"(?<![A-Za-z0-9])(?=[A-Za-z0-9/-]*[A-Za-z])(?=[A-Za-z0-9/-]*\d)[A-Za-z0-9]+(?:[/-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
        generic_tokens = {"a3", "a4", "a5", "s1", "s2", "s3", "s4", "80g", "75g", "90g"}
        for raw in re.findall(pattern, query or ""):
            value = raw.strip()
            if len(value) >= 3 and value.casefold() not in generic_tokens and value not in values:
                values.append(value)
        return values[:4]

    @staticmethod
    def _quote_brand_terms(query: str) -> list[str]:
        """Keep explicit common brands in marketplace requests when present."""
        text = str(query or "")
        lowered = text.casefold()
        values: list[str] = []
        for match in re.finditer(r"(?:brand|marke|品牌)\s*:\s*([A-Za-z0-9][A-Za-z0-9._-]{1,40})", text, re.IGNORECASE):
            value = match.group(1).strip()
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
        for brand in (
            "logitech", "lenovo", "dell", "hp", "apple", "samsung", "philips",
            "festo", "sika", "teroson", "henkel", "uvex", "tesa", "canon",
        ):
            if re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", lowered):
                if brand not in {item.casefold() for item in values}:
                    values.append(brand)
        return values[:2]

    @staticmethod
    def _strip_constraint_words(text: str) -> str:
        """Remove budget/quantity/constraint noise, keep product-signal words."""
        import re
        # Remove numbers with units
        text = re.sub(r'\d+\s*(台|个|欧元|元|预算|eur|st|stück|blatt)', '', text, flags=re.I)
        # Remove constraint phrases
        for phrase in ['预算', '适用于', '支持', '不限', '以内', '以上', '以下', '预算', '不超过']:
            text = text.replace(phrase, ' ')
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:100]

    @classmethod
    def _is_quote_noise_result(cls, text: str, url: str = "") -> bool:
        lowered = f"{text} {url}".lower()
        noise_markers = (
            "stückpreis rechner", "unit price calculator", "preis je menge", "omnicalculator",
            "briefmarke", "briefversand", "grossbrief", "großbrief",
            "alles für 1€", "alles fuer 1", "katalog", "aktionen",
            "berechnen", "calculator",
            "kopien ", "copy shop", "posterdruck",
            "0,05", "0.05 €",
            "kurs in euro", "live kurs", "crypto", "kryptowährung", "bitpanda",
            "men's and women's", "chinos", "khakis", "clothing", "dockers®",
        )
        return any(marker in lowered for marker in noise_markers)


    @classmethod
    def _term_in_text(cls, term: str, text: str) -> bool:
        term = str(term or "").lower().strip()
        if not term:
            return False
        if re.search(r"[\u4e00-\u9fff]", term):
            return term in text
        # Latin tokens need boundaries so dock != dockers and phone != headphone.
        return re.search(rf"(?<![a-z0-9äöüß]){re.escape(term)}(?![a-z0-9äöüß])", text, re.I) is not None

    @classmethod
    def _any_term_in_text(cls, terms, text: str) -> bool:
        return any(cls._term_in_text(str(term), text) for term in terms)

    @classmethod
    def _is_non_product_quote_page(cls, title: str, snippet: str, url: str, intent=None) -> bool:
        """Reject obvious non-product pages without killing useful shop/category leads.

        Trusted German procurement shops often expose category/listing URLs such as
        "Ordner günstig kaufen" or "Spülmittel 1 Liter". Those are acceptable quote
        leads when the text matches the product gate. We only reject clearly unrelated
        sites/pages (crypto, clothing brands, generic store homepages, broad accessory
        categories).
        """
        host = cls._hostname(url)
        text = f"{title} {snippet} {url}".lower()
        if cls._is_blocked_quote_domain(host):
            return True
        if host == "bing.com" and "aclick" in url.lower():
            return True

        trusted_hosts = (
            "bueromarkt-ag.de", "schaefer-shop.de", "viking.de", "amazon.de",
            "idealo.de", "otto.de", "mediamarkt.de", "saturn.de", "billiger.de",
            "notebooksbilliger.de", "cyberport.de", "alternate.de", "conrad.de", "voelkner.de", "reichelt.de",
            "rs-online.com", "de.rs-online.com", "distrelec.de", "automation24.de",
            "festo.com",
        )
        trusted = any(host == h or host.endswith(f".{h}") for h in trusted_hosts)
        trusted_search_noise = (
            "suchergebnis auf amazon",
            "dockingstationen online kaufen",
            "idealo – die nr. 1",
            "idealo - die nr. 1",
            "die nr. 1 im preisvergleich",
            "amazon.de: traditional",
            "amazon.de: mini",
            "thinkpad-angebote",
            "laptop-angebote",
            "sale/thinkpad",
        )
        if trusted and any(marker in text for marker in trusted_search_noise):
            return True
        if trusted:
            # Let product-level required/negative gates decide for trusted commerce sites.
            return False

        hard_noise = (
            "kurs in euro", "live kurs", "crypto", "kryptowährung", "bitpanda",
            "men's and women's", "chinos", "khakis", "clothing", "dockers®",
            "dock & bay", "handtücher", "towels", "schwimmdock",
        )
        if any(marker in text for marker in hard_noise):
            return True

        broad_page_markers = (
            "suchergebnis", "search results", "all - ", "collections",
            "computertechnik & zubehör", "monitore webcams & zubehör",
            "traditional laptops", "mini iphone",
        )
        if any(marker in text for marker in broad_page_markers):
            return True

        # Generic category/store pages without price and without a concrete product model are weak.
        if re.search(r"\b(all|alle|store|zubehör|accessories)\b", text) and not re.search(r"€|eur|[0-9]+,[0-9]{2}|[a-z]+[- ]?[0-9]", text):
            return True
        return False

    @classmethod
    def _is_blocked_quote_domain(cls, host: str) -> bool:
        host = host.lower()
        return any(host == domain or host.endswith(f".{domain}") for domain in cls.QUOTE_WEB_BLOCKED_DOMAINS)

    @classmethod
    def _is_relevant_quote_result(cls, text: str, query: str, intent, price_found: bool = False) -> bool:
        haystack = text.lower()
        terms = cls._quote_relevance_terms(query, intent)
        if not terms:
            return price_found

        required_groups = cls._quote_required_term_groups(query, intent)
        if required_groups:
            if cls._quote_negative_term_hit(haystack, query, intent):
                return False
            return all(cls._any_term_in_text(group, haystack) for group in required_groups)

        # No known product group — with German keywords now injected into intent,
        # we can be stricter: priced results must match at least one term (>=2 chars).
        # This prevents irrelevant local DB items (e.g., A4 paper) from passing
        # when the user searches for "人体工学椅".
        if price_found:
            for term in terms:
                if len(term) >= 2 and cls._term_in_text(term, haystack):
                    return True
            # No term match — likely irrelevant even if priced
            return False

        # For unpriced results, require some keyword overlap
        overlap = sum(1 for term in terms if cls._term_in_text(term, haystack))
        return overlap >= max(1, len(terms) // 3)


    @classmethod
    def _quote_negative_term_hit(cls, haystack: str, query: str, intent=None) -> bool:
        """Reject known sibling/accessory false positives before ranking."""
        text = f"{query} {' '.join(str(k) for k in (getattr(intent, 'keywords', []) or []))}".lower()
        category = getattr(intent, "category", None)
        if category == "paper" or any(marker in text for marker in ("打印纸", "复印纸", "kopierpapier", "druckerpapier")):
            return any(bad in haystack for bad in ("ordner", "etikett", "laminier", "trennstreifen", "folder", "binder", "label", "folie"))
        if category == "laptop" or any(marker in text for marker in ("笔记本", "laptop", "notebook", "thinkpad", "elitebook")):
            return any(bad in haystack for bad in (
                "dock", "docking", "headset", "monitor", "display", "phone", "iphone",
                "laptophülle", "laptoptasche", "laptopständer", "laptop stand", "sleeve", "hülle", "tasche",
                "lüfter", "fan", "cooler", "kühler"
            ))
        if category == "monitor" or any(marker in text for marker in ("显示器", "monitor", "bildschirm", "display")):
            return any(bad in haystack for bad in ("dock", "docking", "headset", "papier", "paper", "monitorarm", "monitor-ständer", "ständer", "webcam", "kamera"))
        if category == "accessory" and any(marker in text for marker in ("dock", "thunderbolt", "扩展坞")):
            if any(bad in haystack for bad in ("bitpanda", "kurs in euro", "dockers", "chinos", "khakis", "clothing", "nintendo", "kamera", "piranha", "handtuch")):
                return True
            return not cls._any_term_in_text(("dock", "docking", "dockingstation", "扩展坞"), haystack)
        if category == "phone" or any(marker in text for marker in ("iphone", "smartphone", "手机", "智能手机")):
            return any(bad in haystack for bad in ("android", "player", "ladegerät", "charger", "adapter", "kabel", "cable", "hülle", "case", "powerbank"))
        if category == "equipment" and any(marker in text for marker in ("接头", "anschluss", "fitting", "qs")):
            if any(bad in haystack for bad in ("steckdose", "steckschlüssel", "ratschen", "verlängerungskabel", "schuko", "usb-steckdose")):
                return True
            return any(bad in haystack for bad in ("schalldämpfer", "drossel", "rückschlagventil", "ventil")) and not any(ok in haystack for ok in ("anschluss", "steck", "qs", "fitting"))
        if category == "safetyShoes" or any(marker in text for marker in ("安全鞋", "sicherheitsschuh", "s3")):
            return "s3" in text and "s1" in haystack and "s3" not in haystack
        if category == "cleaning" and any(marker in text for marker in ("洗洁精", "spülmittel", "dish soap")):
            return any(bad in haystack for bad in ("schwamm", "schwämme", "wc-reiniger", "toilettenpapier", "spülmaschinensalz", "klarspüler")) and "spülmittel" not in haystack
        if category == "office" and any(marker in text for marker in ("文件夹", "ordner", "folder")):
            return any(bad in haystack for bad in ("etikett", "label", "laminier", "folie", "kopierpapier", "druckerpapier"))
        return False

    @classmethod
    def _quote_relevance_terms(cls, query: str, intent) -> set[str]:
        lowered_query = query.lower()
        raw_terms = re.findall(r"[a-zA-Z0-9\u4e00-\u9fffäöüÄÖÜß]{2,}", lowered_query)
        for group in cls._quote_required_term_groups(query, intent):
            raw_terms.extend(group)
        for field in ("category", "country"):
            value = getattr(intent, field, None)
            if value:
                raw_terms.extend(re.findall(r"[a-zA-Z0-9\u4e00-\u9fffäöüÄÖÜß]{2,}", str(value).lower()))
        for keyword in getattr(intent, "keywords", []) or []:
            keyword_lower = str(keyword).lower()
            raw_terms.extend(re.findall(r"[a-zA-Z0-9\u4e00-\u9fffäöüÄÖÜß]{2,}", keyword_lower))
            for group in cls._quote_required_term_groups(str(keyword), intent=None):
                raw_terms.extend(group)
        return {term for term in raw_terms if term not in cls.QUOTE_WEB_STOPWORDS and (len(term) >= 2 or term == "a4")}

    @classmethod
    def _quote_required_term_groups(cls, query: str, intent=None) -> list[set[str]]:
        """Concrete product concepts that must match for quote comparisons.

        This is deliberately product-level rather than category-level: if the user
        asks for mouse, keyboard, calculator, A4 paper, etc., a cheap item from the
        same broad hardware/office category should not be shown.
        """
        pieces = [query or ""]
        if intent is not None:
            pieces.extend(str(keyword) for keyword in getattr(intent, "keywords", []) or [])
        text = " ".join(pieces).lower()
        groups: list[set[str]] = []

        def add_if(markers: tuple[str, ...], aliases: set[str]) -> None:
            if any(marker in text for marker in markers):
                if not any(aliases == existing for existing in groups):
                    groups.append(aliases)

        add_if(("a4",), {"a4"})
        add_if(("a4纸", "a4紙", "打印纸", "复印纸", "paper", "papier", "kopierpapier", "druckerpapier"), {"纸", "紙", "paper", "papier", "kopierpapier", "druckerpapier"})
        add_if(("鼠标", "滑鼠", "mouse", "maus"), {"鼠标", "滑鼠", "mouse", "maus"})
        add_if(("键盘", "鍵盤", "keyboard", "tastatur"), {"键盘", "鍵盤", "keyboard", "tastatur"})
        add_if(("计算器", "計算器", "calculator", "taschenrechner"), {"计算器", "計算器", "calculator", "taschenrechner"})
        add_if(("文件夹", "資料夾", "folder", "ordner", "schnellhefter"), {"文件夹", "資料夾", "folder", "ordner", "schnellhefter"})
        add_if(("订书钉", "訂書釘", "heftklammer", "staple"), {"订书钉", "訂書釘", "heftklammer", "heftklammern", "staple", "staples"})
        add_if(("剪刀", "scissors", "schere"), {"剪刀", "scissors", "schere"})
        add_if(("胶水", "膠水", "glue", "kleber", "klebestift"), {"胶水", "膠水", "glue", "kleber", "klebestift"})
        add_if(("马克笔", "麥克筆", "记号笔", "marker", "edding"), {"马克笔", "麥克筆", "记号笔", "marker", "whiteboard-marker", "permanentmarker", "edding"})
        add_if(("便利贴", "便签", "便條", "post-it", "haftnotiz"), {"便利贴", "便签", "便條", "post-it", "postit", "haftnotiz", "haftnotizen"})
        add_if(("胶带", "膠帶", "tape", "klebefilm", "tesa"), {"胶带", "膠帶", "tape", "klebefilm", "tesa"})
        add_if(("垃圾桶", "纸篓", "papierkorb", "bin"), {"垃圾桶", "纸篓", "papierkorb", "bin", "waste bin"})
        add_if(("碎纸机", "碎紙機", "shredder", "aktenvernichter", "schredder", "reißwolf", "reibwolf"), {"碎纸机", "碎紙機", "shredder", "aktenvernichter", "schredder", "reißwolf", "reibwolf", "paper shredder"})
        add_if(("打印机", "印表機", "drucker", "printer", "multifunktionsdrucker"), {"打印机", "印表機", "drucker", "printer", "multifunktionsdrucker", "laserdrucker", "tintenstrahldrucker"})
        add_if(("显示器", "顯示器", "monitor", "bildschirm", "display"), {"显示器", "顯示器", "monitor", "bildschirm", "display", "screen"})
        add_if(("笔记本", "筆記本", "laptop", "notebook", "thinkpad"), {"笔记本", "筆記本", "laptop", "notebook", "thinkpad", "arbeitslaptop"})
        add_if(("电话", "電話", "telefon", "phone", "handy"), {"电话", "電話", "telefon", "phone", "handy", "schnurlostelefon", "voip-telefon"})
        add_if(("iphone", "smartphone", "手机", "智能手机"), {"iphone", "smartphone", "handy", "phone", "手机", "galaxy"})
        add_if(("dock", "docking", "扩展坞", "dockingstation"), {"dock", "docking", "dockingstation", "扩展坞"})
        add_if(("耳机", "headset", "kopfhörer", "earpod"), {"耳机", "headset", "kopfhörer", "earpod", "headphone"})
        add_if(("安全鞋", "sicherheitsschuh", "sicherheitsschuhe", "schutzschuh"), {"安全鞋", "sicherheitsschuh", "sicherheitsschuhe", "schutzschuh"})
        add_if(("s3",), {"s3"})
        add_if(("洗洁精", "spülmittel", "geschirrspülmittel", "dish soap"), {"洗洁精", "spülmittel", "geschirrspülmittel", "dish soap"})
        add_if(("气动接头", "steckanschluss", "anschluss", "fitting", "verschraubung"), {"steckanschluss", "anschluss", "verschraubung", "fitting", "kupplung", "接头"})
        add_if(("festo",), {"festo"})
        add_if(("qs",), {"qs"})
        add_if(("工作站", "workstation", "zbook", "desktop"), {"工作站", "workstation", "zbook", "desktop", "arbeitsstation"})
        # A model/SKU is an explicit buyer constraint, not merely a ranking
        # preference.  Require it alongside the product-family group so a
        # search for "Logitech B100 mouse" cannot recommend an unrelated
        # gaming mouse just because both are mice.
        for identifier in cls._quote_model_identifiers(query):
            aliases = {identifier.casefold()}
            if not any(aliases == existing for existing in groups):
                groups.append(aliases)
        return groups

    @classmethod
    def _is_relevant_quote_item(cls, quote: dict, query: str, intent) -> bool:
        text = " ".join(
            str(value)
            for value in [
                quote.get("vendor"),
                quote.get("platform"),
                quote.get("product"),
                quote.get("description"),
            ]
            if value
        )
        url = (quote.get("sourceUrls") or [""])[0]
        # Marketplace APIs return an individual product record. Its URL can be
        # on a domain intentionally blocked from generic crawler results, but it
        # still needs ordinary product relevance validation.
        is_marketplace_item = str(quote.get("sourceDetail") or "").startswith("marketplace:")
        if not is_marketplace_item and cls._is_non_product_quote_page(
            str(quote.get("product") or ""),
            str(quote.get("description") or ""),
            url,
            intent,
        ):
            return False
        return cls._is_relevant_quote_result(text, query, intent, price_found=quote.get("unitPriceEur") is not None)

    @staticmethod
    def _quote_has_decision_vendor(quote: dict) -> bool:
        """Do not rank orphaned local quotes as if they were suppliers."""
        vendor = str(quote.get("vendor") or "").strip().casefold()
        if vendor in {"", "unknown", "n/a", "none", "null"}:
            return False
        return True


    async def _llm_filter_relevant_quotes(self, query: str, candidates: list[dict], intent, german_search_terms: str = "") -> list[dict]:
        """LLM judges which candidates actually match the user's specific product need.

        Products from parsed listing pages pass through directly — they were found
        via site-specific targeted searches and already priced. We keep ALL listing
        products (not just keyword-matched ones) because the search query already
        targeted the right product category.

        LLM only filters candidates from other sources (search snippets, single pages).
        When german_search_terms is provided, it's included in the LLM prompt to help
        match German product names against the user's Chinese query.
        """
        if not candidates:
            return []
        
        listing_all = [c for c in candidates if c.get('sourceDetail') == 'listing']
        other_products = [c for c in candidates if c.get('sourceDetail') != 'listing']
        
        # Listing pages can still contain sibling accessories (e.g. laptop sleeves,
        # chargers, monitor arms). Keep only products passing the same product-level
        # relevance gate; this is safer than trusting the listing page wholesale.
        listing_products = [c for c in listing_all if self._is_relevant_quote_item(c, query, intent)]
        
        if not other_products:
            return listing_products
        
        if not self.llm:
            return listing_products + [c for c in other_products if self._is_relevant_quote_item(c, query, intent)]

        # Extract product keywords from query for the LLM prompt
        product_keywords = ' '.join([str(k) for k in getattr(intent, 'keywords', []) or []]) or query[:120]
        
        # Include German search terms so LLM can match German product names
        context_note = ""
        if german_search_terms:
            context_note = f"\nNote: products were searched with German keywords: \"{german_search_terms}\". "
            context_note += "A German product name matching these German keywords IS relevant even if its name doesn't contain Chinese words."
        
        # Build summary for LLM — cap at 20 to avoid 2-minute inference time
        items = []
        for i, c in enumerate(other_products[:20]):
            items.append(
                f"[{i}] {c.get('product','')} | vendor={c.get('vendor','')} "
                f"| platform={c.get('platform','')} | price=€{c.get('unitPriceEur','?')}"
            )

        prompt = (
            f"User needs: {product_keywords}{context_note}\n\n"
            f"Candidates to check:\n" + "\n".join(items) + "\n\n"
            "For each candidate, decide if it IS the type of product the user needs. "
            "MATCH if it's the same product (e.g., shredder=shredder, paper=paper). "
            "REJECT if it's a different product (e.g., shredder oil is NOT a shredder).\n\n"
            "Return a JSON array of indices (the numbers in brackets) that MATCH. "
            "Example: [0, 3, 5]\n"
            "Return ONLY the JSON array, nothing else."
        )

        try:
            if hasattr(self.llm, "ainvoke"):
                response = await self.llm.ainvoke(prompt)
            elif hasattr(self.llm, "invoke"):
                import asyncio
                response = await asyncio.to_thread(self.llm.invoke, prompt)
            else:
                return listing_products + [c for c in other_products if self._is_relevant_quote_item(c, query, intent)]

            content_text = str(getattr(response, "content", response))
            import re, json
            match = re.search(r'\[.*?\]', content_text, re.S)
            if not match:
                return listing_products + [c for c in other_products if self._is_relevant_quote_item(c, query, intent)]
            indices = json.loads(match.group(0))
            kept = [
                other_products[i]
                for i in indices
                if isinstance(i, int)
                and 0 <= i < len(other_products)
                and self._is_relevant_quote_item(other_products[i], query, intent)
            ]
            return listing_products + kept
        except Exception:
            return listing_products + [c for c in other_products if self._is_relevant_quote_item(c, query, intent)]

    @classmethod
    def _extract_eur_price(cls, text: str) -> float | None:
        patterns = [
            r'"(?:price|lowPrice)"\s*:\s*"?([0-9][0-9.,]*(?:[.,][0-9]{2})?)"?[^{}]{0,160}"priceCurrency"\s*:\s*"?EUR"?',
            r'"priceCurrency"\s*:\s*"?EUR"?[^{}]{0,160}"(?:price|lowPrice)"\s*:\s*"?([0-9][0-9.,]*(?:[.,][0-9]{2})?)"?',
            r'(?:data-price|content|amount)\s*=\s*["\']([0-9][0-9.,]*(?:[.,][0-9]{2})?)["\'][^<>]{0,120}(?:EUR|€)',
            r'(?:EUR|Euro)\s*([0-9][0-9.,]*(?:[.,][0-9]{2})?)',
            r"€\s*([0-9][0-9.,]*(?:[.,][0-9]{2})?)",
            r"€\s*([0-9][0-9.,]*)\s*(?:[-–]|,–)",
            r"([0-9][0-9.,]*(?:[.,][0-9]{2})?)\s*(?:EUR|Euro|€)",
            r"([0-9][0-9.,]*)\s*(?:[-–]|,–)\s*(?:EUR|Euro|€)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = cls._parse_price_number(match.group(1))
            if value is not None and 0.08 < value < 100000:
                return value
        return None

    @staticmethod
    def _parse_price_number(raw: str) -> float | None:
        value = raw.strip().strip(".,;:").replace(" ", "")
        if not value:
            return None
        dot = value.rfind(".")
        comma = value.rfind(",")
        if dot != -1 and comma != -1:
            # Last separator is decimal, the other is thousands: 1.234,56 / 1,234.56
            if dot > comma:
                value = value.replace(",", "")
            else:
                value = value.replace(".", "").replace(",", ".")
        elif comma != -1:
            parts = value.split(",")
            if len(parts[-1]) == 2:
                value = "".join(parts[:-1]).replace(",", "") + "." + parts[-1]
            else:
                value = value.replace(",", "")
        elif dot != -1:
            parts = value.split(".")
            if len(parts) > 2 and len(parts[-1]) == 2:
                value = "".join(parts[:-1]) + "." + parts[-1]
            elif len(parts) > 2:
                value = "".join(parts)
            elif len(parts) == 2 and len(parts[-1]) == 3:
                # German prices often use a single dot as the thousands
                # separator, e.g. € 1.234 for € 1,234.
                value = "".join(parts)
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return (urlparse(url).netloc or "").replace("www.", "")
        except Exception:
            return ""

    @staticmethod
    def _vendor_from_host(host: str) -> str:
        base = host.split(":", 1)[0].split(".")[0]
        return base.replace("-", " ").replace("_", " ").title() or host

    @staticmethod
    def _quote_identity_key(item: dict) -> str:
        """Build a stable offer key that removes repeated DB imports.

        Quote IDs and tracking URLs are often unique per crawl even when the
        vendor, product and platform are identical.  Those rows add noise to
        the comparison table without adding a new purchasing option.  Web
        listing rows keep their item IDs because each card can be a distinct
        offer; database rows use the normalized commercial identity instead.
        """
        source = str(item.get("source") or "web").casefold()
        detail = str(item.get("sourceDetail") or "").casefold()
        if source == "database" or detail == "database":
            def _norm(value: object) -> str:
                return re.sub(r"[^a-z0-9äöüß]+", "", str(value or "").casefold())

            vendor = _norm(item.get("vendor"))
            product = _norm(item.get("product"))
            platform = _norm(item.get("platform"))
            if vendor or product or platform:
                return f"database:{vendor}|{product}|{platform}"
        if detail == "listing":
            return f"listing:{item.get('id') or ''}"
        urls = item.get("sourceUrls") or []
        if urls:
            return f"web-url:{str(urls[0]).split('#', 1)[0]}"
        return f"web-id:{item.get('id') or item.get('vendor') or len(str(item))}"

    @classmethod
    def _merge_quote_candidates(cls, local_candidates: list[dict], web_candidates: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for item in [*local_candidates, *web_candidates]:
            key = cls._quote_identity_key(item)
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
                continue
            existing_has_price = existing.get("unitPriceEur") is not None
            item_has_price = item.get("unitPriceEur") is not None
            if item_has_price and not existing_has_price:
                merged[key] = item
            elif item_has_price == existing_has_price and item.get("matchScore", 0) > existing.get("matchScore", 0):
                merged[key] = item
        return list(merged.values())

    @staticmethod
    def _prefer_priced_quote_candidates(candidates: list[dict]) -> list[dict]:
        """Hide manual-price rows when at least one published offer exists."""
        priced = [item for item in candidates if item.get("unitPriceEur") is not None]
        if priced:
            return priced
        return candidates

    @staticmethod
    def _quote_evidence_snippets(text: str) -> list[str]:
        lines = [line.strip() for line in re.split(r"[\n。]", text or "") if len(line.strip()) > 20]
        return lines[:3]

    @staticmethod
    def _create_llm():
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from langchain_openai import ChatOpenAI

            kwargs = {
                "model": os.getenv("LLM_MODEL", "gpt-4o"),
                "temperature": 0,
            }
            base_url = os.getenv("OPENAI_BASE_URL")
            if base_url:
                kwargs["base_url"] = base_url
            return ChatOpenAI(**kwargs)
        except Exception:
            return None

    @staticmethod
    def _create_chroma_collection():
        try:
            import chromadb

            persist_dir = os.getenv("CHROMA_PERSIST_DIR", str(BASE_DIR / "chroma_data"))
            client = chromadb.PersistentClient(path=persist_dir)
            return client.get_or_create_collection(name="suppliers", embedding_function=None)
        except Exception:
            return None

    @staticmethod
    def _delivery_time_to_days(delivery_time: Optional[str]) -> Optional[int]:
        mapping = {
            "within3": 3,
            "within7": 7,
            "unlimited": None,
            None: None,
        }
        return mapping.get(delivery_time)

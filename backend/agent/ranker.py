from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field


class RankedItem(BaseModel):
    id: str
    matchScore: int = Field(ge=0, le=100)
    reason: str


class RankingResponse(BaseModel):
    results: list[RankedItem] = Field(default_factory=list)


class LLMRanker:
    def __init__(self, llm):
        from pathlib import Path
        self.llm = llm
        self.optimized_prompt_path = Path(__file__).resolve().parents[1] / "data" / "optimized_prompt.txt"

    async def rank_suppliers(
        self,
        query: str,
        candidates: list[dict],
        criteria: list[dict] | None = None,
    ) -> list[dict]:
        """Rank suppliers by relevance plus optional user-defined criteria.

        Criteria are intentionally open-ended rather than a fixed price / lead
        time / payment triad.  A supplier record can provide a ``criteriaScores``
        map for company-specific dimensions, while commonly used dimensions
        (certifications, environmental standards, capacity, incumbent status,
        etc.) have conservative evidence-based fallbacks.
        """
        if not candidates:
            return []
        normalized_criteria = self._normalize_supplier_criteria(criteria)
        llm_scores = await self._rank_with_llm(
            query,
            candidates,
            "supplier",
            weights={"criteria": normalized_criteria} if normalized_criteria else None,
        )
        ranked = []
        for candidate in candidates:
            scored = dict(candidate)
            llm_score = llm_scores.get(candidate.get("id"))
            if llm_score:
                scored["matchScore"] = llm_score.matchScore
                scored["reason"] = llm_score.reason
            else:
                scored["matchScore"] = self._heuristic_score(query, candidate)
                scored["reason"] = self._supplier_reason(query, candidate)
            scored["matchScore"] = self._apply_repurchase_bonus(scored)
            if normalized_criteria:
                criteria_score, evidence_scores = self._supplier_weighted_criteria_score(
                    scored, normalized_criteria
                )
                # Relevance remains the dominant guardrail, while the user can
                # materially shift a close decision with their own dimensions.
                scored["matchScore"] = max(
                    0, min(100, round(scored["matchScore"] * 0.65 + criteria_score * 0.35))
                )
                scored["criteriaScores"] = evidence_scores
                scored["weightedCriteriaScore"] = criteria_score
                scored["appliedCriteria"] = normalized_criteria
            ranked.append(scored)
        return sorted(ranked, key=lambda item: item.get("matchScore", 0), reverse=True)

    async def rank_quotes(
        self,
        query: str,
        candidates: list[dict],
        min_price: float = None,
        max_price: float = None,
        max_delivery_days: int = None,
        weights: dict | None = None,
        criteria: list[dict] | None = None,
    ) -> list[dict]:
        """Score quotes with core weights and optional custom decision criteria."""
        filtered = []
        for candidate in candidates:
            unit_price = candidate.get("unitPriceEur")
            delivery_days = candidate.get("deliveryDays")
            if min_price is not None and unit_price is not None and unit_price < min_price:
                continue
            if max_price is not None and unit_price is not None and unit_price > max_price:
                continue
            if max_delivery_days is not None and delivery_days is not None and delivery_days > max_delivery_days:
                continue
            filtered.append(candidate)

        if not filtered:
            return []

        normalized_weights = self._normalize_quote_weights(weights)
        normalized_criteria = self._normalize_supplier_criteria(criteria)
        quote_stats = self._quote_normalization_stats(filtered)
        llm_scores = await self._rank_with_llm(
            query,
            filtered,
            "quote",
            weights={"core": normalized_weights, "criteria": normalized_criteria} if normalized_criteria else normalized_weights,
        )
        ranked = []
        for candidate in filtered:
            scored = dict(candidate)
            weighted_score = self._quote_score(query, candidate, max_delivery_days, normalized_weights, quote_stats)
            llm_score = llm_scores.get(candidate.get("id"))
            if llm_score:
                # Keep the LLM's semantic judgement, but make the slider weights materially affect ranking.
                scored["matchScore"] = max(0, min(100, round(llm_score.matchScore * 0.55 + weighted_score * 0.45)))
                scored["reason"] = f"{llm_score.reason} Weighted by price/delivery/rating preferences."
            else:
                scored["matchScore"] = weighted_score
                scored["reason"] = self._quote_reason(candidate)
            if normalized_criteria:
                criteria_score, evidence_scores = self._supplier_weighted_criteria_score(
                    scored, normalized_criteria
                )
                scored["matchScore"] = max(
                    0, min(100, round(scored["matchScore"] * 0.65 + criteria_score * 0.35))
                )
                scored["criteriaScores"] = evidence_scores
                scored["weightedCriteriaScore"] = criteria_score
                scored["appliedCriteria"] = normalized_criteria
            ranked.append(scored)
        return sorted(ranked, key=lambda item: item.get("matchScore", 0), reverse=True)

    async def _rank_with_llm(
        self,
        query: str,
        candidates: list[dict],
        item_type: str,
        weights: dict | None = None,
    ) -> dict[str, RankedItem]:
        if self.llm is None or not hasattr(self.llm, "with_structured_output"):
            return {}
        try:
            payload = [
                {
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("vendor"),
                    "category": item.get("category"),
                    "description": item.get("description") or item.get("product"),
                    "certifications": item.get("certifications"),
                    "price": item.get("unitPriceEur"),
                    "deliveryDays": item.get("deliveryDays"),
                    "baseScore": item.get("matchScore"),
                    "source": item.get("source"),
                    "repurchasePriority": item.get("repurchasePriority"),
                }
                for item in candidates
            ]
            system_instruction = ""
            if self.optimized_prompt_path.exists():
                try:
                    with open(self.optimized_prompt_path, "r", encoding="utf-8") as f:
                        system_instruction = f.read().strip() + "\n\n"
                except Exception:
                    pass

            criteria_instruction = ""
            if item_type == "supplier" and weights:
                criteria_instruction = (
                    "For suppliers, also respect these user-defined evaluation criteria "
                    f"and their relative weights: {weights}. "
                )
            elif item_type == "quote" and weights:
                criteria_instruction = f"For quote comparisons, respect these decision weights: {weights}. "

            prompt = (
                f"{system_instruction}"
                f"Rank these procurement {item_type} candidates for the query. "
                "Return one result per candidate id with matchScore 0-100 and a concise reason. "
                "For suppliers, candidates with source=database / repurchasePriority=database are existing database suppliers and should receive a modest repurchase preference when relevance is close; do not let this override a clearly much better new web supplier. "
                f"{criteria_instruction}"
                f"Query: {query}\nCandidates: {payload}"
            )
            structured_llm = self.llm.with_structured_output(RankingResponse, method="function_calling")
            response = await structured_llm.ainvoke(prompt)
            ranking = response if isinstance(response, RankingResponse) else RankingResponse.model_validate(response)
            return {item.id: item for item in ranking.results}
        except Exception:
            return {}

    def _heuristic_score(self, query: str, candidate: dict) -> int:
        text = " ".join(
            str(value)
            for value in [
                candidate.get("name"),
                candidate.get("category"),
                candidate.get("country"),
                candidate.get("description"),
                " ".join(candidate.get("products", [])),
                " ".join(candidate.get("certifications", [])),
                " ".join(candidate.get("capabilities", [])),
            ]
            if value
        ).lower()
        tokens = set(re.findall(r"[\w\u4e00-\u9fffäöüÄÖÜß+-]+", query.lower()))
        overlap = sum(1 for token in tokens if token in text)
        base = int(candidate.get("matchScore", 70))
        return max(0, min(100, base + min(12, overlap * 2)))

    def _quote_score(
        self,
        query: str,
        candidate: dict,
        max_delivery_days: Optional[int],
        weights: dict | None = None,
        stats: dict | None = None,
    ) -> int:
        relevance = self._heuristic_score(query, candidate)
        price = float(candidate.get("unitPriceEur") or 0)
        delivery_days = int(candidate.get("deliveryDays") or 99)
        rating = float(candidate.get("rating") or 0)
        weights = self._normalize_quote_weights(weights)
        stats = stats or self._quote_normalization_stats([candidate])

        min_price = stats["min_price"]
        max_price = stats["max_price"]
        if price and max_price > min_price:
            price_score = 100 - ((price - min_price) / (max_price - min_price) * 100)
        elif price:
            price_score = 85
        else:
            price_score = 45

        min_delivery = stats["min_delivery"]
        max_delivery = stats["max_delivery"]
        if delivery_days and max_delivery > min_delivery:
            delivery_score = 100 - ((delivery_days - min_delivery) / (max_delivery - min_delivery) * 100)
        elif delivery_days <= 3:
            delivery_score = 90
        elif max_delivery_days and delivery_days <= max_delivery_days:
            delivery_score = 78
        else:
            delivery_score = 50

        rating_score = max(0, min(100, (rating / 5.0) * 100)) if rating else 45
        decision_score = (
            price_score * weights["price"]
            + delivery_score * weights["delivery"]
            + rating_score * weights["rating"]
        ) / 100

        # Keep some semantic query relevance so an off-topic cheap item cannot dominate completely.
        score = relevance * 0.35 + decision_score * 0.65
        return max(0, min(100, round(score)))

    @staticmethod
    def _normalize_quote_weights(weights: dict | None) -> dict[str, float]:
        raw = weights or {}
        price = max(0.0, float(raw.get("price", 40) or 0))
        delivery = max(0.0, float(raw.get("delivery", 35) or 0))
        rating = max(0.0, float(raw.get("rating", 25) or 0))
        total = price + delivery + rating
        if total <= 0:
            return {"price": 40.0, "delivery": 35.0, "rating": 25.0}
        return {
            "price": price / total * 100,
            "delivery": delivery / total * 100,
            "rating": rating / total * 100,
        }

    @staticmethod
    def _normalize_supplier_criteria(criteria: list[dict] | None) -> list[dict]:
        """Validate, deduplicate and normalize arbitrary supplier criteria."""
        merged: dict[str, dict] = {}
        for raw in criteria or []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip()
            if not key:
                continue
            try:
                weight = max(0.0, float(raw.get("weight", 0) or 0))
            except (TypeError, ValueError):
                continue
            normalized_key = key.casefold()
            current = merged.get(normalized_key)
            if current is None:
                merged[normalized_key] = {
                    "key": key,
                    "label": str(raw.get("label") or key),
                    "weight": weight,
                }
            else:
                current["weight"] += weight
        total = sum(item["weight"] for item in merged.values())
        if total <= 0:
            return []
        return [
            {**item, "weight": round(item["weight"] / total * 100, 2)}
            for item in merged.values()
            if item["weight"] > 0
        ]

    @classmethod
    def _supplier_weighted_criteria_score(
        cls, candidate: dict, criteria: list[dict]
    ) -> tuple[float, dict[str, float]]:
        scores: dict[str, float] = {}
        weighted = 0.0
        for criterion in criteria:
            key = str(criterion["key"])
            score = cls._supplier_criterion_score(candidate, key, str(criterion.get("label") or ""))
            scores[key] = score
            weighted += score * float(criterion["weight"])
        return round(weighted / 100, 2), scores

    @staticmethod
    def _supplier_criterion_score(candidate: dict, key: str, label: str = "") -> float:
        """Resolve a 0–100 score from stored evidence without inventing facts."""
        canonical = re.sub(r"[\W_]", "", key.casefold())
        label_token = re.sub(r"[\W_]", "", label.casefold())
        searchable = f"{canonical} {label_token}"

        def matches(*aliases: str) -> bool:
            return any(alias.casefold() in searchable for alias in aliases)

        supplied = candidate.get("criteriaScores") or {}
        if isinstance(supplied, dict):
            for supplied_key, supplied_score in supplied.items():
                supplied_token = re.sub(r"[\W_]", "", str(supplied_key).casefold())
                if supplied_token not in {canonical, label_token}:
                    continue
                try:
                    return max(0.0, min(100.0, float(supplied_score)))
                except (TypeError, ValueError):
                    break

        rating = candidate.get("rating")
        try:
            rating_score = float(rating)
            rating_score = rating_score * 20 if rating_score <= 5 else rating_score
            rating_score = max(0.0, min(100.0, rating_score))
        except (TypeError, ValueError):
            rating_score = 50.0
        certifications = [str(item).casefold() for item in candidate.get("certifications", [])]
        environment = [
            *certifications,
            *(str(item).casefold() for item in candidate.get("environmentalStandards", [])),
        ]

        if matches("quality", "productquality", "质量", "品质"):
            return rating_score if rating is not None else (80.0 if certifications else 45.0)
        if matches("reputation", "supplierreputation", "reliability", "信誉", "声誉", "可靠"):
            return rating_score
        if matches("certification", "certifications", "compliance", "认证", "资质"):
            return 90.0 if certifications else 25.0
        if matches("environment", "environmental", "sustainability", "esg", "environmentalstandards", "环保", "环境", "可持续"):
            return 92.0 if any("14001" in item or "eco" in item or "esg" in item for item in environment) else 35.0
        if matches("capacity", "productioncapacity", "manufacturingcapacity", "生产能力", "产能"):
            return 82.0 if candidate.get("productionCapacity") or candidate.get("employees") else 40.0
        if matches("minimumorderquantity", "moq", "minimumquantity", "最低订购量", "最小起订量", "起订量"):
            return 80.0 if candidate.get("minimumOrderQuantity") else 40.0
        if matches("region", "location", "geography", "country", "地区", "区域", "地点"):
            return 82.0 if candidate.get("country") else 35.0
        if matches("incumbent", "supplierhistory", "historicalperformance", "pastperformance", "preferredsupplier", "历史", "合作记录", "历史表现"):
            return 92.0 if candidate.get("preferred") or candidate.get("source") == "database" else 45.0
        if matches("price", "cost", "价格", "报价", "成本"):
            return 72.0 if candidate.get("unitPriceEur") is not None else 45.0
        if matches("delivery", "leadtime", "deliverytime", "交付", "交期", "交货"):
            return 72.0 if candidate.get("deliveryDays") is not None else 45.0
        if matches("payment", "paymentterms", "付款", "支付"):
            return 72.0 if candidate.get("paymentTerm") or candidate.get("paymentLabel") else 45.0
        # A custom criterion with no stored evidence remains visibly neutral;
        # it must not be treated as a positive verification signal.
        return 50.0

    @staticmethod
    def _quote_normalization_stats(candidates: list[dict]) -> dict[str, float]:
        prices = [float(item.get("unitPriceEur")) for item in candidates if item.get("unitPriceEur") is not None]
        deliveries = [float(item.get("deliveryDays")) for item in candidates if item.get("deliveryDays") is not None]
        return {
            "min_price": min(prices) if prices else 0.0,
            "max_price": max(prices) if prices else 0.0,
            "min_delivery": min(deliveries) if deliveries else 0.0,
            "max_delivery": max(deliveries) if deliveries else 0.0,
        }

    @staticmethod
    def _apply_repurchase_bonus(candidate: dict) -> int:
        """Small bonus for known database suppliers when relevance is close.

        Reduced from +12 to +3 so web-researched suppliers with genuinely higher
        relevance can outrank marginal database hits.
        """
        score = int(candidate.get("matchScore", 0))
        if candidate.get("source") == "database" or candidate.get("repurchasePriority") == "database":
            score += 3  # 只做轻微复购偏好，不能压过明显更相关的网络供应商
        return max(0, min(100, score))

    @staticmethod
    def _supplier_reason(query: str, candidate: dict) -> str:
        strengths = []
        if candidate.get("country"):
            strengths.append(candidate["country"])
        strengths.extend(candidate.get("certifications", [])[:2])
        if candidate.get("capabilities"):
            strengths.append(candidate["capabilities"][0])
        return "Matches the query through " + ", ".join(strengths) if strengths else "Relevant supplier profile."

    @staticmethod
    def _quote_reason(candidate: dict) -> str:
        return (
            f"{candidate.get('unitLabel')} with {candidate.get('deliveryLabel')} delivery "
            f"and {candidate.get('rating')} rating."
        )

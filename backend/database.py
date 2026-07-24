"""
database.py
===========
数据库查询逻辑，连接 Supabase (PostgreSQL)。
按真实表结构编写：supplier / product / quote / sourcing_candidate
"""

import logging
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlparse
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Environment variables may already be supplied by the launcher.
    def load_dotenv(*args, **kwargs):
        return False

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ModuleNotFoundError:  # Web-only/offline mode does not require Postgres.
    psycopg2 = None  # type: ignore[assignment]
    RealDictCursor = None  # type: ignore[assignment]

load_dotenv()


logger = logging.getLogger(__name__)
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DRIVER_WARNING_EMITTED = False


def _string_values(value: object) -> list[str]:
    """Normalize JSONB evidence arrays while accepting older stored shapes."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for candidate in values:
        if not isinstance(candidate, str):
            continue
        cleaned = candidate.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _attribute_dict(value: object) -> dict:
    """Return a dict for JSONB attributes, including defensive legacy parsing."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_SYNTHETIC_TEST_PREFIX = re.compile(r"^\s*test(?:\b|[-_])", re.IGNORECASE)


def _is_synthetic_quote_record(row: dict) -> bool:
    """Identify obvious fixture/test quotes without deleting persisted data.

    This deliberately uses only explicit markers requested for local quote
    hygiene: vendor/product names beginning with ``TEST`` and a reserved
    ``test-shop`` host. Ordinary ``.example`` URLs are kept because they are
    also used by provenance and integration fixtures.
    """
    attrs = _attribute_dict(row.get("quote_attrs"))
    vendor = row.get("vendor_name")
    product = row.get("product_name") or row.get("listing_title")
    if any(_SYNTHETIC_TEST_PREFIX.match(str(value or "")) for value in (vendor, product)):
        return True

    urls = [row.get("source_url"), *_string_values(attrs.get("sourceUrls"))]
    for raw_url in urls:
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        candidate = raw_url.strip()
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        host = (parsed.hostname or "").casefold().strip(".")
        labels = set(host.split("."))
        if "test-shop" in labels or host.startswith("test-shop."):
            return True
    return False


def _quote_row_to_comparison_item(row: dict) -> dict:
    """Restore a persisted quote without losing API or Idealo provenance."""
    attrs = _attribute_dict(row.get("quote_attrs"))
    price = row.get("price")
    delivery_days = row.get("lead_time_days")
    source_urls = _string_values(attrs.get("sourceUrls"))
    stored_source_url = row.get("source_url")
    if isinstance(stored_source_url, str) and stored_source_url.strip() and stored_source_url.strip() not in source_urls:
        source_urls.insert(0, stored_source_url.strip())

    has_price = price not in (None, "")
    source = attrs.get("source") if isinstance(attrs.get("source"), str) and attrs.get("source") else "database"
    source_detail = (
        attrs.get("sourceDetail")
        if isinstance(attrs.get("sourceDetail"), str) and attrs.get("sourceDetail")
        else "database"
    )
    price_confidence = (
        attrs.get("priceConfidence")
        if isinstance(attrs.get("priceConfidence"), str) and attrs.get("priceConfidence")
        else ("extracted" if has_price else "unknown")
    )

    vendor = row["vendor_name"] or attrs.get("vendor") or attrs.get("seller") or "Unknown"
    platform = attrs.get("platform", "")
    vendor = str(vendor).strip() or "Unknown"
    return {
        "id": str(row["id"]),
        "vendor": vendor,
        "platform": platform,
        "product": row["product_name"] or row["listing_title"] or "",
        "matchScore": int(float(row["score"]) * 20) if row["score"] else 70,
        "unitPriceEur": float(price) if has_price else None,
        "unitLabel": f"€ {price}" if has_price else "需人工核价",
        "deliveryDays": int(delivery_days) if delivery_days not in (None, "") else None,
        "deliveryLabel": row["lead_time_text"] or "需确认交期",
        "paymentTerm": attrs.get("paymentTerm", "onAccount"),
        "paymentLabel": attrs.get("paymentLabel", ""),
        "deliveryMethod": attrs.get("deliveryMethod", ""),
        "rating": float(row["score"]) if row["score"] else 0.0,
        "reviews": attrs.get("reviews", 0),
        "category": attrs.get("category", ""),
        "source": source,
        "sourceDetail": source_detail,
        "sourceUrls": source_urls,
        "evidenceSnippets": _string_values(attrs.get("evidenceSnippets")),
        "priceConfidence": price_confidence,
        "criteriaScores": attrs.get("criteriaScores", {}),
        "weightedCriteriaScore": attrs.get("weightedCriteriaScore"),
        "appliedCriteria": attrs.get("appliedCriteria", []),
        "vendorVerified": vendor.casefold() != "unknown",
    }


def _load_local_supplier_directory() -> list[dict]:
    """Load bundled seed suppliers plus user-managed offline records.

    This keeps local sourcing useful when Supabase is intentionally unavailable
    (for example during a desktop demo or a temporary network outage).  The
    editable file is written by ``api.suppliers`` and is never required to be
    present in a source checkout.
    """
    suppliers: list[dict] = []
    seen: set[str] = set()
    for path in (_DATA_DIR / "suppliers.json", _DATA_DIR / "supplier_directory.local.json"):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read local supplier data; skipping one local data source.")
            continue
        if not isinstance(raw, list):
            continue
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            record = dict(item)
            key = str(record.get("id") or record["name"]).casefold()
            if key in seen:
                continue
            seen.add(key)
            record.setdefault("id", f"local-{index}")
            record.setdefault("category", "general")
            record.setdefault("products", [])
            record.setdefault("capabilities", [])
            record.setdefault("certifications", [])
            record.setdefault("matchScore", 70)
            record["source"] = "database"
            record.setdefault("sourceDetail", "local-file")
            record.setdefault("repurchasePriority", "database")
            suppliers.append(record)
    return suppliers


def _local_suppliers_matching(category: str | None = None, country: str | None = None) -> list[dict]:
    records = _load_local_supplier_directory()
    if category:
        records = [record for record in records if record.get("category") == category]
    if country:
        records = [record for record in records if record.get("country") == country]
    return records


def _number_or_none(value: object) -> float | None:
    """Return a finite numeric value, or ``None`` for absent local catalog data."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer_or_none(value: object) -> int | None:
    number = _number_or_none(value)
    return int(number) if number is not None else None


def _load_local_quote_catalog(
    max_price: float | None = None,
    max_delivery_days: int | None = None,
) -> list[dict]:
    """Load bundled comparison quotes when Postgres is not available.

    ``quotes.json`` is a portable comparison catalog rather than a database
    dump. Normalize it here so offline results use the persisted quote contract
    and provenance fields without inventing source URLs.
    """
    path = _DATA_DIR / "quotes.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read local quote data; offline comparison has no seed quotes.")
        return []
    if not isinstance(raw, list):
        logger.warning("Local quote data has an invalid format; offline comparison has no seed quotes.")
        return []

    records: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        vendor = str(item.get("vendor") or "").strip()
        product = str(item.get("product") or "").strip()
        if not vendor or not product:
            continue

        price = _number_or_none(item.get("unitPriceEur"))
        delivery_days = _integer_or_none(item.get("deliveryDays"))
        if max_price is not None and (price is None or price > max_price):
            continue
        if max_delivery_days is not None and (delivery_days is None or delivery_days > max_delivery_days):
            continue

        unit_label = item.get("unitLabel")
        delivery_label = item.get("deliveryLabel")
        match_score = _integer_or_none(item.get("matchScore"))
        records.append({
            "id": str(item.get("id") or f"local-quote-{index}"),
            "vendor": vendor,
            "platform": str(item.get("platform") or "").strip(),
            "product": product,
            "matchScore": match_score if match_score is not None else 70,
            "unitPriceEur": price,
            "unitLabel": str(unit_label).strip() if unit_label else (f"€ {price}" if price is not None else "需人工核价"),
            "deliveryDays": delivery_days,
            "deliveryLabel": str(delivery_label).strip() if delivery_label else "需确认交期",
            "paymentTerm": str(item.get("paymentTerm") or "onAccount"),
            "paymentLabel": str(item.get("paymentLabel") or ""),
            "deliveryMethod": str(item.get("deliveryMethod") or ""),
            "rating": _number_or_none(item.get("rating")) or 0.0,
            "reviews": _integer_or_none(item.get("reviews")) or 0,
            "category": str(item.get("category") or ""),
            "source": "database",
            "sourceDetail": "local-file",
            "sourceUrls": _string_values(item.get("sourceUrls")),
            "evidenceSnippets": _string_values(item.get("evidenceSnippets")),
            "priceConfidence": "extracted" if price is not None else "unknown",
            "criteriaScores": item.get("criteriaScores") if isinstance(item.get("criteriaScores"), dict) else {},
            "weightedCriteriaScore": _number_or_none(item.get("weightedCriteriaScore")),
            "appliedCriteria": item.get("appliedCriteria") if isinstance(item.get("appliedCriteria"), list) else [],
            "vendorVerified": vendor.casefold() != "unknown",
        })
    return records


def get_connection():
    """Return a PostgreSQL connection, or ``None`` when persistence is unavailable.

    The procurement workflow can still use live web research without Supabase.
    A bounded connection attempt is therefore important: an unreachable database
    must not make the API appear to hang during startup or a search request.
    """
    global _DRIVER_WARNING_EMITTED
    if psycopg2 is None:
        if not _DRIVER_WARNING_EMITTED:
            logger.warning("PostgreSQL driver is not installed; continuing in local/web-only mode.")
            _DRIVER_WARNING_EMITTED = True
        return None
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    try:
        timeout = max(1, int(os.getenv("DATABASE_CONNECT_TIMEOUT", "3")))
    except ValueError:
        timeout = 3
    try:
        return psycopg2.connect(database_url, connect_timeout=timeout)
    except psycopg2.Error as exc:
        # Do not log DATABASE_URL or connection details; callers deliberately
        # degrade to web search / local storage when the DB is unavailable.
        logger.warning("Database unavailable (%s); continuing without it.", type(exc).__name__)
        return None


# ═══════════════════════════════════════════════════════
# Sourcing：查询供应商
# ═══════════════════════════════════════════════════════

async def query_suppliers(
    category: str | None = None,
    country: str | None = None,
) -> list[dict]:
    """
    查询供应商表，返回符合前端 Supplier 格式的数据。

    注意：真实表里没有前端需要的所有字段（比如 capabilities,
    certifications, established, employees, annualRevenue），
    这些字段如果不存在，先用默认值占位，等数据补全。
    """
    conn = get_connection()
    if conn is None:
        return _local_suppliers_matching(category, country)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT
                s.id,
                s.name,
                s.origin,
                s.country,
                s.website,
                s.contact_name,
                s.contact_email,
                s.contact_phone,
                s.scale,
                s.rating,
                s.attributes
            FROM supplier s
            WHERE 1=1
        """
        params = []
        if country:
            query += " AND s.country = %s"
            params.append(country)
        cur.execute(query, params)
        rows = cur.fetchall()

        # 转换成前端期望的字段名
        results = []
        for row in rows:
            attrs = row.get("attributes") or {}
            results.append({
                "id": str(row["id"]),
                "name": row["name"],
                "category": category or attrs.get("category", "unknown"),
                "country": row["country"] or "",
                "city": attrs.get("city", ""),
                "description": attrs.get("description", ""),
                "products": attrs.get("products", []),
                "address": attrs.get("address", ""),
                "contactPerson": row["contact_name"] or "",
                "phone": row["contact_phone"] or "",
                "email": row["contact_email"] or "",
                "website": row["website"] or "",
                "employees": row["scale"] or "",
                "annualRevenue": attrs.get("annualRevenue", ""),
                "established": attrs.get("established", 0),
                "capabilities": attrs.get("capabilities", []),
                "certifications": attrs.get("certifications", []),
                "productName": attrs.get("productName"),
                "brand": attrs.get("brand"),
                "model": attrs.get("model"),
                "specifications": attrs.get("specifications"),
                "standards": attrs.get("standards", []),
                "notes": attrs.get("notes"),
                "historicalPerformance": attrs.get("historicalPerformance"),
                "minimumOrderQuantity": attrs.get("minimumOrderQuantity"),
                "productionCapacity": attrs.get("productionCapacity"),
                "environmentalStandards": attrs.get("environmentalStandards", []),
                "unitPriceEur": attrs.get("unitPriceEur"),
                "quoteConditions": attrs.get("quoteConditions"),
                "deliveryDays": attrs.get("deliveryDays"),
                "deliveryLabel": attrs.get("deliveryLabel"),
                "paymentTerm": attrs.get("paymentTerm"),
                "paymentLabel": attrs.get("paymentLabel"),
                "sourceUrls": attrs.get("sourceUrls", []),
                "evidenceSnippets": attrs.get("evidenceSnippets", []),
                "verificationStatus": attrs.get("verificationStatus"),
                "verificationNotes": attrs.get("verificationNotes", []),
                "matchScore": int(float(row["rating"]) * 20) if row["rating"] else 70,
                "source": "database",
                "sourceDetail": row.get("origin") or "database",
                "repurchasePriority": "database",
                "tags": attrs.get("tags", []),
                "preferred": bool(attrs.get("preferred", False)),
                "criteriaScores": attrs.get("criteriaScores", {}),
            })
        return results
    except Exception as exc:
        logger.warning("Supplier query failed (%s); falling back to local supplier data.", type(exc).__name__)
        return _local_suppliers_matching(category, country)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# Comparison：查询报价
# ═══════════════════════════════════════════════════════

async def query_products(
    max_price: float | None = None,
    max_delivery_days: int | None = None,
) -> list[dict]:
    """
    查询报价表（quote 联 product 和 supplier），
    返回符合前端 ComparisonItem 格式的数据。
    """
    conn = get_connection()
    if conn is None:
        return _load_local_quote_catalog(max_price, max_delivery_days)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT
                q.id,
                q.listing_title,
                q.price,
                q.lead_time_text,
                q.lead_time_days,
                q.score,
                q.source_url,
                q.attributes AS quote_attrs,
                s.name AS vendor_name,
                p.name_de AS product_name
            FROM quote q
            LEFT JOIN supplier s ON q.supplier_id = s.id
            LEFT JOIN product p ON q.product_id = p.id
            WHERE 1=1
        """
        params = []
        if max_price is not None:
            query += " AND q.price <= %s"
            params.append(max_price)
        if max_delivery_days is not None:
            query += " AND q.lead_time_days <= %s"
            params.append(max_delivery_days)
        cur.execute(query, params)
        rows = cur.fetchall()

        return [
            _quote_row_to_comparison_item(row)
            for row in rows
            if not _is_synthetic_quote_record(row)
        ]
    except Exception as exc:
        logger.warning("Quote query failed (%s); falling back to local quote data.", type(exc).__name__)
        return _load_local_quote_catalog(max_price, max_delivery_days)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# 同步版本（给 ProcurementAgent.__init__ 用，因为 __init__ 不能 await）
# ═══════════════════════════════════════════════════════

def query_suppliers_sync() -> list[dict]:
    """
    同步版查询所有供应商，给 Agent 启动时一次性加载用。
    无数据库连接时回退到本地种子数据和可编辑的离线资料库。
    """
    conn = get_connection()
    if conn is None:
        return _load_local_supplier_directory()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM supplier")
        rows = cur.fetchall()

        results = []
        for row in rows:
            attrs = row.get("attributes") or {}
            results.append({
                "id": str(row["id"]),
                "name": row["name"],
                "category": attrs.get("category", "unknown"),
                "country": row["country"] or "",
                "city": attrs.get("city", ""),
                "description": attrs.get("description", ""),
                "products": attrs.get("products", []),
                "address": attrs.get("address", ""),
                "contactPerson": row["contact_name"] or "",
                "phone": row["contact_phone"] or "",
                "email": row["contact_email"] or "",
                "website": row["website"] or "",
                "employees": row["scale"] or "",
                "annualRevenue": attrs.get("annualRevenue", ""),
                "established": attrs.get("established", 0),
                "capabilities": attrs.get("capabilities", []),
                "certifications": attrs.get("certifications", []),
                "productName": attrs.get("productName"),
                "brand": attrs.get("brand"),
                "model": attrs.get("model"),
                "specifications": attrs.get("specifications"),
                "standards": attrs.get("standards", []),
                "notes": attrs.get("notes"),
                "historicalPerformance": attrs.get("historicalPerformance"),
                "minimumOrderQuantity": attrs.get("minimumOrderQuantity"),
                "productionCapacity": attrs.get("productionCapacity"),
                "environmentalStandards": attrs.get("environmentalStandards", []),
                "unitPriceEur": attrs.get("unitPriceEur"),
                "quoteConditions": attrs.get("quoteConditions"),
                "deliveryDays": attrs.get("deliveryDays"),
                "deliveryLabel": attrs.get("deliveryLabel"),
                "paymentTerm": attrs.get("paymentTerm"),
                "paymentLabel": attrs.get("paymentLabel"),
                "sourceUrls": attrs.get("sourceUrls", []),
                "evidenceSnippets": attrs.get("evidenceSnippets", []),
                "verificationStatus": attrs.get("verificationStatus"),
                "verificationNotes": attrs.get("verificationNotes", []),
                "matchScore": int(float(row["rating"]) * 20) if row["rating"] else 70,
                "source": "database",
                "sourceDetail": row.get("origin") or "database",
                "repurchasePriority": "database",
                "tags": attrs.get("tags", []),
                "preferred": bool(attrs.get("preferred", False)),
                "criteriaScores": attrs.get("criteriaScores", {}),
            })
        return results
    except Exception as exc:
        logger.warning("Supplier catalogue query failed (%s); falling back to local supplier data.", type(exc).__name__)
        return _load_local_supplier_directory()
    finally:
        conn.close()

'''comparision模块做完后需要修改本部分代码，目前接到了supplier的数据集上测试是否跑通'''
def query_products_sync() -> list[dict]:
    """
    同步版查询所有报价，给 Agent 启动时一次性加载用。
    无数据库连接时回退到内置报价目录，支持离线标准品比价。
    """
    conn = get_connection()
    if conn is None:
        return _load_local_quote_catalog()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                q.id,
                q.listing_title,
                q.price,
                q.lead_time_text,
                q.lead_time_days,
                q.score,
                q.source_url,
                q.attributes AS quote_attrs,
                s.name AS vendor_name,
                p.name_de AS product_name
            FROM quote q
            LEFT JOIN supplier s ON q.supplier_id = s.id
            LEFT JOIN product p ON q.product_id = p.id
        """)
        rows = cur.fetchall()

        return [
            _quote_row_to_comparison_item(row)
            for row in rows
            if not _is_synthetic_quote_record(row)
        ]
    except Exception as exc:
        logger.warning("Quote catalogue query failed (%s); falling back to local quote data.", type(exc).__name__)
        return _load_local_quote_catalog()
    finally:
        conn.close()

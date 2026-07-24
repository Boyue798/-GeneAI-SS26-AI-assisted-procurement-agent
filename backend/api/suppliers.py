"""Supplier directory API.

This is the management surface for the enterprise's local supplier knowledge.
It uses the shared PostgreSQL ``supplier`` table when available and falls back
to a small local JSON file for desktop/offline development.  Search candidates
remain separate from this directory until a user explicitly promotes them.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ModuleNotFoundError:  # The JSON fallback works without the optional driver.
    psycopg2 = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment]
    RealDictCursor = None  # type: ignore[assignment]

from api.auth import AuthUser, get_current_user
from database import get_connection


router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])
logger = logging.getLogger(__name__)

_LOCAL_DIRECTORY_PATH = Path(__file__).resolve().parents[1] / "data" / "supplier_directory.local.json"
_LOCAL_DIRECTORY_LOCK = threading.Lock()


class SupplierDirectoryPersistenceError(RuntimeError):
    """A connected supplier database rejected or could not process a write."""


class SupplierDirectoryConflictError(SupplierDirectoryPersistenceError):
    """A supplier with the same unique identity already exists."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _criteria_scores(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    scores: dict[str, float] = {}
    for key, score in value.items():
        try:
            scores[str(key)] = max(0.0, min(100.0, float(score)))
        except (TypeError, ValueError):
            continue
    return scores


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _timestamp_ms(value: Any) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    return _now_ms()


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    origin: Literal["internal", "web"] = "internal"
    category: str = Field(default="general", max_length=120)
    productName: str | None = Field(default=None, max_length=240)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    specifications: str | None = Field(default=None, max_length=2000)
    standards: list[str] = Field(default_factory=list)
    country: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=500)
    website: str | None = Field(default=None, max_length=500)
    contactPerson: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    employees: str | None = Field(default=None, max_length=80)
    annualRevenue: str | None = Field(default=None, max_length=120)
    established: int | None = Field(default=None, ge=1800, le=2200)
    products: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    preferred: bool = False
    historicalPerformance: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=4000)
    minimumOrderQuantity: str | None = Field(default=None, max_length=120)
    productionCapacity: str | None = Field(default=None, max_length=160)
    environmentalStandards: list[str] = Field(default_factory=list)
    criteriaScores: dict[str, float] = Field(default_factory=dict)
    unitPriceEur: float | None = Field(default=None, ge=0)
    quoteConditions: str | None = Field(default=None, max_length=1000)
    deliveryDays: int | None = Field(default=None, ge=0)
    deliveryLabel: str | None = Field(default=None, max_length=240)
    paymentTerm: str | None = Field(default=None, max_length=240)
    paymentLabel: str | None = Field(default=None, max_length=240)
    rating: float | None = Field(default=None, ge=0, le=5)


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    origin: Literal["internal", "web"] | None = None
    category: str | None = Field(default=None, max_length=120)
    productName: str | None = Field(default=None, max_length=240)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    specifications: str | None = Field(default=None, max_length=2000)
    standards: list[str] | None = None
    country: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=500)
    website: str | None = Field(default=None, max_length=500)
    contactPerson: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    employees: str | None = Field(default=None, max_length=80)
    annualRevenue: str | None = Field(default=None, max_length=120)
    established: int | None = Field(default=None, ge=1800, le=2200)
    products: list[str] | None = None
    capabilities: list[str] | None = None
    certifications: list[str] | None = None
    tags: list[str] | None = None
    preferred: bool | None = None
    historicalPerformance: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=4000)
    minimumOrderQuantity: str | None = Field(default=None, max_length=120)
    productionCapacity: str | None = Field(default=None, max_length=160)
    environmentalStandards: list[str] | None = None
    criteriaScores: dict[str, float] | None = None
    unitPriceEur: float | None = Field(default=None, ge=0)
    quoteConditions: str | None = Field(default=None, max_length=1000)
    deliveryDays: int | None = Field(default=None, ge=0)
    deliveryLabel: str | None = Field(default=None, max_length=240)
    paymentTerm: str | None = Field(default=None, max_length=240)
    paymentLabel: str | None = Field(default=None, max_length=240)
    rating: float | None = Field(default=None, ge=0, le=5)


class SupplierRecord(BaseModel):
    id: str
    name: str
    origin: Literal["internal", "web"]
    source: Literal["database"] = "database"
    sourceDetail: str
    category: str = "general"
    productName: str | None = None
    brand: str | None = None
    model: str | None = None
    specifications: str | None = None
    standards: list[str] = Field(default_factory=list)
    country: str = ""
    city: str = ""
    address: str = ""
    website: str = ""
    contactPerson: str = ""
    email: str = ""
    phone: str = ""
    employees: str = ""
    annualRevenue: str = ""
    established: int | None = None
    products: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    preferred: bool = False
    historicalPerformance: str | None = None
    notes: str | None = None
    minimumOrderQuantity: str | None = None
    productionCapacity: str | None = None
    environmentalStandards: list[str] = Field(default_factory=list)
    criteriaScores: dict[str, float] = Field(default_factory=dict)
    unitPriceEur: float | None = None
    quoteConditions: str | None = None
    deliveryDays: int | None = None
    deliveryLabel: str | None = None
    paymentTerm: str | None = None
    paymentLabel: str | None = None
    rating: float | None = None
    matchScore: int = Field(ge=0, le=100)
    sourceUrls: list[str] = Field(default_factory=list)
    verificationStatus: Literal["verified", "needs-review"] = "needs-review"
    createdAt: int
    updatedAt: int


_DIRECT_COLUMNS = {
    "name": "name",
    "origin": "origin",
    "country": "country",
    "website": "website",
    "contactPerson": "contact_name",
    "email": "contact_email",
    "phone": "contact_phone",
    "employees": "scale",
    "rating": "rating",
}
_ATTRIBUTE_FIELDS = {
    "category",
    "productName",
    "brand",
    "model",
    "specifications",
    "standards",
    "city",
    "address",
    "annualRevenue",
    "established",
    "products",
    "capabilities",
    "certifications",
    "tags",
    "preferred",
    "historicalPerformance",
    "notes",
    "minimumOrderQuantity",
    "productionCapacity",
    "environmentalStandards",
    "criteriaScores",
    "unitPriceEur",
    "quoteConditions",
    "deliveryDays",
    "deliveryLabel",
    "paymentTerm",
    "paymentLabel",
}


def _record_from_values(
    values: dict[str, Any],
    *,
    supplier_id: str,
    created_at: int | None = None,
    updated_at: int | None = None,
) -> SupplierRecord:
    origin = values.get("origin") if values.get("origin") in {"internal", "web"} else "web"
    rating = values.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    website = str(values.get("website") or "")
    has_contact_evidence = bool(website or values.get("email") or values.get("phone"))
    created_at = created_at or _now_ms()
    updated_at = updated_at or _integer_or_none(values.get("updatedAt")) or created_at
    return SupplierRecord(
        id=str(supplier_id),
        name=str(values.get("name") or ""),
        origin=origin,
        sourceDetail=origin,
        category=str(values.get("category") or "general"),
        productName=values.get("productName"),
        brand=values.get("brand"),
        model=values.get("model"),
        specifications=values.get("specifications"),
        standards=_string_list(values.get("standards")),
        country=str(values.get("country") or ""),
        city=str(values.get("city") or ""),
        address=str(values.get("address") or ""),
        website=website,
        contactPerson=str(values.get("contactPerson") or ""),
        email=str(values.get("email") or ""),
        phone=str(values.get("phone") or ""),
        employees=str(values.get("employees") or ""),
        annualRevenue=str(values.get("annualRevenue") or ""),
        established=values.get("established") if isinstance(values.get("established"), int) else None,
        products=_string_list(values.get("products")),
        capabilities=_string_list(values.get("capabilities")),
        certifications=_string_list(values.get("certifications")),
        tags=_string_list(values.get("tags")),
        preferred=bool(values.get("preferred", False)),
        historicalPerformance=values.get("historicalPerformance"),
        notes=values.get("notes"),
        minimumOrderQuantity=values.get("minimumOrderQuantity"),
        productionCapacity=values.get("productionCapacity"),
        environmentalStandards=_string_list(values.get("environmentalStandards")),
        criteriaScores=_criteria_scores(values.get("criteriaScores")),
        unitPriceEur=_number_or_none(values.get("unitPriceEur")),
        quoteConditions=values.get("quoteConditions"),
        deliveryDays=_integer_or_none(values.get("deliveryDays")),
        deliveryLabel=values.get("deliveryLabel"),
        paymentTerm=values.get("paymentTerm"),
        paymentLabel=values.get("paymentLabel"),
        rating=rating,
        matchScore=max(0, min(100, round(rating * 20))) if rating is not None else 70,
        sourceUrls=[website] if website else [],
        verificationStatus="verified" if has_contact_evidence else "needs-review",
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _record_from_db_row(row: dict[str, Any]) -> SupplierRecord:
    attrs = _attributes(row.get("attributes"))
    values = {
        **attrs,
        "name": row.get("name"),
        "origin": row.get("origin"),
        "country": row.get("country"),
        "website": row.get("website"),
        "contactPerson": row.get("contact_name"),
        "email": row.get("contact_email"),
        "phone": row.get("contact_phone"),
        "employees": row.get("scale"),
        "rating": row.get("rating"),
    }
    return _record_from_values(values, supplier_id=str(row["id"]), created_at=_timestamp_ms(row.get("created_at")))


def _create_attributes(values: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key in _ATTRIBUTE_FIELDS:
        if key not in values:
            continue
        value = values[key]
        if key in {"products", "capabilities", "certifications", "tags", "standards", "environmentalStandards"}:
            attrs[key] = _string_list(value)
        elif key == "criteriaScores":
            attrs[key] = _criteria_scores(value)
        elif value is not None:
            attrs[key] = value
    return attrs


def _read_local() -> list[SupplierRecord]:
    with _LOCAL_DIRECTORY_LOCK:
        if not _LOCAL_DIRECTORY_PATH.exists():
            return []
        try:
            raw = json.loads(_LOCAL_DIRECTORY_PATH.read_text(encoding="utf-8"))
            return [SupplierRecord.model_validate(item) for item in raw if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("Local supplier directory could not be read; using an empty directory.")
            return []


def _write_local(records: list[SupplierRecord]) -> None:
    with _LOCAL_DIRECTORY_LOCK:
        _LOCAL_DIRECTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LOCAL_DIRECTORY_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(_LOCAL_DIRECTORY_PATH)


def _db_list() -> list[SupplierRecord] | None:
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM supplier ORDER BY created_at DESC, name ASC")
            return [_record_from_db_row(dict(row)) for row in cur.fetchall()]
    except psycopg2.Error as exc:
        logger.warning("Supplier directory database query failed (%s); using local storage.", type(exc).__name__)
        return None
    finally:
        conn.close()


def _db_create(values: dict[str, Any]) -> SupplierRecord | None:
    conn = get_connection()
    if conn is None:
        return None
    try:
        attrs = _create_attributes(values)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO supplier (
                    name, origin, website, country, contact_name, contact_email,
                    contact_phone, scale, rating, attributes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    values["name"], values.get("origin", "internal"), values.get("website"),
                    values.get("country"), values.get("contactPerson"), values.get("email"),
                    values.get("phone"), values.get("employees"), values.get("rating"), Json(attrs),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return _record_from_db_row(dict(row)) if row else None
    except psycopg2.Error as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == "23505":
            raise SupplierDirectoryConflictError("A supplier with this name and website already exists.") from exc
        logger.warning("Supplier directory database create failed (%s).", type(exc).__name__)
        raise SupplierDirectoryPersistenceError("Supplier directory database write failed.") from exc
    finally:
        conn.close()


def _db_update(supplier_id: str, changes: dict[str, Any]) -> SupplierRecord | None | bool:
    """Return a record, ``False`` for not-found, or ``None`` for DB fallback."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM supplier WHERE id = %s FOR UPDATE", (supplier_id,))
            existing = cur.fetchone()
            if existing is None:
                conn.rollback()
                return False
            attrs = _attributes(existing.get("attributes"))
            sets: list[str] = []
            values: list[Any] = []
            for api_name, column in _DIRECT_COLUMNS.items():
                if api_name in changes:
                    sets.append(f"{column} = %s")
                    values.append(changes[api_name])
            for key in _ATTRIBUTE_FIELDS:
                if key not in changes:
                    continue
                value = changes[key]
                if value is None:
                    attrs.pop(key, None)
                elif key in {"products", "capabilities", "certifications", "tags", "standards", "environmentalStandards"}:
                    attrs[key] = _string_list(value)
                elif key == "criteriaScores":
                    attrs[key] = _criteria_scores(value)
                else:
                    attrs[key] = value
            # The schema has created_at but no supplier-level updated_at.  Keep
            # a precise update timestamp in attributes for the API/UI contract.
            if sets or any(key in changes for key in _ATTRIBUTE_FIELDS):
                attrs["updatedAt"] = _now_ms()
                sets.append("attributes = %s")
                values.append(Json(attrs))
            if not sets:
                conn.rollback()
                return _record_from_db_row(dict(existing))
            values.append(supplier_id)
            cur.execute(f"UPDATE supplier SET {', '.join(sets)} WHERE id = %s RETURNING *", values)
            row = cur.fetchone()
        conn.commit()
        return _record_from_db_row(dict(row)) if row else False
    except psycopg2.Error as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == "23505":
            raise SupplierDirectoryConflictError("A supplier with this name and website already exists.") from exc
        logger.warning("Supplier directory database update failed (%s).", type(exc).__name__)
        raise SupplierDirectoryPersistenceError("Supplier directory database write failed.") from exc
    finally:
        conn.close()


def _db_delete(supplier_id: str) -> bool | None:
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier WHERE id = %s", (supplier_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except psycopg2.Error as exc:
        conn.rollback()
        logger.warning("Supplier directory database delete failed (%s).", type(exc).__name__)
        raise SupplierDirectoryPersistenceError("Supplier directory database write failed.") from exc
    finally:
        conn.close()


async def _refresh_agent_catalog(request: Request) -> None:
    try:
        # Existing result caches must not hide a just-added or edited supplier.
        from api.sourcing import clear_search_result_cache
        clear_search_result_cache()
    except Exception:
        logger.warning("Could not invalidate the sourcing result cache after a directory update.")
    agent = getattr(request.app.state, "agent", None)
    refresh = getattr(agent, "refresh_local_catalog", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # A successful directory edit must not fail because a refresh is unavailable.
            logger.warning("Supplier catalogue refresh failed after a directory update.")


def _filter_records(
    records: list[SupplierRecord],
    query: str | None,
    country: str | None,
    origin: str | None,
    tag: str | None,
) -> list[SupplierRecord]:
    needle = (query or "").strip().casefold()
    country_needle = (country or "").strip().casefold()
    tag_needle = (tag or "").strip().casefold()
    filtered: list[SupplierRecord] = []
    for record in records:
        if origin and record.origin != origin:
            continue
        if country_needle and country_needle not in record.country.casefold():
            continue
        if tag_needle and tag_needle not in {item.casefold() for item in record.tags}:
            continue
        if needle:
            haystack = " ".join(
                [record.name, record.category, record.country, record.city, *record.products, *record.certifications, *record.tags]
            ).casefold()
            if needle not in haystack:
                continue
        filtered.append(record)
    return filtered


@router.get("", response_model=list[SupplierRecord])
async def list_suppliers(
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    query: str | None = None,
    country: str | None = None,
    origin: Literal["internal", "web"] | None = None,
    tag: str | None = None,
) -> list[SupplierRecord]:
    del current_user  # auth is intentionally required even though records are company-wide.
    records = _db_list()
    if records is None:
        records = _read_local()
    return _filter_records(records, query, country, origin, tag)


@router.post("", response_model=SupplierRecord, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    req: SupplierCreate,
    request: Request,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> SupplierRecord:
    del current_user
    values = req.model_dump()
    try:
        record = _db_create(values)
    except SupplierDirectoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "CONFLICT", "message": str(exc)}) from exc
    except SupplierDirectoryPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "DATABASE_UNAVAILABLE", "message": str(exc)}) from exc
    if record is None:
        records = _read_local()
        record = _record_from_values(values, supplier_id=f"local-{uuid4().hex}")
        records.append(record)
        _write_local(records)
    await _refresh_agent_catalog(request)
    return record


@router.patch("/{supplier_id}", response_model=SupplierRecord)
async def update_supplier(
    supplier_id: str,
    req: SupplierUpdate,
    request: Request,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> SupplierRecord:
    del current_user
    changes = req.model_dump(exclude_unset=True)
    try:
        updated = _db_update(supplier_id, changes)
    except SupplierDirectoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "CONFLICT", "message": str(exc)}) from exc
    except SupplierDirectoryPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "DATABASE_UNAVAILABLE", "message": str(exc)}) from exc
    if updated is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": "Supplier not found"})
    if updated is None:
        records = _read_local()
        for index, record in enumerate(records):
            if record.id != supplier_id:
                continue
            values = record.model_dump()
            values.update(changes)
            values["updatedAt"] = _now_ms()
            updated = _record_from_values(
                values, supplier_id=record.id, created_at=record.createdAt, updated_at=values["updatedAt"]
            )
            records[index] = updated
            _write_local(records)
            break
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": "Supplier not found"})
    await _refresh_agent_catalog(request)
    return updated


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: str,
    request: Request,
    response: Response,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> None:
    del current_user
    try:
        deleted = _db_delete(supplier_id)
    except SupplierDirectoryPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "DATABASE_UNAVAILABLE", "message": str(exc)}) from exc
    if deleted is None:
        records = _read_local()
        remaining = [record for record in records if record.id != supplier_id]
        deleted = len(remaining) != len(records)
        if deleted:
            _write_local(remaining)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": "Supplier not found"})
    await _refresh_agent_catalog(request)
    response.status_code = status.HTTP_204_NO_CONTENT

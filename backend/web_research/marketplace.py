"""Optional fast marketplace quote providers for standard-product comparison.

The comparison pipeline can use these providers before slower page scraping.
They are deliberately opt-in: when no provider credentials or endpoint are
configured, :class:`MarketplaceSearchLayer` returns an empty list without any
network request and the existing Idealo/web-search fallback continues normally.

Supported configuration:

* ``SERPAPI_API_KEY`` enables SerpApi's Google Shopping engine. It is queried
  before the other optional providers. ``SERPAPI_COUNTRY`` defaults to ``de``;
  request countries are resolved through a fixed Google Shopping market map.
  ``SERPAPI_CALL_BUDGET`` sets the process-wide request limit and defaults to
  ``70``; cached responses do not consume the budget.
* ``EBAY_CLIENT_ID`` and ``EBAY_CLIENT_SECRET`` enable the official eBay
  Browse API. ``EBAY_MARKETPLACE_ID`` defaults to ``EBAY_DE``.
* ``MARKETPLACE_API_URL`` enables a generic JSON connector. It sends
  ``query``, ``limit`` and ``country`` and accepts common item field names
  (``title``/``product``, ``price``/``unitPriceEur``, ``url``/``itemWebUrl``).
  ``MARKETPLACE_API_KEY`` is optional and, when supplied, is sent as a Bearer
  token by default. Set ``MARKETPLACE_API_METHOD=POST`` only for an endpoint
  that explicitly requires POST; the default is a read-only GET request.

No Amazon, Taobao, or other marketplace is implied by this module. Each needs
its own authorised API integration and credentials.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import threading
import time
from collections.abc import Iterable
from typing import Any, Protocol

import httpx


_SERPAPI_MARKETS: dict[str, tuple[str, str, str]] = {
    "at": ("at", "de", "google.at"),
    "be": ("be", "nl", "google.be"),
    "ca": ("ca", "en", "google.ca"),
    "de": ("de", "de", "google.de"),
    "es": ("es", "es", "google.es"),
    "fr": ("fr", "fr", "google.fr"),
    "gb": ("uk", "en", "google.co.uk"),
    "it": ("it", "it", "google.it"),
    "nl": ("nl", "nl", "google.nl"),
    "pl": ("pl", "pl", "google.pl"),
    "us": ("us", "en", "google.com"),
}
_SERPAPI_COUNTRY_ALIASES = {
    "austria": "at",
    "at": "at",
    "belgium": "be",
    "be": "be",
    "canada": "ca",
    "ca": "ca",
    "de": "de",
    "deutschland": "de",
    "germany": "de",
    "es": "es",
    "spain": "es",
    "fr": "fr",
    "france": "fr",
    "gb": "gb",
    "greatbritain": "gb",
    "uk": "gb",
    "unitedkingdom": "gb",
    "it": "it",
    "italy": "it",
    "netherlands": "nl",
    "nl": "nl",
    "holland": "nl",
    "pl": "pl",
    "poland": "pl",
    "polska": "pl",
    "unitedstates": "us",
    "us": "us",
    "usa": "us",
}
_SERPAPI_AGGREGATE_MARKETS = {"eu", "europe", "europeanunion", "欧洲", "欧盟", "欧洲联盟"}
_SERPAPI_DEFAULT_CALL_BUDGET = 70
_SERPAPI_CALL_COUNT = 0
_SERPAPI_CALL_LOCK = threading.Lock()
_EBAY_MARKETPLACE_COUNTRIES = {
    "EBAY_DE": "de",
    "EBAY_ES": "es",
    "EBAY_FR": "fr",
    "EBAY_GB": "gb",
    "EBAY_IT": "it",
    "EBAY_US": "us",
}


def _serpapi_country_code(value: str | None) -> str | None:
    normalized = re.sub(r"[^a-z]", "", str(value or "").casefold())
    return _SERPAPI_COUNTRY_ALIASES.get(normalized)


def _is_aggregate_market(value: str | None) -> bool:
    return str(value or "").strip().casefold() in _SERPAPI_AGGREGATE_MARKETS


def _serpapi_call_budget() -> int:
    """Return the process-wide request budget configured for SerpApi."""

    raw_budget = os.getenv("SERPAPI_CALL_BUDGET", str(_SERPAPI_DEFAULT_CALL_BUDGET)).strip()
    try:
        return max(0, int(raw_budget))
    except (TypeError, ValueError):
        return _SERPAPI_DEFAULT_CALL_BUDGET


def _reserve_serpapi_call() -> bool:
    """Atomically reserve one SerpApi request, if the process budget allows it."""

    global _SERPAPI_CALL_COUNT
    with _SERPAPI_CALL_LOCK:
        if _SERPAPI_CALL_COUNT >= _serpapi_call_budget():
            return False
        _SERPAPI_CALL_COUNT += 1
        return True


def reset_serpapi_budget_for_tests() -> None:
    """Reset the process-wide SerpApi budget counter for deterministic tests."""

    global _SERPAPI_CALL_COUNT
    with _SERPAPI_CALL_LOCK:
        _SERPAPI_CALL_COUNT = 0


def _item_market_matches_country(item: dict[str, Any], country_code: str) -> bool:
    """Require a generic API response to declare the requested market."""

    for key in ("country", "countryCode", "marketCountry", "market", "locationCountry"):
        value = item.get(key)
        if isinstance(value, str) and _serpapi_country_code(value) == country_code:
            return True
    return False


def _serpapi_market(
    country: str | None,
    *,
    fallback_country: str | None,
    language_override: str | None = None,
    domain_override: str | None = None,
) -> tuple[str, str, str]:
    """Map a user country to supported documented Google Shopping params.

    Unknown values intentionally fall back to the configured default instead of
    being forwarded as arbitrary third-party API parameters.
    """

    code = _serpapi_country_code(country) or _serpapi_country_code(fallback_country) or "de"
    gl, default_hl, default_domain = _SERPAPI_MARKETS[code]
    language = (language_override or "").strip().casefold()
    if not re.fullmatch(r"[a-z]{2,5}", language):
        language = default_hl
    domain = (domain_override or "").strip().casefold()
    if not re.fullmatch(r"google\.[a-z.]{2,20}", domain):
        domain = default_domain
    return gl, language, domain


def _env_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _parse_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return numeric if 0.0 < numeric < 1000000 else None
    if not isinstance(value, str):
        return None

    text = value.strip().replace("\u00a0", " ")
    match = re.search(r"([0-9][0-9.,\s]*)", text)
    if not match:
        return None
    numeric = match.group(1).replace(" ", "")
    dot = numeric.rfind(".")
    comma = numeric.rfind(",")
    if dot != -1 and comma != -1:
        if dot > comma:
            numeric = numeric.replace(",", "")
        else:
            numeric = numeric.replace(".", "").replace(",", ".")
    elif comma != -1:
        parts = numeric.split(",")
        numeric = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) == 2 else numeric.replace(",", "")
    elif dot != -1:
        parts = numeric.split(".")
        if len(parts) > 2:
            numeric = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) == 2 else "".join(parts)
        elif len(parts) == 2 and len(parts[-1]) == 3:
            numeric = "".join(parts)
    try:
        parsed = float(numeric)
    except ValueError:
        return None
    return parsed if 0.0 < parsed < 1000000 else None


def _first_text(item: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_mapping(item: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _as_int(value: object) -> int:
    try:
        return max(0, int(float(str(value))))
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        return max(0.0, float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0.0


def _delivery_days(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else None


def _has_eur_marker(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"(?:€|\bEUR\b|\bEuro\b)", value, flags=re.IGNORECASE))


def normalize_marketplace_item(
    item: dict[str, Any],
    *,
    provider: str,
    platform: str,
) -> dict[str, Any] | None:
    """Normalize a marketplace item into the existing quote-comparison contract.

    Prices are accepted only with explicit EUR evidence: a currency field,
    a ``unitPriceEur`` field, or an EUR/€ marker on the price label. The
    system intentionally does not infer currencies or invent conversion.
    """

    price_value = item.get("unitPriceEur")
    price_currency = _first_text(item, ("currency", "priceCurrency", "price_currency"))
    raw_price = item.get("price")
    raw_label = _first_text(item, ("unitLabel", "priceLabel", "displayPrice"))
    has_unit_price_eur = item.get("unitPriceEur") is not None
    if isinstance(raw_price, dict):
        price_value = raw_price.get("value", raw_price.get("amount", price_value))
        price_currency = str(raw_price.get("currency") or price_currency or "")
    elif raw_price is not None and price_value is None:
        price_value = raw_price

    price = _parse_number(price_value)
    explicit_eur = (
        has_unit_price_eur
        or price_currency.upper() == "EUR"
        or _has_eur_marker(raw_price)
        or _has_eur_marker(raw_label)
    )
    if (
        price is None
        or not explicit_eur
        or (price_currency and price_currency.upper() != "EUR")
    ):
        return None

    title = _first_text(item, ("title", "product", "productName", "name"))
    url = _first_text(item, ("itemWebUrl", "url", "productUrl", "link", "itemAffiliateWebUrl"))
    if not title or not url:
        return None

    seller = _first_mapping(item, ("seller", "merchant", "shop"))
    vendor = _first_text(item, ("vendor", "sellerName", "shopName", "merchantName"))
    vendor = vendor or _first_text(seller, ("username", "name", "displayName")) or platform
    delivery_label = _first_text(item, ("deliveryLabel", "delivery", "estimatedDelivery", "shippingLabel"))
    shipping_options = item.get("shippingOptions")
    if not delivery_label and isinstance(shipping_options, list) and shipping_options:
        first_shipping = shipping_options[0] if isinstance(shipping_options[0], dict) else {}
        delivery_label = _first_text(first_shipping, ("shippingCostType", "deliveryDate", "minEstimatedDeliveryDate"))
    delivery_days = item.get("deliveryDays")
    if delivery_days is None and isinstance(shipping_options, list) and shipping_options:
        first_shipping = shipping_options[0] if isinstance(shipping_options[0], dict) else {}
        delivery_days = first_shipping.get("deliveryDays")

    raw_identifier = _first_text(item, ("itemId", "id", "sku", "legacyItemId")) or url
    identifier = hashlib.sha1(f"{provider}:{raw_identifier}".encode("utf-8")).hexdigest()[:16]
    evidence = _first_text(item, ("shortDescription", "description", "subtitle"))

    return {
        "id": f"marketplace-{provider}-{identifier}",
        "vendor": vendor,
        "platform": platform,
        "product": title,
        "category": item.get("category") or "web",
        "description": evidence,
        "matchScore": 80,
        "unitPriceEur": price,
        "unitLabel": raw_label or f"€ {price:.2f}",
        "packageLabel": _first_text(item, ("packageLabel", "packSize", "quantity", "size")),
        "deliveryDays": _delivery_days(delivery_days),
        "deliveryLabel": delivery_label or "需确认交期",
        # A marketplace item rarely exposes its buyer-specific payment terms.
        # Never turn an absent field into a factual claim such as prepayment.
        "paymentTerm": item.get("paymentTerm") or "unknown",
        "paymentLabel": item.get("paymentLabel") or "需确认付款方式",
        "deliveryMethod": item.get("deliveryMethod") or "需确认配送方式",
        "rating": _as_float(item.get("rating") or item.get("feedbackScore") or 0),
        "reviews": _as_int(item.get("reviews") or item.get("reviewCount") or item.get("feedbackCount") or 0),
        "source": "web",
        "sourceDetail": f"marketplace:{provider}",
        "sourceUrls": [url],
        "evidenceSnippets": [line for line in (f"[{platform}] € {price:.2f}", evidence[:300]) if line],
        "priceConfidence": "api",
    }


class MarketplaceQuoteProvider(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def search(self, query: str, *, country: str | None = None, limit: int = 8) -> list[dict[str, Any]]: ...


def _serpapi_explicit_eur_price(item: dict[str, Any]) -> tuple[float, str] | None:
    """Return a SerpApi price only when its response explicitly identifies EUR."""

    raw_price = item.get("price")
    display_price = raw_price.strip() if isinstance(raw_price, str) else ""
    currency = _first_text(item, ("currency", "price_currency", "priceCurrency"))
    price_value: object = item.get("extracted_price", item.get("extractedPrice"))
    if isinstance(raw_price, dict):
        price_value = raw_price.get("value", raw_price.get("amount", price_value))
        currency = str(raw_price.get("currency") or currency or "")
        display_price = _first_text(raw_price, ("display", "label", "formatted"))
    elif price_value is None:
        price_value = raw_price

    price_has_eur_marker = bool(re.search(r"(?:€|\bEUR\b|\bEuro\b)", display_price, flags=re.IGNORECASE))
    if currency.upper() != "EUR" and not price_has_eur_marker:
        return None
    # A declared non-EUR currency always wins over a formatted display string;
    # this prevents accidental conversion or ambiguous data from entering EUR
    # comparisons.
    if currency and currency.upper() != "EUR":
        return None
    price = _parse_number(price_value)
    if price is None:
        return None
    return price, display_price or f"€ {price:.2f}"


def _serpapi_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("link", "product_link", "productLink", "url", "product_url"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        url = value.strip()
        if url.startswith(("https://", "http://")) and url not in urls:
            urls.append(url)
    return urls


def _serpapi_extensions(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("delivery", "shipping", "tag", "snippet"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    extensions = item.get("extensions")
    if isinstance(extensions, list):
        values.extend(value.strip() for value in extensions if isinstance(value, str) and value.strip())
    return list(dict.fromkeys(values))


def _normalize_serpapi_item(item: dict[str, Any]) -> dict[str, Any] | None:
    explicit_price = _serpapi_explicit_eur_price(item)
    if explicit_price is None:
        return None
    price, display_price = explicit_price
    title = _first_text(item, ("title", "product", "name"))
    source_urls = _serpapi_urls(item)
    if not title or not source_urls:
        return None

    seller = _first_text(item, ("source", "seller", "merchant", "store", "seller_name"))
    evidence_parts = _serpapi_extensions(item)
    delivery = _first_text(item, ("delivery", "shipping", "shipping_info"))
    normalized = normalize_marketplace_item(
        {
            "id": _first_text(item, ("product_id", "productId", "id")) or source_urls[0],
            "title": title,
            "url": source_urls[0],
            "price": {"value": price, "currency": "EUR"},
            "unitLabel": display_price,
            "sellerName": seller,
            "delivery": delivery,
            "deliveryDays": _delivery_days(delivery),
            "rating": item.get("rating"),
            "reviews": item.get("reviews", item.get("review_count")),
            "description": " | ".join(evidence_parts),
        },
        provider="serpapi",
        platform="Google Shopping (SerpApi)",
    )
    if normalized is None:
        return None
    normalized["sourceUrls"] = source_urls
    # Shopping APIs often return a pack size only in extensions. Preserve it
    # separately so a buyer can distinguish pack price from unit price.
    if evidence_parts:
        normalized["packageLabel"] = " | ".join(evidence_parts[:3])
        normalized["quoteConditions"] = " | ".join(evidence_parts[:4])
    normalized["evidenceSnippets"] = list(
        dict.fromkeys(
            [
                f"[Google Shopping (SerpApi)] {display_price}",
                *([f"Seller: {seller}"] if seller else []),
                *evidence_parts[:4],
            ]
        )
    )
    return normalized


class SerpApiGoogleShoppingProvider:
    """Optional Google Shopping search through the authorised SerpApi API.

    SerpApi is intentionally queried only when a private ``SERPAPI_API_KEY``
    is configured. The user-facing country is allowlisted and mapped to the
    documented ``gl``, ``hl``, and ``google_domain`` Google Shopping params.
    Results without an explicit EUR price are excluded instead of converted.
    """

    name = "serpapi"
    platform = "Google Shopping (SerpApi)"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_country: str | None = None,
        language: str | None = None,
        google_domain: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("SERPAPI_API_KEY", "")).strip()
        self.default_country = default_country if default_country is not None else os.getenv("SERPAPI_COUNTRY", "de")
        self.language = language if language is not None else os.getenv("SERPAPI_LANGUAGE", "")
        self.google_domain = google_domain if google_domain is not None else os.getenv("SERPAPI_GOOGLE_DOMAIN", "")
        self.endpoint = (endpoint or "https://serpapi.com/search.json").strip()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else _env_float("SERPAPI_TIMEOUT_SECONDS", 8.0)
        self.cache_ttl_seconds = cache_ttl_seconds if cache_ttl_seconds is not None else _env_float("SERPAPI_CACHE_TTL_SECONDS", 180.0)
        self._transport = transport
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.endpoint.startswith(("https://", "http://"))

    async def search(self, query: str, *, country: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        if not self.enabled or not query.strip():
            return []
        requested_market = country.strip() if isinstance(country, str) and country.strip() else self.default_country
        # Google Shopping is a single-country service. Do not silently turn an
        # unsupported country or an aggregate request such as Europe/EU into
        # the configured German default.
        if _is_aggregate_market(requested_market) or _serpapi_country_code(requested_market) is None:
            return []
        bounded_limit = max(1, min(limit, 20))
        gl, hl, google_domain = _serpapi_market(
            country,
            fallback_country=self.default_country,
            language_override=self.language,
            domain_override=self.google_domain,
        )
        cache_key = f"{query.strip().casefold()}|{gl}|{hl}|{google_domain}|{bounded_limit}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.time():
            return [dict(item) for item in cached[1]]

        params = {
            "engine": "google_shopping",
            "q": query.strip(),
            "api_key": self.api_key,
            "gl": gl,
            "hl": hl,
            "google_domain": google_domain,
            "num": str(bounded_limit),
        }
        # Cache lookup intentionally happens before this reservation so a
        # fresh result never consumes the process-wide network budget.
        if not _reserve_serpapi_call():
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.get(self.endpoint, params=params, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return []

        raw_results = payload.get("shopping_results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            return []
        results = [
            normalized
            for raw in raw_results
            if isinstance(raw, dict)
            if (normalized := _normalize_serpapi_item(raw)) is not None
        ][:bounded_limit]
        if results:
            self._cache[cache_key] = (time.time() + self.cache_ttl_seconds, [dict(item) for item in results])
        return results


class EbayBrowseApiProvider:
    """Official eBay Browse API provider, enabled only with client credentials."""

    name = "ebay"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        marketplace_id: str | None = None,
        oauth_url: str | None = None,
        browse_api_base: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = (client_id if client_id is not None else os.getenv("EBAY_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret if client_secret is not None else os.getenv("EBAY_CLIENT_SECRET", "")).strip()
        self.marketplace_id = (marketplace_id or os.getenv("EBAY_MARKETPLACE_ID", "EBAY_DE")).strip() or "EBAY_DE"
        self.oauth_url = (oauth_url or os.getenv("EBAY_OAUTH_URL", "https://api.ebay.com/identity/v1/oauth2/token")).rstrip("/")
        self.browse_api_base = (browse_api_base or os.getenv("EBAY_BROWSE_API_BASE_URL", "https://api.ebay.com/buy/browse/v1")).rstrip("/")
        self._transport = transport
        self._access_token = ""
        self._access_token_expires_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token
        if not self.enabled:
            return ""

        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        timeout = _env_float("MARKETPLACE_API_TIMEOUT_SECONDS", 4.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.post(
                    self.oauth_url,
                    data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Authorization": f"Basic {credentials}",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return ""

        token = str(payload.get("access_token") or "").strip()
        if not token:
            return ""
        expires_in = _as_int(payload.get("expires_in") or 300)
        self._access_token = token
        self._access_token_expires_at = time.time() + max(30, expires_in - 30)
        return token

    async def search(self, query: str, *, country: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        if country and country.strip():
            requested_country = _serpapi_country_code(country)
            marketplace_country = _EBAY_MARKETPLACE_COUNTRIES.get(self.marketplace_id.upper())
            # The configured Browse marketplace is authoritative. For example,
            # EBAY_DE must not become an implicit Polish/German fallback.
            if (
                _is_aggregate_market(country)
                or requested_country is None
                or marketplace_country is None
                or requested_country != marketplace_country
            ):
                return []
        token = await self._get_access_token()
        if not token or not query.strip():
            return []

        timeout = _env_float("MARKETPLACE_API_TIMEOUT_SECONDS", 4.0)
        params = {"q": query.strip(), "limit": str(max(1, min(limit, 20)))}
        # Browse API markets are explicit; preserve the buyer's country only as
        # a delivery preference where possible, without rejecting valid offers.
        if country and country.casefold() in {"germany", "de", "deutschland"}:
            params["filter"] = "deliveryCountry:DE"
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.get(
                    f"{self.browse_api_base}/item_summary/search",
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}",
                        "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return []

        items = payload.get("itemSummaries") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [
            normalized
            for raw in items
            if isinstance(raw, dict)
            if (normalized := normalize_marketplace_item(raw, provider=self.name, platform="eBay Browse API")) is not None
        ]


class GenericMarketplaceConnector:
    """Configurable read-only JSON connector for an authorised marketplace API.

    The connector is intentionally generic so an organisation can add an
    approved vendor/marketplace API without changing the quote pipeline. It
    only sends a search request, never mutates remote data.
    """

    name = "generic"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        method: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = (endpoint if endpoint is not None else os.getenv("MARKETPLACE_API_URL", "")).strip()
        self.api_key = (api_key if api_key is not None else os.getenv("MARKETPLACE_API_KEY", "")).strip()
        self.method = (method or os.getenv("MARKETPLACE_API_METHOD", "GET")).strip().upper()
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return self.endpoint.startswith(("https://", "http://")) and self.method in {"GET", "POST"}

    async def search(self, query: str, *, country: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        if not self.enabled or not query.strip():
            return []

        requested_country = _serpapi_country_code(country) if country and country.strip() else None
        if country and country.strip() and (_is_aggregate_market(country) or requested_country is None):
            return []
        # A generic connector has no intrinsic marketplace geography. When a
        # user explicitly selects a country, accept only rows that declare the
        # same market. Otherwise an approved API could return offers from a
        # different market and wrongly satisfy the fast-path threshold.
        require_response_market = bool(requested_country)

        payload = {"query": query.strip(), "limit": max(1, min(limit, 20)), "country": country or ""}
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers[os.getenv("MARKETPLACE_API_KEY_HEADER", "Authorization")] = (
                f"{os.getenv('MARKETPLACE_API_KEY_PREFIX', 'Bearer').strip()} {self.api_key}".strip()
            )
        timeout = _env_float("MARKETPLACE_API_TIMEOUT_SECONDS", 4.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                if self.method == "POST":
                    response = await client.post(self.endpoint, json=payload, headers=headers)
                else:
                    response = await client.get(self.endpoint, params=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return []

        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = next(
                (body[key] for key in ("items", "results", "data", "itemSummaries", "offers") if isinstance(body.get(key), list)),
                [],
            )
        else:
            items = []
        return [
            normalized
            for raw in items
            if isinstance(raw, dict)
            if not require_response_market or _item_market_matches_country(raw, requested_country or "")
            if (normalized := normalize_marketplace_item(raw, provider=self.name, platform="Marketplace API")) is not None
        ]


class MarketplaceSearchLayer:
    """Merge optional marketplace providers and cache only successful searches."""

    def __init__(
        self,
        providers: list[MarketplaceQuoteProvider] | None = None,
        *,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        # SerpApi is the primary optional source because Google Shopping
        # returns a broadly comparable product result shape. It remains inert
        # until a private API key is configured.
        self.providers = providers if providers is not None else [
            SerpApiGoogleShoppingProvider(),
            EbayBrowseApiProvider(),
            GenericMarketplaceConnector(),
        ]
        self.cache_ttl_seconds = cache_ttl_seconds if cache_ttl_seconds is not None else _env_float("MARKETPLACE_CACHE_TTL_SECONDS", 120.0)
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @property
    def enabled(self) -> bool:
        return any(provider.enabled for provider in self.providers)

    async def search(
        self,
        query: str,
        *,
        country: str | None = None,
        limit: int = 8,
        min_priced_results: int = 3,
    ) -> list[dict[str, Any]]:
        if not self.enabled or not query.strip():
            return []
        cache_key = f"{query.strip().casefold()}|{(country or '').casefold()}|{limit}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.time():
            return [dict(item) for item in cached[1]]

        merged: dict[str, dict[str, Any]] = {}
        for provider in self.providers:
            if not provider.enabled:
                continue
            try:
                results = await provider.search(query, country=country, limit=limit)
            except Exception:
                results = []
            for item in results:
                urls = item.get("sourceUrls") or []
                key = str(urls[0] if urls else item.get("id"))
                if key and key not in merged:
                    merged[key] = item
            collapsed = self._collapse_duplicate_sellers(list(merged.values()))
            if sum(1 for item in collapsed if item.get("unitPriceEur") is not None) >= min_priced_results:
                break

        output = self._collapse_duplicate_sellers(list(merged.values()))[:limit]
        # Avoid the Idealo failure mode: a transient timeout must not poison a
        # later search with an empty cached response.
        if output:
            self._cache[cache_key] = (time.time() + self.cache_ttl_seconds, [dict(item) for item in output])
        return output

    @staticmethod
    def _seller_key(item: dict[str, Any]) -> str:
        """Return a stable seller key for marketplace offers.

        Search APIs commonly return one row per seller listing, so the same
        merchant can appear many times with different tracking URLs. Only
        marketplace rows with a real seller name are collapsed; generic test
        or provider-only rows remain independent offers.
        """

        if not str(item.get("sourceDetail") or "").startswith("marketplace:"):
            return ""
        vendor = str(item.get("vendor") or "").strip().casefold()
        if vendor in {"ebay seller", "shopping seller", "approved marketplace seller"}:
            return ""
        key = re.sub(r"[^a-z0-9]+", "", vendor)
        if key in {"", "googleshoppingserpapi", "marketplaceapi", "ebaybrowseapi"}:
            return ""
        return key

    @classmethod
    def _collapse_duplicate_sellers(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep one comparison row per marketplace seller while preserving offers.

        The cheapest explicit EUR offer becomes the visible row. Other offers
        are retained in ``alternateOffers`` so an expandable result view can
        show pack sizes, refurbished variants, or different conditions without
        flooding the first comparison table with repeated seller rows.
        """

        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            key = cls._seller_key(item)
            if not key:
                order.append(("", item))
                continue
            if key not in groups:
                groups[key] = []
                order.append((key, item))
            groups[key].append(item)

        collapsed: list[dict[str, Any]] = []
        for key, first in order:
            if not key:
                collapsed.append(first)
                continue
            offers = groups[key]
            if len(offers) == 1:
                collapsed.append(offers[0])
                continue
            representative = min(
                offers,
                key=lambda item: (
                    item.get("unitPriceEur") is None,
                    float(item.get("unitPriceEur") or float("inf")),
                ),
            )
            row = dict(representative)
            row["offerCount"] = len(offers)
            row["alternateOffers"] = [
                {
                    "id": offer.get("id"),
                    "product": offer.get("product"),
                    "unitPriceEur": offer.get("unitPriceEur"),
                    "unitLabel": offer.get("unitLabel"),
                    "packageLabel": offer.get("packageLabel"),
                    "sourceUrls": offer.get("sourceUrls") or [],
                    "evidenceSnippets": offer.get("evidenceSnippets") or [],
                }
                for offer in offers
                if offer is not representative
            ]
            row["quoteConditions"] = " | ".join(
                value
                for value in [
                    str(row.get("quoteConditions") or "").strip(),
                    f"同一卖家共 {len(offers)} 个报价，已显示最低明确 EUR 价",
                ]
                if value
            )
            collapsed.append(row)
        return collapsed


def reset_marketplace_cache_for_tests(layer: MarketplaceSearchLayer) -> None:
    """Explicit test hook; production code never needs to clear this cache."""

    layer._cache.clear()

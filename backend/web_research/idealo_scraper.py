"""Idealo.de public price-comparison search for standard-product quotes.

The scraper opens Idealo's normal public search-result URL and first extracts
the product cards already present on that page.  It only visits a small number
of product detail pages when those cards do not expose a usable price.  This
keeps the browser work bounded and lets the ordinary web-search fallback take
over when Idealo is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IDEALO_SEARCH_ENDPOINT = "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html"
_CACHE_TTL_SECONDS = 300
_MAX_RESULT_LIMIT = 12
_MAX_DETAIL_PAGES = 2
_PAGE_LOAD_TIMEOUT_SECONDS = 20
_RESULT_WAIT_SECONDS = 8
_DETAIL_WAIT_SECONDS = 6
_MAX_CONCURRENT_BROWSERS = 1

_RESULT_LINK_SELECTORS = (
    "a.productCard-link",
    "a[href*='/preisvergleich/OffersOfProduct/']",
)
_RESULT_WAIT_SELECTORS = _RESULT_LINK_SELECTORS
_DETAIL_WAIT_SELECTORS = (
    ".productOffers-listItem",
    ".offerList-item",
    "div[data-offer-id]",
)
_PRICE_SELECTORS = (
    "[data-testid*='price']",
    "[class*='price']",
    "[class*='Price']",
)
_SHOP_SELECTORS = (
    "[data-testid*='shop']",
    "[class*='shop']",
    "[class*='Shop']",
    "[class*='merchant']",
    "[class*='seller']",
)
_DELIVERY_SELECTORS = (
    "[data-testid*='delivery']",
    "[class*='delivery']",
    "[class*='Delivery']",
    "[class*='shipping']",
    "[class*='Shipping']",
)
_RATING_SELECTORS = (
    "[class*='rating']",
    "[class*='Rating']",
    "[class*='stars']",
    "[class*='Stars']",
)
_REVIEW_SELECTORS = (
    "[class*='review']",
    "[class*='Review']",
    "[class*='ratingCount']",
    "[class*='numberOfRatings']",
)

# Cache only non-empty results. A timeout or an unavailable browser must never
# make a future request look like there are no matching offers.
_SCRAPE_CACHE: dict[str, dict[str, Any]] = {}
# This is deliberately a thread-level semaphore instead of an asyncio one.
# ``asyncio.wait_for`` cannot cancel a Selenium call already running in a
# worker thread. Keeping the permit in that thread prevents a timed-out caller
# from freeing capacity early and starting unlimited Chrome processes.
_BROWSER_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_BROWSERS)


def _normalise_search_term(search_term: str) -> str:
    return " ".join(str(search_term or "").split())[:160]


def build_idealo_search_url(search_term: str) -> str:
    """Return Idealo's public search-result URL for a product query."""

    return f"{IDEALO_SEARCH_ENDPOINT}?{urlencode({'q': _normalise_search_term(search_term)})}"


async def search_idealo(search_term: str, limit: int = 5, timeout: float = 45) -> list[dict[str, Any]]:
    """Return public Idealo product/offer candidates for a product search.

    Selenium runs in a worker thread so the FastAPI event loop remains free.
    The call is bounded by ``timeout`` and falls back to an empty list on a
    normal browser, network, or selector failure.
    """

    query = _normalise_search_term(search_term)
    if not query:
        return []
    try:
        bounded_limit = max(1, min(_MAX_RESULT_LIMIT, int(limit)))
    except (TypeError, ValueError):
        bounded_limit = 5
    cache_key = f"{query.casefold()}:{bounded_limit}"
    cached = _SCRAPE_CACHE.get(cache_key)
    if cached and time.time() - cached.get("_ts", 0) < _CACHE_TTL_SECONDS:
        logger.info("Idealo cache hit: query=%r limit=%s", query, bounded_limit)
        return [dict(item) for item in cached["results"]]
    if cached:
        _SCRAPE_CACHE.pop(cache_key, None)

    try:
        timeout_seconds = max(1.0, float(timeout))
    except (TypeError, ValueError):
        timeout_seconds = 20.0
    deadline = time.monotonic() + timeout_seconds
    logger.info("Idealo search started: query=%r limit=%s timeout=%ss", query, bounded_limit, timeout_seconds)
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_scrape_idealo_with_permit, query, bounded_limit, deadline),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        # The worker cannot be force-killed safely while Selenium/Chrome is in
        # a blocking call. It retains its permit until its deadline/cleanup, so
        # later callers cannot launch extra browsers during that residual work.
        logger.warning(
            "Idealo caller timed out after %ss: query=%r; worker retains browser permit until cleanup",
            timeout_seconds,
            query,
        )
        results = []
    except Exception as exc:
        logger.warning("Idealo search failed: query=%r error=%s", query, exc)
        results = []

    if results:
        _SCRAPE_CACHE[cache_key] = {"_ts": time.time(), "results": [dict(item) for item in results]}
        logger.info("Idealo search finished: query=%r candidates=%s", query, len(results))
    else:
        logger.info("Idealo search yielded no candidates: query=%r", query)
    return results


def reset_idealo_cache_for_tests() -> None:
    """Clear the in-memory cache for deterministic tests."""

    _SCRAPE_CACHE.clear()


def _remaining_seconds(deadline: float | None) -> float:
    return float("inf") if deadline is None else max(0.0, deadline - time.monotonic())


def _driver_timeout_seconds(deadline: float | None, maximum: float) -> float:
    """Return a page/wait timeout that never extends past the request deadline."""

    if deadline is None:
        return maximum
    remaining = _remaining_seconds(deadline)
    if remaining <= 0:
        return 0.0
    return min(maximum, remaining)


def _scrape_idealo_with_permit(search_term: str, limit: int, deadline: float) -> list[dict[str, Any]]:
    """Acquire global browser capacity inside the non-cancellable worker thread."""

    remaining = _remaining_seconds(deadline)
    if remaining <= 0:
        return []
    if not _BROWSER_SEMAPHORE.acquire(timeout=remaining):
        logger.info("Idealo browser capacity unavailable before deadline: query=%r", search_term)
        return []
    try:
        if _remaining_seconds(deadline) <= 0:
            return []
        return _scrape_idealo_sync(search_term, limit, deadline=deadline)
    finally:
        _BROWSER_SEMAPHORE.release()


def _scrape_idealo_sync(search_term: str, limit: int, *, deadline: float | None = None) -> list[dict[str, Any]]:
    """Use a normal browser session to inspect public Idealo result pages."""

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        logger.info("Idealo skipped because Selenium is not installed")
        return []

    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1440,1200")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")

        # Selenium Manager, included with Selenium, resolves a matching Chrome
        # driver. No webdriver-manager download or browser-masquerading logic is
        # needed for this public result-page search.
        driver = webdriver.Chrome(options=options)

        search_url = build_idealo_search_url(search_term)
        logger.info("Idealo opening public result page: %s", search_url)
        page_timeout = _driver_timeout_seconds(deadline, _PAGE_LOAD_TIMEOUT_SECONDS)
        if page_timeout <= 0:
            return []
        driver.set_page_load_timeout(page_timeout)
        driver.get(search_url)
        result_wait_timeout = _driver_timeout_seconds(deadline, _RESULT_WAIT_SECONDS)
        if result_wait_timeout <= 0:
            return []
        result_wait = WebDriverWait(driver, result_wait_timeout)
        if not _wait_for_any_selector(driver, result_wait, By.CSS_SELECTOR, _RESULT_WAIT_SELECTORS):
            logger.info("Idealo result page had no known product-card selector; parsing available DOM")

        direct_results, detail_urls = parse_idealo_result_page(driver.page_source, search_term, limit)
        logger.info(
            "Idealo result page parsed: direct_candidates=%s detail_urls=%s",
            len(direct_results),
            len(detail_urls),
        )

        candidates = direct_results[:limit]
        missing_price_urls = [
            url
            for candidate in candidates
            if candidate.get("unitPriceEur") is None
            for url in candidate.get("sourceUrls", [])[:1]
        ]
        detail_targets = missing_price_urls or ([] if candidates else detail_urls)
        detail_targets = _dedupe_urls(detail_targets)[:_MAX_DETAIL_PAGES]

        if detail_targets:
            logger.info("Idealo opening up to %s detail pages to fill missing prices", len(detail_targets))
        for detail_url in detail_targets:
            try:
                page_timeout = _driver_timeout_seconds(deadline, _PAGE_LOAD_TIMEOUT_SECONDS)
                if page_timeout <= 0:
                    logger.info("Idealo deadline reached before detail page: query=%r", search_term)
                    break
                driver.set_page_load_timeout(page_timeout)
                driver.get(detail_url)
                detail_wait_timeout = _driver_timeout_seconds(deadline, _DETAIL_WAIT_SECONDS)
                if detail_wait_timeout <= 0:
                    break
                detail_wait = WebDriverWait(driver, detail_wait_timeout)
                _wait_for_any_selector(driver, detail_wait, By.CSS_SELECTOR, _DETAIL_WAIT_SELECTORS)
                detail_candidates = parse_idealo_offer_page(driver.page_source, detail_url, search_term, limit=3)
                candidates = _merge_detail_candidates(candidates, detail_url, detail_candidates, limit)
            except Exception as exc:
                logger.info("Idealo detail page unavailable: url=%s error=%s", detail_url, exc)

        return candidates[:limit]
    except Exception as exc:
        logger.warning("Idealo browser scrape failed: query=%r error=%s", search_term, exc)
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _wait_for_any_selector(driver: Any, wait: Any, by: Any, selectors: tuple[str, ...]) -> bool:
    """Wait explicitly until one of the expected public-page selectors exists."""

    def has_any_selector(active_driver: Any) -> bool:
        for selector in selectors:
            try:
                if active_driver.find_elements(by, selector):
                    return True
            except Exception:
                continue
        return False

    try:
        return bool(wait.until(has_any_selector))
    except Exception:
        return False


def parse_idealo_result_page(html: str, search_term: str, limit: int = 5) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract product cards and detail URLs from a public Idealo result page.

    Keeping this parser independent from Selenium makes its selector behavior
    directly testable and allows the browser code to remain small.
    """

    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[dict[str, Any]] = []
    detail_urls: list[str] = []
    seen_urls: set[str] = set()
    selector = ", ".join(_RESULT_LINK_SELECTORS)

    for anchor in soup.select(selector):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        url = urljoin("https://www.idealo.de", href)
        parsed = urlparse(url)
        if not parsed.hostname or not parsed.hostname.endswith("idealo.de"):
            continue
        if "/preisvergleich/" not in parsed.path or url in seen_urls:
            continue
        seen_urls.add(url)
        detail_urls.append(url)

        card = _result_container(anchor)
        title = _first_text((anchor, *_select_nodes(card, ("h1", "h2", "h3", "[class*='title']", "[class*='Title']"))))
        title = title or _normalise_search_term(search_term)
        price_text = _first_price_text(card)
        price_eur = _parse_eur(price_text)
        shop = _first_selector_text(card, _SHOP_SELECTORS) or "Idealo Preisvergleich"
        delivery_text = _first_selector_text(card, _DELIVERY_SELECTORS)
        rating = _parse_rating(_first_selector_text(card, _RATING_SELECTORS))
        reviews = _parse_count(_first_selector_text(card, _REVIEW_SELECTORS))

        candidates.append(
            _idealo_candidate(
                product=title,
                vendor=shop,
                url=url,
                price_eur=price_eur,
                price_text=price_text,
                delivery_text=delivery_text,
                rating=rating,
                reviews=reviews,
                evidence_origin="search",
            )
        )
        if len(candidates) >= max(1, limit):
            break

    return candidates, detail_urls


def parse_idealo_offer_page(html: str, url: str, search_term: str, limit: int = 3) -> list[dict[str, Any]]:
    """Extract a limited number of priced offers from an Idealo product page."""

    soup = BeautifulSoup(html or "", "html.parser")
    product_name = _first_selector_text(soup, ("h1.oopStage-title", "h1 span", "h1")) or _normalise_search_term(search_term)
    rows = soup.select(", ".join(_DETAIL_WAIT_SELECTORS))
    candidates: list[dict[str, Any]] = []

    for row in rows:
        price_text = _first_price_text(row)
        price_eur = _parse_eur(price_text)
        if price_eur is None:
            continue
        shop = _first_selector_text(row, _SHOP_SELECTORS) or "Idealo Shop"
        delivery_text = _first_selector_text(row, _DELIVERY_SELECTORS)
        rating = _parse_rating(_first_selector_text(row, _RATING_SELECTORS))
        reviews = _parse_count(_first_selector_text(row, _REVIEW_SELECTORS))
        shipping = _first_attribute_or_text(row, ("[class*='shipping']", "[class*='Shipping']"), "title")
        candidates.append(
            {
                **_idealo_candidate(
                    product=product_name,
                    vendor=shop,
                    url=url,
                    price_eur=price_eur,
                    price_text=price_text,
                    delivery_text=delivery_text,
                    rating=rating,
                    reviews=reviews,
                    evidence_origin="offer",
                ),
                "shipping": shipping,
            }
        )
        if len(candidates) >= max(1, limit):
            break
    return candidates


def _merge_detail_candidates(
    candidates: list[dict[str, Any]],
    detail_url: str,
    detail_candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Enrich an unpriced result card, or add details only when room remains."""

    if not detail_candidates:
        return candidates
    best_detail = min(detail_candidates, key=lambda item: item.get("unitPriceEur") or float("inf"))
    for index, candidate in enumerate(candidates):
        source_url = str((candidate.get("sourceUrls") or [""])[0])
        if source_url != detail_url:
            continue
        if candidate.get("unitPriceEur") is not None:
            return candidates
        evidence = _dedupe_texts([
            *(candidate.get("evidenceSnippets") or []),
            *(best_detail.get("evidenceSnippets") or []),
        ])
        candidates[index] = {
            **candidate,
            "vendor": best_detail.get("vendor") or candidate.get("vendor"),
            "unitPriceEur": best_detail.get("unitPriceEur"),
            "unitLabel": best_detail.get("unitLabel") or candidate.get("unitLabel"),
            "deliveryLabel": best_detail.get("deliveryLabel") or candidate.get("deliveryLabel"),
            "rating": best_detail.get("rating") or candidate.get("rating", 0),
            "reviews": best_detail.get("reviews") or candidate.get("reviews", 0),
            "priceConfidence": "extracted",
            "matchScore": max(candidate.get("matchScore", 0), best_detail.get("matchScore", 0)),
            "evidenceSnippets": evidence,
        }
        return candidates

    remaining = max(0, limit - len(candidates))
    return [*candidates, *detail_candidates[:remaining]]


def _idealo_candidate(
    *,
    product: str,
    vendor: str,
    url: str,
    price_eur: float | None,
    price_text: str,
    delivery_text: str,
    rating: float,
    reviews: int,
    evidence_origin: str,
) -> dict[str, Any]:
    label = price_text or (f"€ {price_eur:.2f}" if price_eur is not None else "需人工核价")
    evidence = f"[Idealo {evidence_origin}] {product}: {label}"
    return {
        "id": f"idealo-{abs(hash(f'{url}|{vendor}')) % 10_000_000}",
        "product": product,
        "vendor": vendor,
        "platform": "idealo.de",
        "unitPriceEur": price_eur,
        "unitLabel": label,
        "deliveryDays": None,
        "deliveryLabel": delivery_text or "需确认交期",
        "rating": rating,
        "reviews": reviews,
        "sourceUrls": [url],
        "sourceDetail": "idealo",
        "evidenceSnippets": [evidence],
        "priceConfidence": "extracted" if price_eur is not None else "unknown",
        "matchScore": 78 if price_eur is not None else 60,
        "paymentTerm": "prepayment",
        "paymentLabel": "需确认付款方式",
        "deliveryMethod": "需确认配送方式",
        "source": "web",
        "category": "web",
        "description": delivery_text,
    }


def _result_container(anchor: Any) -> Any:
    """Return the nearest card-like parent instead of the entire document."""

    # The result link itself often has a ``productCard-link`` class. Starting
    # with its parent is important: price, shop, and delivery fields live on
    # the surrounding card rather than inside that anchor.
    for node in anchor.parents:
        name = getattr(node, "name", "") or ""
        classes = " ".join(getattr(node, "get", lambda *_: [])("class", [])).casefold()
        if name in {"article", "li"} or any(token in classes for token in ("product", "result", "offer", "card")):
            return node
    return anchor.parent or anchor


def _select_nodes(container: Any, selectors: tuple[str, ...]) -> list[Any]:
    nodes: list[Any] = []
    for selector in selectors:
        try:
            nodes.extend(container.select(selector))
        except Exception:
            continue
    return nodes


def _first_text(nodes: tuple[Any, ...] | list[Any]) -> str:
    for node in nodes:
        try:
            text = " ".join(node.stripped_strings).strip()
        except Exception:
            text = ""
        if text:
            return text
    return ""


def _first_selector_text(container: Any, selectors: tuple[str, ...]) -> str:
    return _first_text(_select_nodes(container, selectors))


def _first_attribute_or_text(container: Any, selectors: tuple[str, ...], attribute: str) -> str:
    for node in _select_nodes(container, selectors):
        value = str(node.get(attribute) or "").strip()
        if value:
            return value
        text = _first_text((node,))
        if text:
            return text
    return ""


def _first_price_text(container: Any) -> str:
    for node in _select_nodes(container, _PRICE_SELECTORS):
        text = _first_text((node,))
        if _parse_eur(text) is not None:
            return text
    return ""


def _dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(url for url in urls if url))


def _dedupe_texts(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _parse_eur(text: str) -> Optional[float]:
    """Parse an explicitly labelled Euro price such as ``1.234,56 EUR``."""

    if not text:
        return None
    patterns = (
        r"(?:€|EUR|Euro)\s*([0-9][0-9.,\s-]*)",
        r"([0-9][0-9.,\s-]*)\s*(?:€|EUR|Euro)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip(" .,-")
        value = _parse_localized_number(raw)
        if value is not None and 0.01 < value < 100000:
            return value
    return None


def _parse_localized_number(raw: str) -> Optional[float]:
    value = raw.replace(" ", "")
    if not value:
        return None
    dot = value.rfind(".")
    comma = value.rfind(",")
    if dot != -1 and comma != -1:
        value = value.replace(",", "") if dot > comma else value.replace(".", "").replace(",", ".")
    elif comma != -1:
        parts = value.split(",")
        value = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) == 2 else value.replace(",", "")
    elif dot != -1:
        parts = value.split(".")
        if len(parts) > 2:
            value = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) == 2 else "".join(parts)
        elif len(parts) == 2 and len(parts[-1]) == 3:
            value = "".join(parts)
    try:
        return float(value)
    except ValueError:
        return None


def _parse_rating(text: str) -> float:
    match = re.search(r"([0-5](?:[.,][0-9])?)", text or "")
    return float(match.group(1).replace(",", ".")) if match else 0.0


def _parse_count(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else 0

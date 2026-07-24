from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from web_research.marketplace import (
    EbayBrowseApiProvider,
    GenericMarketplaceConnector,
    MarketplaceSearchLayer,
    SerpApiGoogleShoppingProvider,
    normalize_marketplace_item,
    reset_serpapi_budget_for_tests,
)
from web_research import idealo_scraper


class StubMarketplaceProvider:
    def __init__(self, name: str, results: list[dict]):
        self.name = name
        self.results = results
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return True

    async def search(self, _query: str, **_kwargs) -> list[dict]:
        self.calls += 1
        return [dict(item) for item in self.results]


class MarketplaceSearchTest(unittest.TestCase):
    def setUp(self):
        # Keep the process-wide SerpApi budget deterministic for every test.
        self._serpapi_budget_env = patch.dict("os.environ", {"SERPAPI_CALL_BUDGET": "70"}, clear=False)
        self._serpapi_budget_env.start()
        reset_serpapi_budget_for_tests()
        self.addCleanup(reset_serpapi_budget_for_tests)
        self.addCleanup(self._serpapi_budget_env.stop)

    def test_serpapi_google_shopping_maps_market_and_keeps_explicit_eur_evidence(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.url.params["engine"], "google_shopping")
            self.assertEqual(request.url.params["q"], "A4 paper")
            self.assertEqual(request.url.params["gl"], "pl")
            self.assertEqual(request.url.params["hl"], "pl")
            self.assertEqual(request.url.params["google_domain"], "google.pl")
            self.assertEqual(request.url.params["api_key"], "test-serpapi-key")
            return httpx.Response(
                200,
                json={
                    "shopping_results": [
                        {
                            "product_id": "paper-001",
                            "title": "Inapa tecno Kopierpapier DIN A4 80 g/qm",
                            "link": "https://merchant.example/paper-001",
                            "product_link": "https://www.google.com/shopping/product/001",
                            "source": "Bueroshop24",
                            "price": "EUR 9,49",
                            "extracted_price": 9.49,
                            "delivery": "Free delivery in 2 days",
                            "rating": 4.7,
                            "reviews": 128,
                            "extensions": ["500 sheets", "In stock"],
                        },
                        {
                            "product_id": "usd-001",
                            "title": "Dollar product",
                            "product_link": "https://www.google.com/shopping/product/usd",
                            "source": "Dollar shop",
                            "price": "$ 10.00",
                            "extracted_price": 10.0,
                        },
                        {
                            "product_id": "ambiguous-001",
                            "title": "Unlabelled product",
                            "product_link": "https://www.google.com/shopping/product/ambiguous",
                            "source": "Unknown shop",
                            "price": "10.00",
                            "extracted_price": 10.0,
                        },
                    ]
                },
            )

        provider = SerpApiGoogleShoppingProvider(
            api_key="test-serpapi-key",
            transport=httpx.MockTransport(handler),
            cache_ttl_seconds=60,
        )
        results = asyncio.run(provider.search("A4 paper", country="Poland", limit=4))

        self.assertEqual(len(requests), 1)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["vendor"], "Bueroshop24")
        self.assertEqual(item["platform"], "Google Shopping (SerpApi)")
        self.assertEqual(item["sourceDetail"], "marketplace:serpapi")
        self.assertEqual(item["priceConfidence"], "api")
        self.assertEqual(item["unitPriceEur"], 9.49)
        self.assertEqual(item["deliveryDays"], 2)
        self.assertEqual(item["rating"], 4.7)
        self.assertEqual(item["reviews"], 128)
        self.assertEqual(item["sourceUrls"], [
            "https://merchant.example/paper-001",
            "https://www.google.com/shopping/product/001",
        ])
        self.assertIn("500 sheets", item["evidenceSnippets"])

    def test_serpapi_is_inert_without_key_and_caches_successful_responses(self):
        called = False
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called, calls
            called = True
            calls += 1
            return httpx.Response(
                200,
                json={
                    "shopping_results": [
                        {
                            "product_id": "paper-001",
                            "title": "A4 paper",
                            "product_link": "https://www.google.com/shopping/product/001",
                            "source": "Paper shop",
                            "price": "€ 4,99",
                            "extracted_price": 4.99,
                        }
                    ]
                },
            )

        disabled = SerpApiGoogleShoppingProvider(api_key="", transport=httpx.MockTransport(handler))
        self.assertFalse(disabled.enabled)
        self.assertEqual(asyncio.run(disabled.search("A4 paper")), [])
        self.assertFalse(called)

        enabled = SerpApiGoogleShoppingProvider(
            api_key="test-serpapi-key",
            transport=httpx.MockTransport(handler),
            cache_ttl_seconds=60,
        )
        first = asyncio.run(enabled.search("A4 paper", country="Germany"))
        first[0]["unitPriceEur"] = 0.01
        second = asyncio.run(enabled.search("A4 paper", country="Germany"))
        self.assertTrue(called)
        self.assertEqual(calls, 1)
        self.assertEqual(second[0]["unitPriceEur"], 4.99)

    def test_serpapi_cache_hits_do_not_consume_process_budget(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            query = request.url.params["q"]
            return httpx.Response(
                200,
                json={
                    "shopping_results": [
                        {
                            "product_id": query,
                            "title": query,
                            "product_link": f"https://shopping.example/{query.replace(' ', '-')}",
                            "source": "Test shop",
                            "price": "EUR 10.00",
                            "extracted_price": 10.0,
                        }
                    ]
                },
            )

        with patch.dict("os.environ", {"SERPAPI_CALL_BUDGET": "2"}, clear=False):
            provider = SerpApiGoogleShoppingProvider(
                api_key="test-serpapi-key",
                transport=httpx.MockTransport(handler),
                cache_ttl_seconds=60,
            )
            asyncio.run(provider.search("A4 paper", country="Germany"))
            asyncio.run(provider.search("A4 paper", country="Germany"))
            asyncio.run(provider.search("A3 paper", country="Germany"))

        self.assertEqual(len(requests), 2)

    def test_serpapi_budget_is_shared_across_provider_instances(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"shopping_results": []})

        with patch.dict("os.environ", {"SERPAPI_CALL_BUDGET": "1"}, clear=False):
            first = SerpApiGoogleShoppingProvider(
                api_key="test-serpapi-key",
                transport=httpx.MockTransport(handler),
            )
            second = SerpApiGoogleShoppingProvider(
                api_key="test-serpapi-key",
                transport=httpx.MockTransport(handler),
            )
            asyncio.run(first.search("A4 paper", country="Germany"))
            asyncio.run(second.search("A3 paper", country="Germany"))

        self.assertEqual(len(requests), 1)

    def test_serpapi_skips_unique_queries_after_budget_is_exhausted(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "shopping_results": [
                        {
                            "product_id": "paper-001",
                            "title": "A4 paper",
                            "product_link": "https://shopping.example/paper-001",
                            "source": "Test shop",
                            "price": "EUR 10.00",
                            "extracted_price": 10.0,
                        }
                    ]
                },
            )

        with patch.dict("os.environ", {"SERPAPI_CALL_BUDGET": "1"}, clear=False):
            provider = SerpApiGoogleShoppingProvider(
                api_key="test-serpapi-key",
                transport=httpx.MockTransport(handler),
            )
            first = asyncio.run(provider.search("A4 paper", country="Germany"))
            second = asyncio.run(provider.search("A3 paper", country="Germany"))

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(requests), 1)

    def test_serpapi_budget_environment_override_and_test_reset(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"shopping_results": []})

        with patch.dict("os.environ", {"SERPAPI_CALL_BUDGET": "1"}, clear=False):
            provider = SerpApiGoogleShoppingProvider(
                api_key="test-serpapi-key",
                transport=httpx.MockTransport(handler),
            )
            asyncio.run(provider.search("A4 paper", country="Germany"))
            asyncio.run(provider.search("A3 paper", country="Germany"))
            reset_serpapi_budget_for_tests()
            asyncio.run(provider.search("A5 paper", country="Germany"))

        self.assertEqual(len(requests), 2)

    def test_serpapi_never_maps_aggregate_or_unknown_markets_to_germany(self):
        called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"shopping_results": []})

        provider = SerpApiGoogleShoppingProvider(
            api_key="test-serpapi-key",
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(asyncio.run(provider.search("A4 paper", country="Europe")), [])
        self.assertEqual(asyncio.run(provider.search("A4 paper", country="Czech Republic")), [])
        self.assertFalse(called)

    def test_serpapi_is_the_first_default_marketplace_provider(self):
        with patch.dict("os.environ", {"SERPAPI_API_KEY": "test-serpapi-key"}, clear=False):
            layer = MarketplaceSearchLayer()

        self.assertIsInstance(layer.providers[0], SerpApiGoogleShoppingProvider)
        self.assertTrue(layer.providers[0].enabled)

    def test_ebay_browse_provider_exchanges_token_and_normalizes_eur_item(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/oauth2/token"):
                self.assertTrue(request.headers["Authorization"].startswith("Basic "))
                return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            self.assertEqual(request.headers["X-EBAY-C-MARKETPLACE-ID"], "EBAY_DE")
            return httpx.Response(
                200,
                json={
                    "itemSummaries": [
                        {
                            "itemId": "v1|123|0",
                            "title": "HP EliteBook 840 G9 Laptop",
                            "itemWebUrl": "https://www.ebay.de/itm/123",
                            "price": {"value": "841.17", "currency": "EUR"},
                            "seller": {"username": "trusted-seller"},
                            "shippingOptions": [{"deliveryDays": 3, "shippingCostType": "FREE"}],
                        }
                    ]
                },
            )

        provider = EbayBrowseApiProvider(
            client_id="client-id",
            client_secret="client-secret",
            transport=httpx.MockTransport(handler),
        )
        results = asyncio.run(provider.search("HP EliteBook", country="Germany", limit=4))

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["unitPriceEur"], 841.17)
        self.assertEqual(results[0]["vendor"], "trusted-seller")
        self.assertEqual(results[0]["sourceDetail"], "marketplace:ebay")
        self.assertEqual(results[0]["priceConfidence"], "api")

    def test_ebay_provider_is_inert_without_credentials(self):
        called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(500)

        provider = EbayBrowseApiProvider(
            client_id="",
            client_secret="",
            transport=httpx.MockTransport(handler),
        )
        self.assertFalse(provider.enabled)
        self.assertEqual(asyncio.run(provider.search("A4 paper")), [])
        self.assertFalse(called)

    def test_generic_connector_accepts_common_normalized_fields(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.params["query"], "business laptop")
            self.assertEqual(request.headers["Authorization"], "Bearer api-key")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "vendor-001",
                            "name": "Business laptop 14 inch",
                            "url": "https://market.example/items/001",
                            "price": "EUR 699,00",
                            "currency": "EUR",
                            "sellerName": "Approved marketplace seller",
                            "delivery": "2-3 days",
                            "country": "Germany",
                        },
                        {
                            "id": "usd-001",
                            "name": "USD offer is not comparable",
                            "url": "https://market.example/items/usd-001",
                            "price": "700",
                            "currency": "USD",
                        },
                    ]
                },
            )

        connector = GenericMarketplaceConnector(
            endpoint="https://market.example/search",
            api_key="api-key",
            transport=httpx.MockTransport(handler),
        )
        results = asyncio.run(connector.search("business laptop", country="Germany"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["unitPriceEur"], 699.0)
        self.assertEqual(results[0]["priceConfidence"], "api")
        self.assertEqual(results[0]["sourceDetail"], "marketplace:generic")

    def test_generic_connector_requires_response_market_for_an_explicit_country(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "unscoped-offer",
                            "name": "Offer without market evidence",
                            "url": "https://market.example/de",
                            "price": "EUR 99.00",
                            "currency": "EUR",
                        },
                        {
                            "id": "german-offer",
                            "name": "German offer with market evidence",
                            "url": "https://market.example/germany",
                            "price": "EUR 109.00",
                            "currency": "EUR",
                            "country": "Germany",
                        },
                        {
                            "id": "polish-offer",
                            "name": "Polish offer with market evidence",
                            "url": "https://market.example/pl",
                            "price": "EUR 119.00",
                            "currency": "EUR",
                            "country": "Poland",
                        },
                    ]
                },
            )

        connector = GenericMarketplaceConnector(
            endpoint="https://market.example/search",
            transport=httpx.MockTransport(handler),
        )
        germany_results = asyncio.run(connector.search("business laptop", country="Germany"))
        poland_results = asyncio.run(connector.search("business laptop", country="Poland"))

        self.assertEqual(len(germany_results), 1)
        self.assertEqual(germany_results[0]["product"], "German offer with market evidence")
        self.assertEqual(len(poland_results), 1)
        self.assertEqual(poland_results[0]["product"], "Polish offer with market evidence")

    def test_layer_short_circuits_later_providers_after_enough_priced_items(self):
        priced = [
            {
                "id": f"api-{index}",
                "unitPriceEur": 10 + index,
                "sourceUrls": [f"https://market.example/{index}"],
            }
            for index in range(3)
        ]
        first = StubMarketplaceProvider("first", priced)
        second = StubMarketplaceProvider("second", priced)
        layer = MarketplaceSearchLayer(providers=[first, second], cache_ttl_seconds=60)

        results = asyncio.run(layer.search("A4 paper", min_priced_results=3))
        cached_results = asyncio.run(layer.search("A4 paper", min_priced_results=3))

        self.assertEqual(len(results), 3)
        self.assertEqual(len(cached_results), 3)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)

    def test_layer_collapses_duplicate_marketplace_seller_and_keeps_alternates(self):
        provider = StubMarketplaceProvider(
            "serpapi",
            [
                {
                    "id": "offer-expensive",
                    "vendor": "Amazon.de - Amazon.de-Seller",
                    "product": "Logitech B100 Maus",
                    "unitPriceEur": 7.99,
                    "unitLabel": "7,99 €",
                    "sourceDetail": "marketplace:serpapi",
                    "sourceUrls": ["https://shopping.example/expensive"],
                },
                {
                    "id": "offer-cheap",
                    "vendor": "Amazon.de - Amazon.de-Seller",
                    "product": "Logitech B100 Maus (Generalüberholt)",
                    "unitPriceEur": 4.99,
                    "unitLabel": "4,99 €",
                    "sourceDetail": "marketplace:serpapi",
                    "sourceUrls": ["https://shopping.example/cheap"],
                },
            ],
        )
        layer = MarketplaceSearchLayer(providers=[provider], cache_ttl_seconds=60)

        results = asyncio.run(layer.search("Logitech B100", min_priced_results=1))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["unitPriceEur"], 4.99)
        self.assertEqual(results[0]["offerCount"], 2)
        self.assertEqual(len(results[0]["alternateOffers"]), 1)

    def test_marketplace_missing_payment_terms_are_explicitly_unknown(self):
        result = normalize_marketplace_item(
            {
                "title": "A4 paper",
                "url": "https://market.example/paper",
                "price": {"value": "4.99", "currency": "EUR"},
            },
            provider="test",
            platform="Test API",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["paymentTerm"], "unknown")
        self.assertEqual(result["paymentLabel"], "需确认付款方式")

    def test_normalizer_rejects_non_eur_values(self):
        self.assertIsNone(
            normalize_marketplace_item(
                {
                    "title": "Dollar item",
                    "url": "https://market.example/usd",
                    "price": {"value": "25.00", "currency": "USD"},
                },
                provider="test",
                platform="Test API",
            )
        )

    def test_normalizer_rejects_unlabelled_price_and_does_not_short_circuit(self):
        self.assertIsNone(
            normalize_marketplace_item(
                {
                    "title": "Unlabelled item",
                    "url": "https://market.example/unlabelled",
                    "price": "25.00",
                },
                provider="test",
                platform="Test API",
            )
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "unlabelled-001",
                            "title": "Unlabelled item",
                            "url": "https://market.example/unlabelled-001",
                            "price": "25.00",
                        }
                    ]
                },
            )

        priced = [
            {
                "id": f"fallback-{index}",
                "unitPriceEur": 10 + index,
                "sourceUrls": [f"https://fallback.example/{index}"],
            }
            for index in range(3)
        ]
        generic = GenericMarketplaceConnector(
            endpoint="https://market.example/search",
            transport=httpx.MockTransport(handler),
        )
        fallback = StubMarketplaceProvider("fallback", priced)
        layer = MarketplaceSearchLayer(providers=[generic, fallback], cache_ttl_seconds=60)

        results = asyncio.run(layer.search("A4 paper", min_priced_results=3))

        self.assertEqual(len(results), 3)
        self.assertEqual(fallback.calls, 1)

    def test_idealo_does_not_cache_an_empty_timeout_or_failed_scrape(self):
        idealo_scraper.reset_idealo_cache_for_tests()
        with patch(
            "web_research.idealo_scraper.asyncio.to_thread",
            new=AsyncMock(return_value=[]),
        ) as to_thread:
            first = asyncio.run(idealo_scraper.search_idealo("A4 paper", limit=2, timeout=1))
            second = asyncio.run(idealo_scraper.search_idealo("A4 paper", limit=2, timeout=1))

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(to_thread.await_count, 2)
        self.assertFalse(idealo_scraper._SCRAPE_CACHE)


if __name__ == "__main__":
    unittest.main()

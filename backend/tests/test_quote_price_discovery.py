from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent.parser import ProcurementIntent
from agent.procurement_agent import ProcurementAgent
from web_research.researcher import PageSnapshot, SearchResult
from web_research.researcher import StaticPageFetcher


class FakeQuoteSearchProvider:
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        self.queries.append(query)
        if len(self.queries) == 1:
            return [
                SearchResult(
                    title="A4 Kopierpapier online kaufen",
                    url="https://paper-shop.example/a4-paper",
                    snippet="A4 Papier für Bürobedarf",
                )
            ]
        return [
            SearchResult(
                title="A4 Papier 500 Blatt € 4,99",
                url="https://second-shop.example/a4-paper",
                snippet="Büropapier A4 online shop Deutschland",
            )
        ]


class FakeQuotePageFetcher:
    async def fetch_page(self, url: str) -> PageSnapshot:
        return PageSnapshot(
            url=url,
            text="A4 Kopierpapier 500 Blatt Packung. Sofort lieferbar. Preis € 5,49 inkl. MwSt.",
            links=[],
        )


class QuotePriceDiscoveryTest(unittest.TestCase):
    def test_web_quote_opens_product_page_and_extracts_price(self):
        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.quote_search_provider = FakeQuoteSearchProvider()
            agent.quote_page_fetcher = FakeQuotePageFetcher()
            intent = ProcurementIntent(category="office", country="Germany", keywords=["A4", "Papier"])

            results = await agent._search_web_quotes("A4 Papier 500 Blatt", intent, max_results=3)

            self.assertGreaterEqual(len(results), 1)
            self.assertTrue(any(item["unitPriceEur"] == 5.49 for item in results), results)
            self.assertTrue(all(item["unitLabel"] != "需人工核价" for item in results), results)
            self.assertGreaterEqual(len(agent.quote_search_provider.queries), 2)

        asyncio.run(scenario())

    def test_priced_candidates_hide_manual_price_rows_when_prices_exist(self):
        candidates = [
            {"id": "manual", "unitPriceEur": None, "unitLabel": "需人工核价"},
            {"id": "priced", "unitPriceEur": 12.5, "unitLabel": "€ 12.50"},
        ]

        filtered = ProcurementAgent._prefer_priced_quote_candidates(candidates)

        self.assertEqual([item["id"] for item in filtered], ["priced"])

    def test_price_parser_handles_common_german_price_formats(self):
        self.assertEqual(ProcurementAgent._extract_eur_price("Preis € 1.234,56"), 1234.56)
        self.assertEqual(ProcurementAgent._extract_eur_price("nur 4,99 EUR"), 4.99)
        self.assertEqual(ProcurementAgent._extract_eur_price("ab € 25,-"), 25.0)
        self.assertEqual(ProcurementAgent._extract_eur_price("Preis ab EUR 841,17"), 841.17)
        self.assertEqual(ProcurementAgent._extract_eur_price('"lowPrice":"1.299,00","priceCurrency":"EUR"'), 1299.0)
        self.assertEqual(ProcurementAgent._extract_eur_price("€ 1.234"), 1234.0)

    def test_model_identifier_survives_search_keyword_translation(self):
        self.assertEqual(
            ProcurementAgent._quote_model_identifiers("德国采购 Philips XC2011/01 无线吸尘器"),
            ["XC2011/01"],
        )
        self.assertEqual(
            ProcurementAgent._quote_model_identifiers("购买 A4 复印纸 80g"),
            [],
        )
        self.assertEqual(
            ProcurementAgent._quote_model_identifiers("Logitech B100 Maus USB"),
            ["B100"],
        )

    def test_model_and_brand_constrain_marketplace_search_and_relevance(self):
        intent = ProcurementIntent(category="accessory", country="Germany", keywords=["Logitech", "B100", "Maus"])

        self.assertEqual(
            ProcurementAgent._quote_search_product_phrase("Logitech B100 Maus USB", intent),
            "logitech B100 maus",
        )
        self.assertTrue(
            ProcurementAgent._is_relevant_quote_item(
                {"product": "Logitech B100 Maus mit Kabel USB", "unitPriceEur": 8.99},
                "Logitech B100 Maus USB",
                intent,
            )
        )
        self.assertFalse(
            ProcurementAgent._is_relevant_quote_item(
                {"product": "Razer DeathAdder Essential Maus", "unitPriceEur": 16.52},
                "Logitech B100 Maus USB",
                intent,
            )
        )

    def test_high_value_web_product_keeps_its_extracted_price(self):
        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            intent = ProcurementIntent(category="hardware", country="Germany", keywords=["laptop"])
            candidate = await agent._web_quote_candidate_from_result(
                SearchResult(
                    title="HP EliteBook 840 G9 Laptop € 841.17",
                    url="https://shop.example/hp-elitebook",
                    snippet="Business laptop with public web price",
                ),
                intent,
                0,
                "HP EliteBook laptop",
            )

            self.assertIsNotNone(candidate)
            self.assertEqual(candidate["unitPriceEur"], 841.17)
            self.assertEqual(candidate["priceConfidence"], "extracted")
            self.assertNotEqual(candidate["unitLabel"], "需人工核价")

        asyncio.run(scenario())

    def test_marketplace_results_can_short_circuit_slow_web_search(self):
        class FakeParser:
            async def parse(self, _query: str):
                return ProcurementIntent(category="hardware", country="Germany", keywords=["laptop"])

        class FakeMarketplace:
            enabled = True

            def __init__(self):
                self.country = None

            async def search(self, _query: str, **_kwargs):
                self.country = _kwargs.get("country")
                return [
                    {
                        "id": f"marketplace-ebay-{index}",
                        "vendor": "eBay seller",
                        "platform": "eBay Browse API",
                        "product": f"Business Laptop {index}",
                        "description": "Business laptop",
                        "unitPriceEur": 800.0 + index,
                        "unitLabel": f"€ {800.0 + index:.2f}",
                        "deliveryDays": 3,
                        "deliveryLabel": "3 Tage",
                        "paymentTerm": "prepayment",
                        "paymentLabel": "需确认付款方式",
                        "deliveryMethod": "parcel",
                        "rating": 4.5,
                        "reviews": 10,
                        "source": "web",
                        "sourceDetail": "marketplace:ebay",
                        "sourceUrls": [f"https://www.ebay.de/itm/{index}"],
                        "priceConfidence": "api",
                        "matchScore": 80,
                    }
                    for index in range(3)
                ]

        class FakeRanker:
            async def rank_quotes(self, _query: str, candidates: list[dict], **_kwargs):
                return candidates

        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.llm = None
            agent.parser = FakeParser()
            agent.quotes = []
            marketplace = FakeMarketplace()
            agent.marketplace_search = marketplace
            agent.ranker = FakeRanker()
            agent._search_web_quotes = AsyncMock(side_effect=AssertionError("web search should be skipped"))
            agent._llm_filter_relevant_quotes = AsyncMock(side_effect=AssertionError("LLM review should be skipped"))

            with patch("agent.procurement_agent.search_idealo", new_callable=AsyncMock) as idealo:
                result = await agent.search_quotes("business laptop", country="Poland")

            self.assertEqual(len(result["results"]), 3)
            self.assertTrue(all(item["priceConfidence"] == "api" for item in result["results"]))
            self.assertEqual(marketplace.country, "Poland")
            self.assertEqual(result["intent"]["country"], "Poland")
            agent._search_web_quotes.assert_not_awaited()
            agent._llm_filter_relevant_quotes.assert_not_awaited()
            idealo.assert_not_awaited()

        asyncio.run(scenario())

    def test_insufficient_marketplace_results_keep_idealo_and_web_fallbacks(self):
        class FakeParser:
            async def parse(self, _query: str):
                return ProcurementIntent(category="hardware", country="Germany", keywords=["laptop"])

        class FakeMarketplace:
            enabled = True

            async def search(self, _query: str, **_kwargs):
                return [
                    {
                        "id": f"marketplace-serpapi-{index}",
                        "vendor": "Shopping seller",
                        "platform": "Google Shopping (SerpApi)",
                        "product": f"Business Laptop {index}",
                        "unitPriceEur": 800.0 + index,
                        "unitLabel": f"€ {800.0 + index:.2f}",
                        "deliveryDays": 3,
                        "deliveryLabel": "3 Tage",
                        "paymentTerm": "prepayment",
                        "paymentLabel": "需确认付款方式",
                        "deliveryMethod": "parcel",
                        "rating": 4.5,
                        "reviews": 10,
                        "source": "web",
                        "sourceDetail": "marketplace:serpapi",
                        "sourceUrls": [f"https://shopping.example/{index}"],
                        "priceConfidence": "api",
                        "matchScore": 80,
                    }
                    for index in range(2)
                ]

        class FakeRanker:
            async def rank_quotes(self, _query: str, candidates: list[dict], **_kwargs):
                return candidates

        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.llm = None
            agent.parser = FakeParser()
            agent.quotes = []
            agent.marketplace_search = FakeMarketplace()
            agent.ranker = FakeRanker()
            agent._search_web_quotes = AsyncMock(return_value=[])

            with patch("agent.procurement_agent.search_idealo", new_callable=AsyncMock, return_value=[]) as idealo:
                result = await agent.search_quotes("business laptop")

            self.assertEqual(len(result["results"]), 2)
            idealo.assert_awaited_once()
            agent._search_web_quotes.assert_awaited_once()

        asyncio.run(scenario())

    def test_slow_supplement_keeps_priced_marketplace_candidate(self):
        class FakeParser:
            async def parse(self, _query: str):
                return ProcurementIntent(category="hardware", country="Germany", keywords=["laptop"])

        class FakeMarketplace:
            enabled = True

            async def search(self, _query: str, **_kwargs):
                return [{
                    "id": "marketplace-laptop",
                    "vendor": "Verified Market Seller",
                    "platform": "Google Shopping (SerpApi)",
                    "product": "Business Laptop",
                    "description": "Business laptop with public EUR price",
                    "unitPriceEur": 799.0,
                    "unitLabel": "€ 799.00",
                    "deliveryDays": 3,
                    "deliveryLabel": "3 Tage",
                    "paymentTerm": "prepayment",
                    "paymentLabel": "需确认付款方式",
                    "deliveryMethod": "parcel",
                    "rating": 4.5,
                    "reviews": 10,
                    "source": "web",
                    "sourceDetail": "marketplace:serpapi",
                    "sourceUrls": ["https://shopping.example/laptop"],
                    "priceConfidence": "api",
                    "matchScore": 80,
                }]

        class FakeRanker:
            async def rank_quotes(self, _query: str, candidates: list[dict], **_kwargs):
                return candidates

        async def slow_web_search(*_args, **_kwargs):
            await asyncio.sleep(1)
            return []

        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.llm = None
            agent.parser = FakeParser()
            agent.quotes = []
            agent.marketplace_search = FakeMarketplace()
            agent.ranker = FakeRanker()
            agent._search_web_quotes = slow_web_search
            agent._quote_web_fallback_timeout_seconds = lambda **_kwargs: 0.01

            async def slow_idealo(*_args, **_kwargs):
                await asyncio.sleep(1)
                return []

            with patch("agent.procurement_agent.search_idealo", new=slow_idealo):
                result = await agent.search_quotes("business laptop")

            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["vendor"], "Verified Market Seller")

        asyncio.run(scenario())

    def test_poland_with_insufficient_api_results_skips_idealo_and_uses_web_fallback(self):
        class FakeParser:
            async def parse(self, _query: str):
                return ProcurementIntent(category="hardware", country="Germany", keywords=["laptop"])

        class FakeRanker:
            async def rank_quotes(self, _query: str, candidates: list[dict], **_kwargs):
                return candidates

        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.llm = None
            agent.parser = FakeParser()
            agent.quotes = []
            agent.marketplace_search = None
            agent.ranker = FakeRanker()
            agent._search_web_quotes = AsyncMock(return_value=[])

            with patch("agent.procurement_agent.search_idealo", new_callable=AsyncMock, return_value=[]) as idealo:
                await agent.search_quotes("business laptop", country="Poland")

            idealo.assert_not_awaited()
            agent._search_web_quotes.assert_awaited_once()

        asyncio.run(scenario())

    def test_no_marketplace_configuration_keeps_default_germany_idealo_fallback(self):
        class FakeParser:
            async def parse(self, _query: str):
                return ProcurementIntent(category="hardware", country=None, keywords=["laptop"])

        class FakeRanker:
            async def rank_quotes(self, _query: str, candidates: list[dict], **_kwargs):
                return candidates

        async def scenario():
            agent = ProcurementAgent.__new__(ProcurementAgent)
            agent.llm = None
            agent.parser = FakeParser()
            agent.quotes = []
            agent.marketplace_search = None
            agent.ranker = FakeRanker()
            agent._search_web_quotes = AsyncMock(return_value=[])

            with patch("agent.procurement_agent.search_idealo", new_callable=AsyncMock, return_value=[]) as idealo:
                await agent.search_quotes("business laptop")

            idealo.assert_awaited_once()
            agent._search_web_quotes.assert_awaited_once()

        asyncio.run(scenario())

    def test_non_german_web_queries_are_country_qualified_without_idealo(self):
        poland_intent = ProcurementIntent(category="hardware", country="Poland", keywords=["laptop"])
        poland_site_queries = ProcurementAgent._quote_site_specific_queries("business laptop", "Poland")
        poland_extra_queries = ProcurementAgent._quote_price_search_queries("business laptop", poland_intent)
        europe_site_queries = ProcurementAgent._quote_site_specific_queries("business laptop", "Europe")

        for queries, market in ((poland_site_queries, "Poland"), (poland_extra_queries, "Poland"), (europe_site_queries, "Europe")):
            self.assertTrue(all(market in query for query in queries), queries)
            self.assertTrue(all("Deutschland" not in query and "idealo.de" not in query for query in queries), queries)

    def test_idealo_timeout_has_a_usable_default_and_is_configurable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IDEALO_TIMEOUT_SECONDS", None)
            self.assertEqual(ProcurementAgent._idealo_timeout_seconds(), 20.0)
        with patch.dict(os.environ, {"IDEALO_TIMEOUT_SECONDS": "45"}, clear=False):
            self.assertEqual(ProcurementAgent._idealo_timeout_seconds(), 45.0)

    def test_static_fetcher_preserves_json_ld_price_fragments(self):
        html = (
            '<html><head>'
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"price":"7.49","priceCurrency":"EUR"}}'
            '</script>'
            '</head><body><h1>A4 Kopierpapier 500 Blatt</h1></body></html>'
        )

        page = StaticPageFetcher()._parse_html("https://shop.example/product", html)
        self.assertIn("7.49", page.text)
        self.assertEqual(ProcurementAgent._extract_eur_price(page.text), 7.49)

    def test_a4_paper_relevance_rejects_generic_a4_and_calculators(self):
        intent = ProcurementIntent(category="hardware", country="Germany", keywords=["a4纸"])

        self.assertTrue(
            ProcurementAgent._is_relevant_quote_item(
                {"product": "Inapa tecno Kopierpapier DIN A4 80 g/qm 500 Blatt", "unitPriceEur": 9.21},
                "我需要a4纸",
                intent,
            )
        )
        self.assertFalse(
            ProcurementAgent._is_relevant_quote_item(
                {"product": "Schnellhefter Plastik A4 （ab 50 stk）", "unitPriceEur": 0.30},
                "我需要a4纸",
                intent,
            )
        )
        self.assertFalse(
            ProcurementAgent._is_relevant_quote_result(
                "Preis je Menge vergleichen und berechnen Stückpreis Rechner € 3,50",
                "我需要a4纸",
                intent,
                price_found=True,
            )
        )

    def test_generic_product_relevance_does_not_cross_match_other_products(self):
        cases = [
            (
                "我需要鼠标",
                ProcurementIntent(category="hardware", country="Germany", keywords=["鼠标"]),
                {"product": "Logitech B100 Maus mit Kabel USB optischer Sensor", "unitPriceEur": 3.48},
                {"product": "Hama Tastatur mit Kabel QWERTZ schwarz", "unitPriceEur": 9.24},
            ),
            (
                "我需要键盘",
                ProcurementIntent(category="hardware", country="Germany", keywords=["键盘"]),
                {"product": "Hama Tastatur mit Kabel QWERTZ schwarz", "unitPriceEur": 9.24},
                {"product": "Logitech B100 Maus mit Kabel USB optischer Sensor", "unitPriceEur": 3.48},
            ),
            (
                "我需要计算器",
                ProcurementIntent(category="hardware", country="Germany", keywords=["计算器"]),
                {"product": "Canon Taschenrechner AS-2200", "unitPriceEur": 15.39},
                {"product": "Preis je Menge vergleichen und berechnen", "unitPriceEur": 3.50},
            ),
        ]

        for query, intent, good, bad in cases:
            with self.subTest(query=query):
                self.assertTrue(ProcurementAgent._is_relevant_quote_item(good, query, intent), good)
                self.assertFalse(ProcurementAgent._is_relevant_quote_item(bad, query, intent), bad)

    def test_web_search_query_is_product_focused_and_noise_is_blocked(self):
        intent = ProcurementIntent(category="hardware", country="Germany", keywords=["a4纸"])

        self.assertEqual(
            ProcurementAgent._quote_search_product_phrase("我需要a4纸", intent),
            "a4 kopierpapier",
        )
        self.assertTrue(ProcurementAgent._is_blocked_quote_domain("euroshop-online.de"))
        self.assertTrue(ProcurementAgent._is_blocked_quote_domain("shop.deutschepost.de"))
        self.assertTrue(
            ProcurementAgent._is_quote_noise_result(
                "Preis je Menge vergleichen und berechnen Stückpreis Rechner € 3,50",
                "https://rechneronline.de/matrix/preis-je-menge.php",
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from web_research import idealo_scraper


class IdealoScraperTest(unittest.TestCase):
    def setUp(self) -> None:
        idealo_scraper.reset_idealo_cache_for_tests()

    def tearDown(self) -> None:
        idealo_scraper.reset_idealo_cache_for_tests()

    def test_builds_direct_public_search_url_with_encoded_query(self):
        url = idealo_scraper.build_idealo_search_url("A4 Kopierpapier 80 g/m2")

        self.assertEqual(
            url,
            "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=A4+Kopierpapier+80+g%2Fm2",
        )

    def test_result_page_parser_normalizes_card_price_and_metadata(self):
        html = """
        <article class="productCard">
          <a class="productCard-link" href="/preisvergleich/OffersOfProduct/201234567_-kopierpapier-a4.html">
            Inapa tecno Kopierpapier DIN A4 80 g/qm
          </a>
          <span class="productCard-price">ab 1.234,56 EUR</span>
          <span class="productCard-shop">Bueroshop24</span>
          <span class="productCard-delivery">Lieferung in 2 Tagen</span>
          <span class="productCard-rating">4,7</span>
          <span class="productCard-review">(128 Bewertungen)</span>
        </article>
        """

        candidates, detail_urls = idealo_scraper.parse_idealo_result_page(html, "A4 paper", limit=4)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            detail_urls,
            ["https://www.idealo.de/preisvergleich/OffersOfProduct/201234567_-kopierpapier-a4.html"],
        )
        item = candidates[0]
        self.assertEqual(item["product"], "Inapa tecno Kopierpapier DIN A4 80 g/qm")
        self.assertEqual(item["vendor"], "Bueroshop24")
        self.assertEqual(item["unitPriceEur"], 1234.56)
        self.assertEqual(item["priceConfidence"], "extracted")
        self.assertEqual(item["sourceDetail"], "idealo")

    def test_offer_page_parser_keeps_only_explicit_euro_prices(self):
        html = """
        <h1 class="oopStage-title">Business Laptop 14 inch</h1>
        <div class="productOffers-listItem">
          <span class="productOffers-listItemOfferShopV2LogoLink" data-shop-name="Office Store - Premium">Office Store</span>
          <span class="productOffers-listItemOfferPrice">841,17 €</span>
          <span class="productOffers-listItemOfferDeliveryStatusDatesRange">2-3 Werktage</span>
        </div>
        <div class="productOffers-listItem">
          <span class="productOffers-listItemOfferShopV2LogoLink" data-shop-name="No Price Shop">No Price Shop</span>
          <span class="productOffers-listItemOfferPrice">Preis auf Anfrage</span>
        </div>
        """

        candidates = idealo_scraper.parse_idealo_offer_page(
            html,
            "https://www.idealo.de/preisvergleich/OffersOfProduct/123_-laptop.html",
            "business laptop",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["unitPriceEur"], 841.17)
        self.assertEqual(candidates[0]["vendor"], "Office Store")
        self.assertEqual(candidates[0]["priceConfidence"], "extracted")

    def test_successful_result_is_cached_and_returned_as_a_copy(self):
        result = [{"id": "idealo-1", "unitPriceEur": 9.99, "sourceUrls": ["https://idealo.example/1"]}]
        with patch(
            "web_research.idealo_scraper.asyncio.to_thread",
            new=AsyncMock(return_value=result),
        ) as to_thread:
            first = asyncio.run(idealo_scraper.search_idealo("A4 paper", limit=2, timeout=1))
            first[0]["unitPriceEur"] = 0.01
            second = asyncio.run(idealo_scraper.search_idealo("  a4   PAPER  ", limit=2, timeout=1))

        self.assertEqual(to_thread.await_count, 1)
        self.assertEqual(second[0]["unitPriceEur"], 9.99)

    def test_failed_or_empty_scrape_is_not_cached(self):
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

    def test_browser_capacity_wait_respects_deadline_without_starting_chrome(self):
        acquired = idealo_scraper._BROWSER_SEMAPHORE.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with patch("web_research.idealo_scraper._scrape_idealo_sync") as scrape:
                result = idealo_scraper._scrape_idealo_with_permit(
                    "A4 paper",
                    2,
                    deadline=time.monotonic() + 0.01,
                )
        finally:
            idealo_scraper._BROWSER_SEMAPHORE.release()

        self.assertEqual(result, [])
        scrape.assert_not_called()

    def test_driver_timeouts_never_extend_past_request_deadline(self):
        deadline = time.monotonic() + 0.5
        timeout = idealo_scraper._driver_timeout_seconds(deadline, 20)

        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 0.5)
        self.assertEqual(idealo_scraper._driver_timeout_seconds(time.monotonic() - 1, 20), 0.0)
        self.assertEqual(idealo_scraper._driver_timeout_seconds(None, 20), 20)


if __name__ == "__main__":
    unittest.main()

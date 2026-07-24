from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import query_products, query_products_sync


class _BrokenConnection:
    def cursor(self, *args, **kwargs):
        raise RuntimeError("database query failed")

    def close(self):
        return None


class LocalQuoteCatalogTest(unittest.TestCase):
    def test_database_unavailable_uses_normalized_local_quote_catalog(self):
        with patch("database.get_connection", return_value=None):
            async_items = asyncio.run(query_products())
            sync_items = query_products_sync()

        self.assertEqual(len(sync_items), 36)
        self.assertEqual(
            [item["id"] for item in async_items],
            [item["id"] for item in sync_items],
        )

        required_fields = {
            "id", "vendor", "platform", "product", "matchScore", "unitPriceEur",
            "unitLabel", "deliveryDays", "deliveryLabel", "paymentTerm", "paymentLabel",
            "deliveryMethod", "rating", "reviews", "source", "sourceDetail", "sourceUrls",
            "evidenceSnippets", "priceConfidence",
        }
        self.assertTrue(all(required_fields.issubset(item) for item in sync_items))
        self.assertTrue(all(item["source"] == "database" for item in sync_items))
        self.assertTrue(all(item["sourceDetail"] == "local-file" for item in sync_items))
        self.assertTrue(all(item["priceConfidence"] == "extracted" for item in sync_items))
        self.assertTrue(all(item["sourceUrls"] == [] for item in sync_items))

    def test_database_query_failure_falls_back_to_local_quote_catalog(self):
        with patch("database.get_connection", side_effect=[_BrokenConnection(), _BrokenConnection()]):
            async_items = asyncio.run(query_products())
            sync_items = query_products_sync()

        self.assertEqual(len(async_items), 36)
        self.assertEqual([item["id"] for item in async_items], [item["id"] for item in sync_items])
        self.assertTrue(all(item["sourceDetail"] == "local-file" for item in async_items))

    def test_local_catalog_honors_price_and_delivery_filters(self):
        with patch("database.get_connection", return_value=None):
            items = asyncio.run(query_products(max_price=1.0, max_delivery_days=1))

        self.assertTrue(items)
        self.assertTrue(all(item["unitPriceEur"] is not None and item["unitPriceEur"] <= 1.0 for item in items))
        self.assertTrue(all(item["deliveryDays"] is not None and item["deliveryDays"] <= 1 for item in items))


if __name__ == "__main__":
    unittest.main()

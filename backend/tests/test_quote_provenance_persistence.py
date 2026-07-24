from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import conversations
from api.auth import AuthUser
from database import query_products, query_products_sync
from db_writer import save_comparison_request_and_quotes


class _WriteCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []
        self._next_row: dict | None = None

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.executions.append((statement, parameters))
        normalized = " ".join(statement.lower().split())
        if "insert into procurement_request" in normalized:
            self._next_row = {"id": 1}
        elif "select id from supplier" in normalized:
            self._next_row = {"id": 2}
        elif "select id from product" in normalized:
            self._next_row = {"id": 3}
        elif "select id, attributes from quote" in normalized:
            self._next_row = None
        else:
            self._next_row = None

    def fetchone(self) -> dict | None:
        return self._next_row


class _WriteConnection:
    def __init__(self) -> None:
        self.cursor_instance = _WriteCursor()

    def cursor(self, *args, **kwargs) -> _WriteCursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _ExistingQuoteCursor(_WriteCursor):
    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        super().execute(statement, parameters)
        normalized = " ".join(statement.lower().split())
        if "select id, attributes from quote" in normalized:
            self._next_row = {
                "id": 44,
                "attributes": {"platform": "Legacy Shop", "criteriaScores": {"price": 80}},
            }


class _ExistingQuoteConnection(_WriteConnection):
    def __init__(self) -> None:
        self.cursor_instance = _ExistingQuoteCursor()


class _ReadCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, object]] = []

    def execute(self, statement: str, parameters=None) -> None:
        self.executions.append((statement, parameters))

    def fetchall(self) -> list[dict]:
        return self.rows


class _ReadConnection:
    def __init__(self, rows: list[dict]) -> None:
        self.cursor_instance = _ReadCursor(rows)

    def cursor(self, *args, **kwargs) -> _ReadCursor:
        return self.cursor_instance

    def close(self) -> None:
        return None


class _ConversationCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.executions.append((statement, parameters))


class _ConversationConnection:
    def __init__(self) -> None:
        self.cursor_instance = _ConversationCursor()

    def cursor(self, *args, **kwargs) -> _ConversationCursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


class QuoteProvenancePersistenceTest(unittest.TestCase):
    def test_writer_persists_api_and_idealo_provenance(self):
        cases = [
            ("marketplace:serpapi", "api", "https://shopping.example/paper"),
            ("idealo", "extracted", "https://www.idealo.de/preisvergleich/paper"),
        ]
        for source_detail, price_confidence, source_url in cases:
            with self.subTest(source_detail=source_detail):
                connection = _WriteConnection()
                item = {
                    "vendor": "Example Shop",
                    "product": "A4 paper 500 sheets",
                    "platform": "Google Shopping (SerpApi)" if price_confidence == "api" else "idealo.de",
                    "unitPriceEur": 5.49,
                    "deliveryDays": 3,
                    "deliveryLabel": "3 days",
                    "rating": 4.5,
                    "matchScore": 82,
                    "source": "web",
                    "sourceDetail": source_detail,
                    "sourceUrls": [source_url, f"{source_url}?offer=2"],
                    "evidenceSnippets": ["Public EUR price", "In stock"],
                    "priceConfidence": price_confidence,
                }
                with patch("db_writer.get_connection", return_value=connection):
                    save_comparison_request_and_quotes("A4 paper", "buyer@example.com", [item])

                quote_execution = next(
                    execution
                    for execution in connection.cursor_instance.executions
                    if "INSERT INTO quote" in execution[0]
                )
                assert quote_execution[1] is not None
                attributes = json.loads(quote_execution[1][-1])
                self.assertEqual(attributes["source"], "web")
                self.assertEqual(attributes["sourceDetail"], source_detail)
                self.assertEqual(attributes["sourceUrls"], item["sourceUrls"])
                self.assertEqual(attributes["evidenceSnippets"], item["evidenceSnippets"])
                self.assertEqual(attributes["priceConfidence"], price_confidence)

    def test_readers_restore_new_and_legacy_quote_provenance(self):
        rows = [
            {
                "id": 11,
                "listing_title": "A4 paper",
                "price": 5.49,
                "lead_time_text": "3 days",
                "lead_time_days": 3,
                "score": 4.1,
                "source_url": "https://shopping.example/paper",
                "quote_attrs": {
                    "platform": "Google Shopping (SerpApi)",
                    "source": "web",
                    "sourceDetail": "marketplace:serpapi",
                    "sourceUrls": ["https://shopping.example/paper", "https://shop.example/details"],
                    "evidenceSnippets": ["Public EUR price", "Ships to Germany"],
                    "priceConfidence": "api",
                },
                "vendor_name": "Example Shop",
                "product_name": "A4 paper 500 sheets",
            },
            {
                "id": 12,
                "listing_title": "Legacy paper",
                "price": 4.99,
                "lead_time_text": None,
                "lead_time_days": None,
                "score": 3.8,
                "source_url": "https://legacy.example/paper",
                "quote_attrs": {"platform": "Legacy Shop"},
                "vendor_name": "Legacy Shop",
                "product_name": "Legacy paper",
            },
        ]
        async_connection = _ReadConnection(rows)
        sync_connection = _ReadConnection(rows)
        with patch("database.get_connection", side_effect=[async_connection, sync_connection]):
            async_results = asyncio.run(query_products())
            sync_results = query_products_sync()

        for results in (async_results, sync_results):
            api_item, legacy_item = results
            self.assertEqual(api_item["source"], "web")
            self.assertEqual(api_item["sourceDetail"], "marketplace:serpapi")
            self.assertEqual(api_item["sourceUrls"], ["https://shopping.example/paper", "https://shop.example/details"])
            self.assertEqual(api_item["evidenceSnippets"], ["Public EUR price", "Ships to Germany"])
            self.assertEqual(api_item["priceConfidence"], "api")
            self.assertEqual(legacy_item["source"], "database")
            self.assertEqual(legacy_item["sourceDetail"], "database")
            self.assertEqual(legacy_item["sourceUrls"], ["https://legacy.example/paper"])
            self.assertEqual(legacy_item["priceConfidence"], "extracted")

    def test_quote_readers_skip_obvious_synthetic_test_records(self):
        rows = [
            {
                "id": 21,
                "listing_title": "A4 paper",
                "price": 1.0,
                "lead_time_text": "1 day",
                "lead_time_days": 1,
                "score": 5.0,
                "source_url": "https://shop.example/a4",
                "quote_attrs": {},
                "vendor_name": "TEST Vendor GmbH",
                "product_name": "A4 paper",
            },
            {
                "id": 22,
                "listing_title": "TEST A4 paper fixture",
                "price": 1.1,
                "lead_time_text": "1 day",
                "lead_time_days": 1,
                "score": 5.0,
                "source_url": "https://trusted-shop.de/a4-test",
                "quote_attrs": {},
                "vendor_name": "Trusted Shop",
                "product_name": "TEST A4 paper fixture",
            },
            {
                "id": 23,
                "listing_title": "A4 paper",
                "price": 1.2,
                "lead_time_text": "1 day",
                "lead_time_days": 1,
                "score": 4.0,
                "source_url": "https://test-shop.example/a4",
                "quote_attrs": {},
                "vendor_name": "Example Test Shop",
                "product_name": "A4 paper",
            },
            {
                "id": 24,
                "listing_title": "A4 paper 80gsm",
                "price": 5.49,
                "lead_time_text": "3 days",
                "lead_time_days": 3,
                "score": 4.1,
                "source_url": "https://trusted-shop.de/a4-80gsm",
                "quote_attrs": {},
                "vendor_name": "Trusted Shop",
                "product_name": "A4 paper 80gsm",
            },
        ]
        async_connection = _ReadConnection(rows)
        sync_connection = _ReadConnection(rows)
        with patch("database.get_connection", side_effect=[async_connection, sync_connection]):
            async_results = asyncio.run(query_products())
            sync_results = query_products_sync()

        for results in (async_results, sync_results):
            self.assertEqual([item["id"] for item in results], ["24"])
            self.assertEqual(results[0]["vendor"], "Trusted Shop")

    def test_repeated_url_backfills_provenance_without_replacing_existing_attributes(self):
        connection = _ExistingQuoteConnection()
        item = {
            "vendor": "Example Shop",
            "product": "A4 paper 500 sheets",
            "unitPriceEur": 5.49,
            "source": "web",
            "sourceDetail": "idealo",
            "sourceUrls": ["https://www.idealo.de/preisvergleich/paper"],
            "evidenceSnippets": ["[Idealo search] A4 paper: € 5.49"],
            "priceConfidence": "extracted",
        }
        with patch("db_writer.get_connection", return_value=connection):
            save_comparison_request_and_quotes("A4 paper", "buyer@example.com", [item])

        update_execution = next(
            execution
            for execution in connection.cursor_instance.executions
            if "UPDATE quote SET attributes" in execution[0]
        )
        assert update_execution[1] is not None
        attributes = json.loads(update_execution[1][0])
        self.assertEqual(attributes["platform"], "Legacy Shop")
        self.assertEqual(attributes["criteriaScores"], {"price": 80})
        self.assertEqual(attributes["source"], "web")
        self.assertEqual(attributes["sourceDetail"], "idealo")
        self.assertEqual(attributes["sourceUrls"], item["sourceUrls"])
        self.assertEqual(attributes["evidenceSnippets"], item["evidenceSnippets"])
        self.assertEqual(attributes["priceConfidence"], "extracted")

    def test_conversation_history_snapshot_keeps_quote_provenance(self):
        connection = _ConversationConnection()
        snapshot = [{
            "vendor": "Example Shop",
            "source": "web",
            "sourceDetail": "idealo",
            "sourceUrls": ["https://www.idealo.de/preisvergleich/paper"],
            "evidenceSnippets": ["[Idealo search] A4 paper: € 5.49"],
            "priceConfidence": "extracted",
        }]
        request = conversations.NewConversation(
            module="comparison",
            query="A4 paper",
            resultsSnapshot=snapshot,
            resultCount=1,
        )
        user = AuthUser(email="buyer@example.com", name="Buyer", company="Example Co.", role="Procurement")

        with (
            patch.object(conversations, "_db_conn", return_value=connection),
            patch.object(conversations, "_ensure_table"),
            patch.object(conversations, "Json", side_effect=lambda value: value),
        ):
            record = asyncio.run(conversations.create_conversation(request, user))

        statement, parameters = connection.cursor_instance.executions[-1]
        self.assertIn("INSERT INTO conversation_history", statement)
        assert parameters is not None
        self.assertEqual(parameters[7], snapshot)
        self.assertEqual(record.resultsSnapshot, snapshot)
        restored = conversations._row_to_record({
            "id": record.id,
            "module": "comparison",
            "query": "A4 paper",
            "filters": {},
            "restore": None,
            "request_snapshot": None,
            "results_snapshot": snapshot,
            "result_count": 1,
            "candidate_names": [],
            "timestamp_ms": record.timestamp,
            "feedback": None,
        })
        self.assertEqual(restored["resultsSnapshot"], snapshot)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.auth import AuthUser
from api import comparison


class _FakeAgent:
    async def search_quotes(self, query: str, **kwargs):
        return {"intent": {"keywords": [query]}, "results": [{"vendor": "Shop", "product": "Paper"}]}


class ComparisonSyncPersistenceTest(unittest.TestCase):
    def test_sync_search_persists_before_returning(self):
        async def scenario() -> None:
            calls: list[dict] = []
            original_save = comparison.save_comparison_request_and_quotes
            comparison.save_comparison_request_and_quotes = lambda **kwargs: calls.append(kwargs)
            try:
                result = await comparison.search(
                    comparison.ComparisonSearchRequest(query="A4 paper"),
                    SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=_FakeAgent()))),
                    AuthUser(email="buyer@fuyao.com", name="Buyer", company="Fuyao", role="Buyer"),
                )
            finally:
                comparison.save_comparison_request_and_quotes = original_save

            self.assertEqual(result["results"][0]["vendor"], "Shop")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["request_text"], "A4 paper")
            self.assertEqual(calls[0]["items"], result["results"])

        asyncio.run(scenario())

    def test_sync_search_passes_custom_criteria_to_the_agent(self):
        class CaptureAgent:
            def __init__(self):
                self.kwargs = None

            async def search_quotes(self, query: str, **kwargs):
                self.kwargs = kwargs
                return {"intent": {"query": query}, "results": []}

        async def scenario() -> None:
            agent = CaptureAgent()
            original_save = comparison.save_comparison_request_and_quotes
            comparison.save_comparison_request_and_quotes = lambda **kwargs: None
            try:
                result = await comparison.search(
                    comparison.ComparisonSearchRequest(
                        query="A4 paper",
                        country="Poland",
                        criteria=[{"key": "environmental", "label": "Environmental", "weight": 60}],
                    ),
                    SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=agent))),
                    AuthUser(email="buyer@fuyao.com", name="Buyer", company="Fuyao", role="Buyer"),
                )
            finally:
                comparison.save_comparison_request_and_quotes = original_save
            self.assertEqual(result["intent"]["query"], "A4 paper")
            self.assertEqual(agent.kwargs["criteria"], [{"key": "environmental", "label": "Environmental", "weight": 60.0}])
            self.assertEqual(agent.kwargs["country"], "Poland")

        asyncio.run(scenario())

    def test_sync_search_omits_country_and_criteria_for_legacy_agent(self):
        class LegacyAgent:
            def __init__(self):
                self.kwargs = None

            async def search_quotes(
                self,
                query: str,
                min_price=None,
                max_price=None,
                delivery_time=None,
                weights=None,
            ):
                self.kwargs = {
                    "min_price": min_price,
                    "max_price": max_price,
                    "delivery_time": delivery_time,
                    "weights": weights,
                }
                return {"intent": {"query": query}, "results": [{"vendor": "Legacy Shop"}]}

        async def scenario() -> None:
            agent = LegacyAgent()
            original_save = comparison.save_comparison_request_and_quotes
            comparison.save_comparison_request_and_quotes = lambda **kwargs: None
            try:
                result = await comparison.search(
                    comparison.ComparisonSearchRequest(
                        query="A4 paper",
                        country="Poland",
                        criteria=[{"key": "environmental", "weight": 80}],
                    ),
                    SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=agent))),
                    AuthUser(email="legacy@fuyao.com", name="Legacy", company="Fuyao", role="Buyer"),
                )
            finally:
                comparison.save_comparison_request_and_quotes = original_save
            self.assertEqual(result["results"][0]["vendor"], "Legacy Shop")
            self.assertEqual(agent.kwargs["weights"], None)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

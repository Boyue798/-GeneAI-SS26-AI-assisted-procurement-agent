from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from web_research.researcher import SearXNGSearchProvider, SearchResult


class SearXNGSearchProviderTest(unittest.TestCase):
    def test_default_uses_ddgs_and_never_reuses_openai_key(self):
        provider = SearXNGSearchProvider(timeout_seconds=2)
        calls: list[tuple[str, int]] = []

        def fake_ddgs(query: str, limit: int) -> list[SearchResult]:
            calls.append((query, limit))
            return [SearchResult("Supplier", "https://supplier.example/", "B2B supplier")]

        provider._ddgs_search_sync = fake_ddgs  # type: ignore[method-assign]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unrelated-llm-key"}, clear=False):
            results = asyncio.run(provider.search("  steel  fasteners  ", max_results=50))

        self.assertEqual(calls, [("steel fasteners", 12)])
        self.assertEqual(results[0].url, "https://supplier.example/")

    def test_configured_searxng_empty_response_falls_back_to_ddgs(self):
        provider = SearXNGSearchProvider(
            searxng_base_url="https://search.internal.example",
            timeout_seconds=2,
        )
        calls: list[str] = []

        def empty_searxng(query: str, limit: int) -> list[SearchResult]:
            calls.append(f"searxng:{query}:{limit}")
            return []

        def fake_ddgs(query: str, limit: int) -> list[SearchResult]:
            calls.append(f"ddgs:{query}:{limit}")
            return [SearchResult("Fallback", "https://fallback.example/", "fallback")]

        provider._searxng_search_sync = empty_searxng  # type: ignore[method-assign]
        provider._ddgs_search_sync = fake_ddgs  # type: ignore[method-assign]
        results = asyncio.run(provider.search("industrial adhesive", max_results=3))

        self.assertEqual(calls, ["searxng:industrial adhesive:3", "ddgs:industrial adhesive:3"])
        self.assertEqual(results[0].title, "Fallback")

    def test_normalize_results_deduplicates_and_rejects_invalid_urls(self):
        results = SearXNGSearchProvider._normalize_results(
            [
                {"title": "First", "href": "https://supplier.example/catalog#detail", "body": "Catalogue"},
                {"title": "Duplicate", "url": "https://supplier.example/catalog#other", "body": "Duplicate"},
                {"title": "Invalid", "url": "ftp://supplier.example/file"},
                {"url": "https://second.example/", "description": "Second supplier"},
            ],
            limit=12,
        )

        self.assertEqual([result.url for result in results], [
            "https://supplier.example/catalog",
            "https://second.example/",
        ])
        self.assertEqual(results[0].snippet, "Catalogue")
        self.assertEqual(results[1].title, "second.example")


if __name__ == "__main__":
    unittest.main()

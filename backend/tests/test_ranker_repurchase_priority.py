from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent.ranker import LLMRanker


class RankerRepurchasePriorityTest(unittest.TestCase):
    def test_better_web_supplier_can_outrank_database_supplier_after_bonus_reduction(self):
        ranker = LLMRanker(llm=None)
        candidates = [
            {
                "id": "db-office",
                "name": "Existing Database Office Supplier",
                "category": "office",
                "country": "Germany",
                "description": "A4 paper folders office supplier",
                "products": ["A4 paper", "folders"],
                "capabilities": ["B2B wholesale"],
                "matchScore": 74,
                "source": "database",
                "repurchasePriority": "database",
            },
            {
                "id": "web-office",
                "name": "New Web Office Supplier",
                "category": "office",
                "country": "Germany",
                "description": "A4 paper folders office supplier",
                "products": ["A4 paper", "folders"],
                "capabilities": ["B2B wholesale"],
                "matchScore": 82,
                "source": "web-research-llm",
            },
        ]

        ranked = asyncio.run(ranker.rank_suppliers("A4 paper folders office Germany", candidates))

        self.assertEqual(ranked[0]["id"], "web-office")
        self.assertEqual(ranked[1]["id"], "db-office")

    def test_custom_supplier_criteria_can_change_a_close_recommendation(self):
        ranker = LLMRanker(llm=None)
        candidates = [
            {
                "id": "high-relevance-low-environment",
                "name": "High Relevance Supplier",
                "category": "office",
                "country": "Germany",
                "products": ["A4 paper"],
                "matchScore": 86,
                "criteriaScores": {"environmental": 25},
                "source": "web",
            },
            {
                "id": "slightly-lower-relevance-strong-environment",
                "name": "Certified Green Supplier",
                "category": "office",
                "country": "Germany",
                "products": ["A4 paper"],
                "matchScore": 78,
                "criteriaScores": {"environmental": 98},
                "source": "web",
            },
        ]

        ranked = asyncio.run(
            ranker.rank_suppliers(
                "A4 paper Germany",
                candidates,
                criteria=[{"key": "environmental", "label": "Environmental standard", "weight": 100}],
            )
        )

        self.assertEqual(ranked[0]["id"], "slightly-lower-relevance-strong-environment")
        self.assertEqual(ranked[0]["criteriaScores"]["environmental"], 98)
        self.assertEqual(ranked[0]["appliedCriteria"][0]["key"], "environmental")

    def test_custom_quote_criteria_are_applied_and_returned(self):
        ranker = LLMRanker(llm=None)
        candidates = [
            {
                "id": "quote-low-sustainability",
                "vendor": "Low Sustainability Shop",
                "product": "A4 paper",
                "unitPriceEur": 4.0,
                "deliveryDays": 3,
                "rating": 4.7,
                "matchScore": 84,
                "criteriaScores": {"environmental": 20},
            },
            {
                "id": "quote-high-sustainability",
                "vendor": "Green Shop",
                "product": "A4 paper",
                "unitPriceEur": 4.2,
                "deliveryDays": 3,
                "rating": 4.6,
                "matchScore": 80,
                "criteriaScores": {"environmental": 98},
            },
        ]

        ranked = asyncio.run(
            ranker.rank_quotes(
                "A4 paper",
                candidates,
                weights={"price": 40, "delivery": 35, "rating": 25},
                criteria=[{"key": "environmental", "weight": 100}],
            )
        )

        self.assertEqual(ranked[0]["id"], "quote-high-sustainability")
        self.assertEqual(ranked[0]["criteriaScores"]["environmental"], 98)
        self.assertEqual(ranked[0]["appliedCriteria"][0]["key"], "environmental")

    def test_history_and_chinese_custom_labels_resolve_to_evidence(self):
        database_supplier = {
            "source": "database",
            "preferred": False,
            "criteriaScores": {"供应商信誉": 88},
        }

        self.assertEqual(
            LLMRanker._supplier_criterion_score(database_supplier, "supplier_history", "Supplier history"),
            92.0,
        )
        self.assertEqual(
            LLMRanker._supplier_criterion_score(database_supplier, "custom_criterion", "供应商信誉"),
            88.0,
        )


if __name__ == "__main__":
    unittest.main()

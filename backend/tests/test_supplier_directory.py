from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.auth import AuthUser
from api import suppliers as supplier_api


class _FakeAgent:
    def __init__(self) -> None:
        self.refreshes = 0

    def refresh_local_catalog(self) -> None:
        self.refreshes += 1


class SupplierDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_path = supplier_api._LOCAL_DIRECTORY_PATH
        self._old_connection = supplier_api.get_connection
        self.temp = tempfile.TemporaryDirectory()
        supplier_api._LOCAL_DIRECTORY_PATH = Path(self.temp.name) / "suppliers.json"
        supplier_api.get_connection = lambda: None
        self.agent = _FakeAgent()
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=self.agent)))
        self.user = AuthUser(
            email="buyer@fuyao.com",
            name="Buyer",
            company="Fuyao",
            role="Procurement Manager",
        )

    def tearDown(self) -> None:
        supplier_api._LOCAL_DIRECTORY_PATH = self._old_path
        supplier_api.get_connection = self._old_connection
        self.temp.cleanup()

    def test_offline_directory_crud_and_refresh(self) -> None:
        async def scenario() -> None:
            created = await supplier_api.create_supplier(
                supplier_api.SupplierCreate(
                    name="Local Seal GmbH",
                    country="Germany",
                    city="Berlin",
                    productName="EPDM seal",
                    certifications=["ISO 9001", "ISO 14001"],
                    tags=["已合作供应商", "历史表现良好"],
                    preferred=True,
                    historicalPerformance="On-time delivery 97%",
                    minimumOrderQuantity="500 m",
                    productionCapacity="1.2M m / month",
                    environmentalStandards=["ISO 14001"],
                    criteriaScores={"quality": 91, "reputation": 87},
                    notes="Verified in supplier audit.",
                ),
                self.request,
                self.user,
            )
            self.assertTrue(created.id.startswith("local-"))
            self.assertEqual(created.source, "database")
            self.assertEqual(created.origin, "internal")
            self.assertEqual(created.criteriaScores["quality"], 91)
            self.assertEqual(created.updatedAt, created.createdAt)

            listed = await supplier_api.list_suppliers(
                self.user, query="seal", country="Germany", tag="历史表现良好"
            )
            self.assertEqual([record.name for record in listed], ["Local Seal GmbH"])

            updated = await supplier_api.update_supplier(
                created.id,
                supplier_api.SupplierUpdate(country="Poland", notes="Reviewed 2026-07-23"),
                self.request,
                self.user,
            )
            self.assertEqual(updated.country, "Poland")
            self.assertEqual(updated.notes, "Reviewed 2026-07-23")
            self.assertGreaterEqual(updated.updatedAt, created.updatedAt)

            await supplier_api.delete_supplier(created.id, self.request, SimpleNamespace(), self.user)
            after_delete = await supplier_api.list_suppliers(self.user)
            self.assertEqual(after_delete, [])
            self.assertEqual(self.agent.refreshes, 3)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

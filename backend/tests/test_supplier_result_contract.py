from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent.procurement_agent import ProcurementAgent


class SupplierResultContractTest(unittest.TestCase):
    def test_local_and_web_duplicates_keep_local_provenance_and_web_evidence(self):
        merged = ProcurementAgent._merge_supplier_candidates(
            [
                {
                    "id": "local-1",
                    "name": "Acme Seals GmbH",
                    "website": "https://www.acme.example/",
                    "source": "database",
                    "sourceDetail": "internal",
                    "matchScore": 76,
                    "products": ["EPDM seal"],
                }
            ],
            [
                {
                    "id": "web-1",
                    "name": "ACME Seals",
                    "website": "https://acme.example/products",
                    "source": "web",
                    "matchScore": 84,
                    "phone": "+49 30 123",
                    "sourceUrls": ["https://acme.example/products"],
                    "evidenceSnippets": ["EPDM seal product listing"],
                }
            ],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "database")
        self.assertIn("web", merged[0]["sourceDetail"])
        self.assertEqual(merged[0]["phone"], "+49 30 123")
        self.assertIn("https://acme.example/products", merged[0]["sourceUrls"])

    def test_directory_profile_enriches_the_matching_local_company_without_duplicate(self):
        merged = ProcurementAgent._merge_supplier_candidates(
            [
                {
                    "id": "local-henkel",
                    "name": "Henkel AG & Co. KGaA",
                    "website": "https://www.henkel-adhesives.com",
                    "source": "database",
                    "products": ["Teroson PU 8597"],
                }
            ],
            [
                {
                    "id": "web-henkel",
                    "name": "HENKEL TEROSON GMBH in Heidelberg, Klebstoffe auf europages",
                    "website": "https://www.europages.de/HENKEL-TEROSON-GMBH/DEU352112-00101.html",
                    "source": "web",
                    "products": ["Teroson"],
                    "phone": "+49 211 797 0",
                    "sourceUrls": ["https://www.europages.de/HENKEL-TEROSON-GMBH/DEU352112-00101.html"],
                }
            ],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "database")
        self.assertIn("Teroson", merged[0]["products"])
        self.assertIn("europages", " ".join(merged[0]["sourceUrls"]))

    def test_contract_keeps_unknown_fields_explicitly_unknown(self):
        result = ProcurementAgent._ensure_supplier_contract(
            {"name": "Evidence-light Supplier", "source": "web"},
            SimpleNamespace(category="equipment"),
            default_source="web",
        )
        self.assertEqual(result["category"], "equipment")
        self.assertIsNone(result["unitPriceEur"])
        self.assertIsNone(result["unitPrice"])
        self.assertEqual(result["deliveryLabel"], "需确认交期")
        self.assertEqual(result["deliveryLeadTime"], "需确认交期")
        self.assertEqual(result["paymentTerms"], "需确认付款方式")
        self.assertEqual(result["verificationStatus"], "needs-review")
        self.assertIn("缺少官网链接", result["verificationNotes"])

    def test_contract_exposes_sourcing_table_compatibility_fields(self):
        result = ProcurementAgent._ensure_supplier_contract(
            {
                "name": "Quoted Supplier",
                "unitPriceEur": 12.5,
                "deliveryLabel": "5 days",
                "paymentLabel": "Net 30",
            },
            SimpleNamespace(category="equipment"),
            default_source="web",
        )
        self.assertEqual(result["unitPrice"], 12.5)
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["deliveryLeadTime"], "5 days")
        self.assertEqual(result["paymentTerms"], "Net 30")

    def test_contract_promotes_explicit_eur_evidence_to_price(self):
        result = ProcurementAgent._ensure_supplier_contract(
            {
                "name": "Evidence priced supplier",
                "source": "web",
                "website": "https://supplier.example/paper",
                "products": ["A4 paper"],
                "evidenceSnippets": ["80g A4 paper, 28,49 € per carton, minimum order 5 cartons"],
            },
            SimpleNamespace(category="paper"),
            default_source="web",
        )
        self.assertEqual(result["unitPriceEur"], 28.49)
        self.assertEqual(result["priceConfidence"], "extracted")
        self.assertEqual(result["unitLabel"], "€ 28.49")

        ranged = ProcurementAgent._ensure_supplier_contract(
            {
                "name": "Range priced supplier",
                "source": "web",
                "website": "https://supplier.example/paper-range",
                "products": ["A4 paper"],
                "evidenceSnippets": ["1,12 - 1,74 € per pack"],
            },
            SimpleNamespace(category="paper"),
            default_source="web",
        )
        self.assertEqual(ranged["unitPriceEur"], 1.74)
        self.assertEqual(ranged["unitLabel"], "€ 1.12–1.74")

    def test_web_verification_rejects_url_or_numeric_id_as_contact(self):
        result = ProcurementAgent._ensure_supplier_contract(
            {
                "name": "Crawler Supplier",
                "source": "web",
                "website": "https://crawler.example/company",
                "products": ["A4 paper"],
                "certifications": ["ISO 9001"],
                "phone": "https://crawler.example/contact?page=42",
                "email": "page-42",
                "verificationStatus": "verified",
            },
            SimpleNamespace(category="paper"),
            default_source="web",
        )
        self.assertEqual(result["verificationStatus"], "needs-review")
        self.assertIn("缺少可验证联系方式", result["verificationNotes"])

        numeric_id = ProcurementAgent._ensure_supplier_contract(
            {
                "name": "Numeric ID Supplier",
                "source": "web",
                "website": "https://supplier.example/company",
                "products": ["A4 paper"],
                "certifications": ["ISO 9001"],
                "phone": "123456789",
            },
            SimpleNamespace(category="paper"),
            default_source="web",
        )
        self.assertEqual(numeric_id["verificationStatus"], "needs-review")

    def test_web_verification_accepts_formatted_phone_or_valid_email(self):
        formatted_phone = ProcurementAgent._supplier_verification(
            website="https://supplier.example/company",
            products=["A4 paper"],
            certifications=["ISO 9001"],
            phone="+49 30 1234567",
            email="",
        )
        self.assertEqual(formatted_phone[0], "verified")

        valid_email = ProcurementAgent._supplier_verification(
            website="https://supplier.example/company",
            products=["A4 paper"],
            certifications=["ISO 9001"],
            phone="Page 42",
            email="sales@supplier.example",
        )
        self.assertEqual(valid_email[0], "verified")

    def test_web_verification_requires_each_requested_certification(self):
        status, notes = ProcurementAgent._supplier_verification(
            website="https://supplier.example/company",
            products=["Teroson windscreen adhesive"],
            certifications=["ISO 9001"],
            phone="+49 30 1234567",
            email="",
            required_certifications=["IATF 16949"],
        )

        self.assertEqual(status, "needs-review")
        self.assertIn("未找到所需认证：IATF 16949", notes)

    def test_year_range_is_not_treated_as_phone(self):
        self.assertFalse(ProcurementAgent._valid_phone("2023-2026"))
        self.assertEqual(
            ProcurementAgent._supplier_verification(
                website="https://supplier.example/company",
                products=["A4 paper"],
                certifications=["ISO 9001"],
                phone="2023-2026",
                email="",
            )[0],
            "needs-review",
        )

    def test_directory_landing_pages_are_not_supplier_rows(self):
        self.assertTrue(
            ProcurementAgent._is_supplier_directory_page(
                "https://www.europages.co.uk/en/showroom/a4-papers-manufacturers/p-3"
            )
        )
        self.assertFalse(
            ProcurementAgent._is_supplier_directory_page(
                "https://www.europages.de/company/acme-gmbh/products/paper"
            )
        )
        self.assertTrue(
            ProcurementAgent._is_supplier_noise_page(
                "Web supplier",
                {"description": "Site Under Maintenance. We will be back soon."},
            )
        )
        self.assertTrue(
            ProcurementAgent._is_supplier_noise_page(
                "europages, b2b sourcing App",
                {"website": "https://apps.apple.com/tr/app/europages/id123"},
            )
        )

    def test_web_normalization_has_a_second_defense_against_registry_pages(self):
        agent = ProcurementAgent.__new__(ProcurementAgent)
        result = asyncio.run(
            agent._normalize_web_suppliers(
                [
                    {
                        "name": "Manufacturer Directory Europe: Source from Verified Suppliers",
                        "website": "https://manufacturer-directory.example/europe",
                        "description": "Supplier directory listing",
                        "products": ["polyurethane adhesive"],
                        "phone": "+49 30 1234567",
                        "source": "web",
                    }
                ],
                SimpleNamespace(category="glassAdhesive"),
            )
        )
        self.assertEqual(result, [])

    def test_orphaned_quote_is_not_rankable_as_supplier(self):
        self.assertFalse(ProcurementAgent._quote_has_decision_vendor({"vendor": "Unknown"}))
        self.assertFalse(ProcurementAgent._quote_has_decision_vendor({"vendor": ""}))
        self.assertTrue(ProcurementAgent._quote_has_decision_vendor({"vendor": "Böttcher AG"}))


if __name__ == "__main__":
    unittest.main()

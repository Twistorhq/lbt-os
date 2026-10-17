"""Tests for the TW-571 M4 roofing pro pack (Zeke Okafor).

Estimating takeoff (exact-cent math), suppliers + purchase orders, and
storm-triggered campaigns. PII doctrine (TW-552): supplier contacts bronze,
masked in responses. TCPA: storm campaigns target existing customers only.
"""

import unittest
from decimal import Decimal
from unittest import mock

ORG_ID = "org-m4-test"
OTHER_ORG = "org-m4-other"


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._rows = list(db._tables.get(table, []))
        self._pending_insert = None
        self._pending_update = None

    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def in_(self, col, values):
        self._rows = [r for r in self._rows if r.get(col) in values]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def single(self):
        return self

    def maybe_single(self):
        return self

    def insert(self, row):
        self._pending_insert = dict(row)
        return self

    def update(self, values):
        self._pending_update = dict(values)
        return self

    def delete(self):
        for r in list(self._rows):
            self._db._tables[self._table].remove(r)
        return FakeResult(self._rows)

    def execute(self):
        if self._pending_insert is not None:
            row = self._pending_insert
            row.setdefault("id", f"row-{len(self._db._tables[self._table])}")
            self._db._tables[self._table].append(row)
            self._pending_insert = None
            return FakeResult([row])
        if self._pending_update is not None:
            for r in self._rows:
                r.update(self._pending_update)
            self._pending_update = None
            return FakeResult(self._rows)
        return FakeResult(self._rows)


class FakeDB:
    def __init__(self):
        self._tables = {
            "organizations": [],
            "quotes": [],
            "suppliers": [],
            "purchase_orders": [],
            "customers": [],
            "leads": [],
            "storm_events": [],
            "storm_campaigns": [],
            "toll_events": [],
        }

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self, name)


def _seed(db):
    db._tables["organizations"].append(
        {
            "id": ORG_ID,
            "name": "Test Roofing",
            "plan": "trades_pro",
            "subscription_status": "active",
        }
    )
    db._tables["quotes"].append(
        {
            "id": "quote-1",
            "org_id": ORG_ID,
            "customer_id": "cust-1",
            "line_items": [],
            "total": 0,
            "status": "draft",
            "attributes": {},
        }
    )
    db._tables["quotes"].append(
        {
            "id": "quote-other",
            "org_id": OTHER_ORG,
            "customer_id": "cust-9",
            "line_items": [],
            "total": 0,
            "status": "draft",
            "attributes": {},
        }
    )
    db._tables["customers"].append(
        {
            "id": "cust-1",
            "org_id": ORG_ID,
            "name": "Adaeze Okafor",
            "phone": "+13035550111",
            "address": "123 Main St, Denver, CO 80202",
        }
    )
    db._tables["customers"].append(
        {
            "id": "cust-2",
            "org_id": ORG_ID,
            "name": "Chidi Eze",
            "phone": "+13035550222",
            "address": "456 Oak Ave, Aurora, CO 80012",
        }
    )


class TakeoffMathTests(unittest.TestCase):
    """Pure math: exact cents, no DB."""

    def test_takeoff_exact_cents(self):
        from app.services.roofing_service import compute_takeoff

        result = compute_takeoff(
            squares=Decimal("28.5"),
            pitch="6/12",
            waste_pct=Decimal("10"),
            material="architectural_shingle",
        )
        items = result["line_items"]
        material = next(i for i in items if "Material" in i["description"])
        labor = next(i for i in items if "Labor" in i["description"])
        # 28.5 * 1.10 = 31.35 squares of material
        self.assertEqual(Decimal(str(material["quantity"])), Decimal("31.35"))
        # 31.35 * $145.00 = $4,545.75
        self.assertEqual(Decimal(str(material["amount"])), Decimal("4545.75"))
        # 28.5 * $95.00 = $2,707.50
        self.assertEqual(Decimal(str(labor["amount"])), Decimal("2707.50"))
        self.assertEqual(Decimal(str(result["total"])), Decimal("7253.25"))

    def test_takeoff_unknown_material_rejected(self):
        from app.services.roofing_service import TakeoffError, compute_takeoff

        with self.assertRaises(TakeoffError):
            compute_takeoff(Decimal("10"), "4/12", Decimal("10"), "unobtainium")

    def test_takeoff_nonpositive_squares_rejected(self):
        from app.services.roofing_service import TakeoffError, compute_takeoff

        with self.assertRaises(TakeoffError):
            compute_takeoff(
                Decimal("0"), "4/12", Decimal("10"), "architectural_shingle"
            )


class RoofingRouterTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.auth import AuthContext, get_auth
        from app.database import get_db
        from app.main import app

        self.db = FakeDB()
        _seed(self.db)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_auth] = lambda: AuthContext(
            user_id="user-1",
            org_id=ORG_ID,
            plan="trades_pro",
            is_owner=True,
        )
        self.client = TestClient(app, raise_server_exceptions=False)
        self._overrides = (app, get_db, get_auth)

    def tearDown(self):
        app, get_db, get_auth = self._overrides
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth, None)

    def test_takeoff_endpoint_updates_quote(self):
        resp = self.client.post(
            "/api/v1/roofing/quotes/quote-1/takeoff",
            json={
                "squares": 28.5,
                "pitch": "6/12",
                "waste_pct": 10,
                "material": "architectural_shingle",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 7253.25)
        self.assertIn("takeoff", body["attributes"])
        quote = self.db._tables["quotes"][0]
        self.assertEqual(quote["attributes"]["takeoff"]["squares"], 28.5)

    def test_takeoff_unknown_material_422(self):
        resp = self.client.post(
            "/api/v1/roofing/quotes/quote-1/takeoff",
            json={
                "squares": 10,
                "pitch": "4/12",
                "waste_pct": 10,
                "material": "unobtainium",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_takeoff_cross_org_quote_404(self):
        # No org enumeration: another org's quote reads as not found.
        resp = self.client.post(
            "/api/v1/roofing/quotes/quote-other/takeoff",
            json={
                "squares": 10,
                "pitch": "4/12",
                "waste_pct": 10,
                "material": "architectural_shingle",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_supplier_crud_scoped_to_org(self):
        resp = self.client.post(
            "/api/v1/roofing/suppliers",
            json={
                "name": "ABC Supply",
                "branch": "Denver",
                "ordering_email": "orders@abcsupply.example",
            },
        )
        self.assertEqual(resp.status_code, 201)
        _created_id = resp.json()["id"]
        # PII masked in response.
        self.assertNotIn(
            "orders@abcsupply.example", resp.json().get("ordering_email", "")
        )
        resp = self.client.get("/api/v1/roofing/suppliers")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(s["org_id"] == ORG_ID for s in resp.json()))

    def test_po_send_transitions_status(self):
        s = self.client.post(
            "/api/v1/roofing/suppliers",
            json={
                "name": "ABC Supply",
                "ordering_email": "orders@abcsupply.example",
            },
        ).json()
        po = self.client.post(
            "/api/v1/roofing/purchase-orders",
            json={
                "supplier_id": s["id"],
                "line_items": [
                    {
                        "sku": "SH-ARCH",
                        "description": "Architectural shingle",
                        "qty": 32,
                        "unit_cost": 145.00,
                    },
                ],
            },
        ).json()
        self.assertEqual(po["status"], "draft")
        with mock.patch("app.services.roofing_service._send_supplier_email") as mail:
            resp = self.client.post(f"/api/v1/roofing/purchase-orders/{po['id']}/send")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "sent")
        self.assertEqual(resp.json()["total_cost"], 4640.00)
        mail.assert_called_once()

    def test_storm_recent_filters_roofing_events(self):
        nws_payload = {
            "features": [
                {
                    "id": "nws-1",
                    "properties": {
                        "event": "Severe Thunderstorm Warning",
                        "headline": "Hail up to 1.75in",
                        "areaDesc": "Denver County; 80202",
                        "severity": "Severe",
                        "effective": "2026-10-16T18:00:00Z",
                        "ends": "2026-10-16T19:00:00Z",
                    },
                },
                {
                    "id": "nws-2",
                    "properties": {
                        "event": "Dense Fog Advisory",
                        "headline": "Fog",
                        "areaDesc": "Denver County",
                        "severity": "Minor",
                        "effective": "2026-10-16T18:00:00Z",
                        "ends": "2026-10-16T19:00:00Z",
                    },
                },
            ]
        }
        with mock.patch(
            "app.services.roofing_service._nws_get", return_value=nws_payload
        ):
            with mock.patch(
                "app.services.roofing_service._geocode_zip",
                return_value=(39.75, -104.99),
            ):
                resp = self.client.get("/api/v1/roofing/storms/recent?zip=80202")
        self.assertEqual(resp.status_code, 200)
        events = resp.json()["events"]
        self.assertEqual(len(events), 1)
        self.assertIn("Thunderstorm", events[0]["event_type"])

    def test_storm_service_down_502(self):
        with mock.patch(
            "app.services.roofing_service._nws_get",
            side_effect=RuntimeError("unreachable"),
        ):
            resp = self.client.get("/api/v1/roofing/storms/recent?zip=80202")
        self.assertEqual(resp.status_code, 502)

    def test_campaign_creates_leads_for_matching_customers(self):
        self.db._tables["storm_events"].append(
            {
                "id": "storm-1",
                "org_id": ORG_ID,
                "nws_event_id": "nws-1",
                "event_type": "Severe Thunderstorm Warning",
                "headline": "Hail",
                "area_desc": "Denver County; 80202",
                "severity": "Severe",
            }
        )
        with mock.patch("app.services.roofing_service._send_sms") as sms:
            resp = self.client.post(
                "/api/v1/roofing/storms/campaigns",
                json={
                    "storm_event_id": "storm-1",
                    "message_body": "Hail reported near you — free storm inspection?",
                },
            )
        self.assertEqual(resp.status_code, 201)
        # Only cust-1 (80202) matches, not cust-2 (80012).
        self.assertEqual(resp.json()["recipient_count"], 1)
        sms.assert_called_once()
        leads = self.db._tables["storm_campaigns"]
        self.assertEqual(len(leads), 1)
        storm_leads = [
            lead
            for lead in self.db._tables["leads"]
            if lead.get("source") == "storm_campaign"
        ]
        self.assertEqual(len(storm_leads), 1)

    def test_campaign_inline_event_stored_first(self):
        with mock.patch("app.services.roofing_service._send_sms", return_value=True):
            resp = self.client.post(
                "/api/v1/roofing/storms/campaigns",
                json={
                    "event": {
                        "nws_event_id": "nws-9",
                        "event_type": "Severe Thunderstorm Warning",
                        "headline": "Hail",
                        "area_desc": "Denver County; 80202",
                        "severity": "Severe",
                    },
                    "message_body": "Hail reported near you — free inspection?",
                },
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["recipient_count"], 1)
        self.assertEqual(len(self.db._tables["storm_events"]), 1)


if __name__ == "__main__":
    unittest.main()

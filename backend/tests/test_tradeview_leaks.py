"""Tests for the TW-303 Leak Map (Zeke Okafor).

Every missed follow-up, stale quote, and at-risk customer rendered on the
Trade View map as a glowing leak WITH a dollar figure. Findings come from
the real leak-engine scan; dollar figures are never fabricated — a pin
shows a figure only when its detector provided one.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from app.auth import AuthContext, get_auth
from app.main import app
from app.routers import tradeview as tv_router
from app.services.geocode import GeocodeResult


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, db, name):
        self._db = db
        self._name = name
        self._rows = list(db.tables.get(name, []))

    def select(self, _cols):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return _Result(self._rows)


class FakeDb:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def table(self, name):
        return _FakeTable(self, name)


ORG = "org-9"


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class _ClientCtx:
    def __init__(self, db):
        self._db = db
        self._real = tv_router.get_db

    def __enter__(self):
        tv_router.get_db = lambda: self._db
        app.dependency_overrides[get_auth] = lambda: AuthContext("user_1", ORG, "pro")
        return TestClient(app)

    def __exit__(self, *exc):
        tv_router.get_db = self._real
        app.dependency_overrides.clear()
        return False


def _brief(findings):
    return {
        "org_id": ORG,
        "vertical": "hvac",
        "generated_at": _days_ago(0),
        "what_happened": [],
        "what_will_happen": [],
        "what_should_we_do": findings,
        "totals": {
            "findings": len(findings),
            "dollars_at_stake": round(
                sum(f.get("estimated_value") or 0 for f in findings), 2
            ),
        },
        "data_status": {},
    }


def _finding(**kw):
    f = {
        "ladder": "should",
        "severity": "urgent",
        "title": "2 stalled quotes still winnable",
        "detail": "Quotes that died without follow-up.",
        "count": 2,
        "estimated_value": 13000.0,
        "entities": [
            {"id": "q-1", "name": "Acme Heating", "total": 8500.0, "days_idle": 21},
            {"id": "q-2", "name": "Beta Corp", "total": 4500.0, "days_idle": 16},
        ],
        "recommended_action": "Two-touch follow-up this week, highest value first.",
        "detector": "quote-resurrection",
        "vertical": "hvac",
        "is_demo": False,
    }
    f.update(kw)
    return f


def _db_with_addresses():
    return FakeDb(
        {
            "organizations": [{"id": ORG, "industry": "hvac"}],
            "customers": [
                {
                    "id": "c-1",
                    "org_id": ORG,
                    "name": "Acme Heating",
                    "address": "123 Colfax Ave, Denver, CO",
                },
            ],
            "leads": [],
            "quotes": [
                {
                    "id": "q-1",
                    "org_id": ORG,
                    "customer_name": "Acme Heating",
                    "address": "123 Colfax Ave, Denver, CO",
                },
                {
                    "id": "q-2",
                    "org_id": ORG,
                    "customer_name": "Beta Corp",
                    "address": None,
                },
            ],
        }
    )


def _geo(address, **kwargs):
    return GeocodeResult(lat=39.7, lon=-105.0, display_name=address, source="photon")


class LeaksEndpointTest(unittest.TestCase):
    def test_leaks_come_from_the_real_scan(self):
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "live")
        # q-1 has an address -> pinned with its own dollar figure.
        pins = {p["entity_id"]: p for p in body["leaks"]}
        self.assertIn("q-1", pins)
        self.assertEqual(pins["q-1"]["dollars"], 8500.0)
        self.assertAlmostEqual(pins["q-1"]["lat"], 39.7)
        self.assertEqual(pins["q-1"]["detector"], "quote-resurrection")
        self.assertEqual(pins["q-1"]["entity_name"], "Acme Heating")

    def test_headline_totals(self):
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        body = resp.json()
        self.assertEqual(body["totals"]["dollars_at_stake"], 13000.0)
        self.assertIn("13,000", body["headline"])
        self.assertIn("totals", body)

    def test_unlocated_entities_counted_not_dropped(self):
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        body = resp.json()
        pins = {p["entity_id"]: p for p in body["leaks"]}
        # q-2 (Beta Corp) has no address: not pinned, but counted honestly.
        self.assertNotIn("q-2", pins)
        self.assertGreaterEqual(body["totals"]["unlocated"], 1)

    def test_dedupe_keeps_highest_dollar_pin(self):
        f1 = _finding(entities=[{"id": "q-1", "name": "Acme Heating", "total": 8500.0}])
        f2 = _finding(
            detector="other-detector",
            entities=[{"id": "q-1", "name": "Acme Heating", "total": 2000.0}],
        )
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([f1, f2])
                ),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        pins = [p for p in resp.json()["leaks"] if p["entity_id"] == "q-1"]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]["dollars"], 8500.0)

    def test_no_dollar_figure_is_never_fabricated(self):
        f = _finding(
            estimated_value=None, entities=[{"id": "q-1", "name": "Acme Heating"}]
        )  # no total
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(tv_router, "run_leak_scan", return_value=_brief([f])),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        pins = {p["entity_id"]: p for p in resp.json()["leaks"]}
        self.assertIn("q-1", pins)
        self.assertIsNone(pins["q-1"]["dollars"])

    def test_empty_scan_is_honest(self):
        with _ClientCtx(_db_with_addresses()) as client:
            with (
                mock.patch.object(tv_router, "run_leak_scan", return_value=_brief([])),
                mock.patch.object(tv_router, "geocode_cached", side_effect=_geo),
            ):
                resp = client.get("/api/v1/tradeview/leaks")
        body = resp.json()
        self.assertEqual(body["leaks"], [])
        self.assertEqual(body["totals"]["dollars_at_stake"], 0)
        self.assertTrue(body["headline"])

    def test_requires_auth(self):
        resp = TestClient(app).get("/api/v1/tradeview/leaks")
        self.assertIn(resp.status_code, (401, 403))


class LeaksIntegrationTest(unittest.TestCase):
    """End to end with the REAL leak engine: stalled quotes become leak pins."""

    def test_real_engine_stalled_quotes_become_pins(self):
        db = FakeDb(
            {
                "organizations": [{"id": ORG, "industry": "hvac"}],
                "customers": [],
                "leads": [],
                "quotes": [
                    {
                        "id": "q-1",
                        "org_id": ORG,
                        "customer_name": "Acme Heating",
                        "address": "123 Colfax Ave, Denver, CO",
                        "status": "sent",
                        "sent_at": _days_ago(20),
                        "follow_up_count": 0,
                        "total": 8500.0,
                    }
                ],
                "service_assets": [],
                "service_plans": [],
            }
        )
        with _ClientCtx(db) as client:
            with mock.patch.object(tv_router, "geocode_cached", side_effect=_geo):
                resp = client.get("/api/v1/tradeview/leaks")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        pins = {p["entity_id"]: p for p in body["leaks"]}
        self.assertIn("q-1", pins)
        self.assertEqual(pins["q-1"]["dollars"], 8500.0)
        self.assertGreater(body["totals"]["dollars_at_stake"], 0)


class LeadAddressModelTest(unittest.TestCase):
    def test_lead_models_accept_address(self):
        from app.models.lead import LeadCreate, LeadUpdate

        c = LeadCreate(name="Cousin Ray", address="456 Main St, Denver, CO")
        self.assertEqual(c.address, "456 Main St, Denver, CO")
        u = LeadUpdate(address="789 Oak St, Denver, CO")
        self.assertEqual(u.address, "789 Oak St, Denver, CO")
        # Missing address stays None — old rows without the column are fine.
        self.assertIsNone(LeadCreate(name="No Address").address)


if __name__ == "__main__":
    unittest.main()

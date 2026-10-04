"""Tests for the TW-301 Trade View real-backend API (Zeke Okafor).

Pins, dossiers, diagnostics, and layers served from real pipeline output
(customers, leads, leak-engine brief) instead of fictional sample data.
Signed-out visitors keep the clearly-labeled sample experience (TW-295);
every endpoint here requires auth and serves only the caller's own org.
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.auth import AuthContext, get_auth
from app.main import app
from app.routers import tradeview as tv_router

# ---------------------------------------------------------------------------
# Supabase stand-in (same shape as test_analytics_events.py)
# ---------------------------------------------------------------------------


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
CUSTOMERS = [
    {
        "id": "c-1",
        "org_id": ORG,
        "name": "Aunt May's Heating",
        "address": "123 Colfax Ave, Denver, CO",
        "phone": "303-555-0101",
        "trade": "HVAC",
    },
    {
        "id": "c-2",
        "org_id": ORG,
        "name": "Uncle June's Plumbing",
        "address": None,
        "phone": "303-555-0102",
        "trade": "Plumbing",
    },
]
LEADS = [
    {
        "id": "l-1",
        "org_id": ORG,
        "name": "Cousin Ray",
        "status": "proposal",
        "estimated_value": 4500.0,
    },
]


def _db():
    return FakeDb(
        {
            "organizations": [{"id": ORG, "industry": "hvac"}],
            "customers": CUSTOMERS,
            "leads": LEADS,
        }
    )


class _ClientCtx:
    """Context manager wiring FakeDb + auth override for one test."""

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


# ---------------------------------------------------------------------------
# Geocoder (vendored keyless: Photon -> Nominatim, stdlib only)
# ---------------------------------------------------------------------------


class GeocodeTest(unittest.TestCase):
    def test_empty_address_returns_none_without_network(self):
        from app.services import geocode as g

        with mock.patch.object(g, "_get", side_effect=AssertionError("no network")):
            self.assertIsNone(g.geocode(""))
            self.assertIsNone(g.geocode("   "))
            self.assertIsNone(g.geocode(None))

    def test_photon_result_shape(self):
        from app.services import geocode as g

        payload = b'{"features": [{"geometry": {"coordinates": [-104.9903, 39.7392]}, "properties": {"name": "Denver", "city": "Denver", "state": "Colorado"}}]}'
        with mock.patch.object(g, "_get", return_value=payload):
            r = g.geocode("Denver, CO")
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.lat, 39.7392)
        self.assertAlmostEqual(r.lon, -104.9903)
        self.assertEqual(r.source, "photon")

    def test_cache_hit_avoids_network(self):
        from app.services import geocode as g

        cache = g.GeocodeCache(ttl_s=600)
        payload = b'{"features": [{"geometry": {"coordinates": [-105.0, 39.7]}, "properties": {"name": "X"}}]}'
        with mock.patch.object(g, "_get", return_value=payload) as m:
            first = cache.get("123 Main St, Denver, CO")
            second = cache.get("123 Main St, Denver, CO")
        self.assertEqual(m.call_count, 1)
        self.assertEqual(first, second)

    def test_network_failure_returns_none(self):
        from app.services import geocode as g

        with mock.patch.object(g, "_get", return_value=None):
            self.assertIsNone(g.geocode("Nowhere, XX"))


# ---------------------------------------------------------------------------
# GET /api/v1/tradeview/pins
# ---------------------------------------------------------------------------


class PinsEndpointTest(unittest.TestCase):
    def _geo(self, address):
        from app.services.geocode import GeocodeResult

        return GeocodeResult(
            lat=39.7, lon=-105.0, display_name=address, source="photon"
        )

    def test_pins_come_from_real_data(self):
        with _ClientCtx(_db()) as client:
            with mock.patch.object(tv_router, "geocode_cached", side_effect=self._geo):
                resp = client.get("/api/v1/tradeview/pins")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "live")
        pins = {p["id"]: p for p in body["pins"]}
        # Customer with an address gets pinned with real coordinates.
        self.assertIn("c-1", pins)
        self.assertEqual(pins["c-1"]["kind"], "customer")
        self.assertAlmostEqual(pins["c-1"]["lat"], 39.7)
        self.assertAlmostEqual(pins["c-1"]["lng"], -105.0)
        self.assertEqual(pins["c-1"]["name"], "Aunt May's Heating")

    def test_unlocated_entities_counted_honestly(self):
        with _ClientCtx(_db()) as client:
            with mock.patch.object(tv_router, "geocode_cached", side_effect=self._geo):
                resp = client.get("/api/v1/tradeview/pins")
        body = resp.json()
        pins = {p["id"]: p for p in body["pins"]}
        # Customer without address + lead without address: not pinned, counted.
        self.assertNotIn("c-2", pins)
        self.assertNotIn("l-1", pins)
        self.assertGreaterEqual(body["unlocated_count"], 2)

    def test_geocode_failure_degrades_to_unlocated(self):
        with _ClientCtx(_db()) as client:
            with mock.patch.object(tv_router, "geocode_cached", return_value=None):
                resp = client.get("/api/v1/tradeview/pins")
        body = resp.json()
        self.assertEqual(body["pins"], [])
        self.assertEqual(body["unlocated_count"], 3)

    def test_requires_auth(self):
        resp = TestClient(app).get("/api/v1/tradeview/pins")
        self.assertIn(resp.status_code, (401, 403))


# ---------------------------------------------------------------------------
# GET /api/v1/tradeview/dossiers/{kind}/{id}
# ---------------------------------------------------------------------------


class DossierEndpointTest(unittest.TestCase):
    def test_customer_dossier_has_facts(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/dossiers/customer/c-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["name"], "Aunt May's Heating")
        self.assertEqual(body["kind"], "customer")
        self.assertIn("address", body["facts"])
        self.assertIn("provenance", body)

    def test_lead_dossier_has_facts(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/dossiers/lead/l-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["name"], "Cousin Ray")
        self.assertEqual(body["facts"]["status"], "proposal")

    def test_unknown_entity_is_404(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/dossiers/customer/nope")
        self.assertEqual(resp.status_code, 404)

    def test_bad_kind_is_404(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/dossiers/vendor/c-1")
        self.assertEqual(resp.status_code, 404)

    def test_cross_org_entity_is_404(self):
        other = FakeDb(
            {
                "customers": [
                    {
                        "id": "c-9",
                        "org_id": "org-other",
                        "name": "Stranger Co",
                        "address": "1 Main St",
                    }
                ],
                "leads": [],
            }
        )
        with _ClientCtx(other) as client:
            resp = client.get("/api/v1/tradeview/dossiers/customer/c-9")
        # org-other's row is invisible to org-9: same as not found, never leaks.
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# GET /api/v1/tradeview/diagnostics/{kind}/{id}
# ---------------------------------------------------------------------------


class DiagnosticsEndpointTest(unittest.TestCase):
    def test_diagnostics_from_real_data(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/diagnostics/customer/c-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["entity_id"], "c-1")
        self.assertIn("snapshot", body)
        self.assertIn("leak_findings", body)
        self.assertIn("recommended_next_step", body)
        self.assertIn("generated_at", body)

    def test_diagnostics_unknown_is_404(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/diagnostics/lead/nope")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# GET /api/v1/tradeview/layers
# ---------------------------------------------------------------------------


class LayersEndpointTest(unittest.TestCase):
    def test_layers_shape(self):
        with _ClientCtx(_db()) as client:
            resp = client.get("/api/v1/tradeview/layers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("layers", body)
        names = {layer["id"] for layer in body["layers"]}
        self.assertIn("storm", names)
        self.assertIn("pins", names)
        for layer in body["layers"]:
            self.assertIn("id", layer)
            self.assertIn("label", layer)
            self.assertIn("live", layer)
            self.assertIn("requires_auth", layer)


if __name__ == "__main__":
    unittest.main()

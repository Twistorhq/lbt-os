"""Tests for the TW-306 What-Should-We-Do layer (Zeke Okafor).

Next-best-action engine (implements TW-212): every open leak ranked by
expected dollars-recovered with the single prescribed next move.
Nearest-qualified-tech assignment (JEV-style auditable records) and
nearest-neighbor route optimization. Recoverability weights are labeled
heuristic priors — never presented as measured rates.
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.auth import AuthContext, get_auth
from app.main import app
from app.routers import tradeview as tv_router
from app.services import next_actions as na
from app.services.geocode import GeocodeResult

ORG = "org-9"


def _finding(detector="quote-resurrection", dollars=13000.0, entities=None):
    return {
        "ladder": "should",
        "severity": "urgent",
        "title": "2 stalled quotes still winnable",
        "detail": "Quotes that died without follow-up.",
        "count": 2,
        "estimated_value": dollars,
        "entities": entities
        if entities is not None
        else [
            {"id": "q-1", "name": "Acme Heating", "total": 8500.0, "days_idle": 21},
            {"id": "q-2", "name": "Beta Corp", "total": 4500.0, "days_idle": 16},
        ],
        "recommended_action": "Two-touch follow-up this week, highest value first.",
        "detector": detector,
        "vertical": "hvac",
        "is_demo": False,
    }


# ---------------------------------------------------------------------------
# Pure engine: ranking
# ---------------------------------------------------------------------------


class RankingTest(unittest.TestCase):
    def test_ranked_by_expected_recovery(self):
        f_quotes = _finding(
            "quote-resurrection",
            entities=[{"id": "q-1", "name": "A", "total": 10000.0}],
        )  # 10000 x 0.35 = 3500
        f_churn = _finding(
            "plan-churn-risk", entities=[{"id": "p-1", "name": "B", "total": 5000.0}]
        )  # 5000 x 0.50 = 2500
        queue = na.build_action_queue([f_quotes, f_churn])
        self.assertEqual(queue[0]["entity_id"], "q-1")
        self.assertEqual(queue[0]["expected_recovery"], 3500.0)
        self.assertEqual(queue[1]["entity_id"], "p-1")
        self.assertEqual(queue[1]["expected_recovery"], 2500.0)

    def test_weights_are_labeled_priors(self):
        queue = na.build_action_queue([_finding()])
        for a in queue:
            self.assertIn("weight_basis", a)
            self.assertIn("prior", a["weight_basis"].lower())

    def test_single_next_move_prescribed(self):
        queue = na.build_action_queue([_finding()])
        self.assertEqual(
            queue[0]["next_move"],
            "Two-touch follow-up this week, highest value first.",
        )

    def test_no_dollar_means_no_expected_recovery_not_zero_claim(self):
        f = _finding(entities=[{"id": "q-1", "name": "A"}])  # no total
        queue = na.build_action_queue([f])
        self.assertIsNone(queue[0]["dollars"])
        self.assertIsNone(queue[0]["expected_recovery"])

    def test_unknown_detector_gets_default_weight(self):
        queue = na.build_action_queue([_finding("mystery-detector")])
        self.assertEqual(queue[0]["recoverability"], na.DEFAULT_RECOVERABILITY)


# ---------------------------------------------------------------------------
# Pure engine: tech assignment
# ---------------------------------------------------------------------------

ROSTER = [
    {"id": "t-far", "name": "Pops", "skills": ["HVAC"], "lat": 39.0, "lng": -105.5},
    {
        "id": "t-near",
        "name": "Auntie",
        "skills": ["HVAC", "Plumbing"],
        "lat": 39.74,
        "lng": -104.99,
    },
    {
        "id": "t-elec",
        "name": "Cuz",
        "skills": ["Electrical"],
        "lat": 39.739,
        "lng": -104.989,
    },
]


class AssignTechTest(unittest.TestCase):
    def _action(self):
        return {
            "id": "action:quote-resurrection:q-1",
            "entity_name": "Acme Heating",
            "trade": "HVAC",
            "lat": 39.75,
            "lng": -105.0,
            "located": True,
        }

    def test_picks_nearest_qualified_tech(self):
        res = na.assign_tech(self._action(), ROSTER)
        self.assertEqual(res["tech"]["id"], "t-near")
        self.assertLess(res["tech"]["distance_km"], 5)
        # JEV-style auditable record.
        self.assertEqual(res["decision"]["chosen_option"], "t-near")
        self.assertIn("t-far", res["decision"]["distribution"])

    def test_unskilled_techs_are_excluded(self):
        res = na.assign_tech(self._action(), [ROSTER[2]])  # Electrical only
        self.assertIsNone(res["tech"])
        self.assertIn("qualified", res["reason"].lower())

    def test_no_roster_is_honest(self):
        for roster in (None, []):
            res = na.assign_tech(self._action(), roster)
            self.assertIsNone(res["tech"])
            self.assertIn("roster", res["reason"].lower())

    def test_unlocated_action_cannot_assign(self):
        a = self._action()
        a["located"] = False
        res = na.assign_tech(a, ROSTER)
        self.assertIsNone(res["tech"])
        self.assertIn("location", res["reason"].lower())


# ---------------------------------------------------------------------------
# Pure engine: routing
# ---------------------------------------------------------------------------


class RouteTest(unittest.TestCase):
    def _stops(self):
        return [
            {"id": "a", "name": "A", "lat": 39.7392, "lng": -104.9903},
            {"id": "b", "name": "B", "lat": 39.75, "lng": -105.0},
            {"id": "c", "name": "C", "lat": 39.73, "lng": -104.98},
        ]

    def test_nearest_neighbor_order(self):
        route = na.optimize_route(
            self._stops(), start={"lat": 39.7392, "lng": -104.9903}
        )
        self.assertEqual(route["order"][0], "a")  # starts at the start point
        self.assertEqual(sorted(route["order"]), ["a", "b", "c"])
        self.assertGreater(route["total_km"], 0)
        self.assertIn("heuristic", route["method"].lower())

    def test_empty_stops_honest(self):
        route = na.optimize_route([])
        self.assertEqual(route["order"], [])
        self.assertEqual(route["total_km"], 0)

    def test_haversine_denver_boulder(self):
        # Denver -> Boulder is ~40km; sanity bound, not a survey.
        km = na.haversine_km(39.7392, -104.9903, 40.0150, -105.2705)
        self.assertGreater(km, 30)
        self.assertLess(km, 55)


# ---------------------------------------------------------------------------
# Endpoints
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
    from datetime import datetime, timezone

    return {
        "org_id": ORG,
        "vertical": "hvac",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "what_happened": [],
        "what_will_happen": [],
        "what_should_we_do": findings,
        "totals": {
            "findings": len(findings),
            "dollars_at_stake": sum(f.get("estimated_value") or 0 for f in findings),
        },
        "data_status": {},
    }


class ActionsEndpointTest(unittest.TestCase):
    def test_action_queue_from_real_scan(self):
        db = FakeDb(
            {
                "organizations": [{"id": ORG, "industry": "hvac"}],
                "customers": [],
                "leads": [],
                "quotes": [],
            }
        )
        with _ClientCtx(db) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(
                    tv_router,
                    "geocode_cached",
                    return_value=GeocodeResult(39.7, -105.0, "x", "photon"),
                ),
            ):
                resp = client.get("/api/v1/tradeview/actions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "live")
        self.assertEqual(len(body["actions"]), 2)
        # Ranked: q-1 ($8,500 x 0.35) before q-2 ($4,500 x 0.35).
        self.assertEqual(body["actions"][0]["entity_id"], "q-1")
        self.assertIn("next_move", body["actions"][0])

    def test_actions_require_auth(self):
        resp = TestClient(app).get("/api/v1/tradeview/actions")
        self.assertIn(resp.status_code, (401, 403))


class RouteEndpointTest(unittest.TestCase):
    def test_route_from_leak_ids(self):
        db = FakeDb(
            {
                "organizations": [{"id": ORG, "industry": "hvac"}],
                "customers": [
                    {
                        "id": "c-1",
                        "org_id": ORG,
                        "name": "Acme",
                        "address": "123 Colfax Ave, Denver, CO",
                    }
                ],
                "leads": [],
                "quotes": [],
            }
        )
        with _ClientCtx(db) as client:
            with (
                mock.patch.object(
                    tv_router,
                    "run_leak_scan",
                    return_value=_brief(
                        [
                            _finding(
                                entities=[
                                    {"id": "c-1", "name": "Acme", "total": 8500.0}
                                ]
                            )
                        ]
                    ),
                ),
                mock.patch.object(
                    tv_router,
                    "geocode_cached",
                    return_value=GeocodeResult(39.7, -105.0, "x", "photon"),
                ),
            ):
                resp = client.post(
                    "/api/v1/tradeview/route", json={"leak_ids": ["c-1"]}
                )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["order"], ["c-1"])
        self.assertGreaterEqual(body["total_km"], 0)

    def test_route_unknown_ids_are_honest(self):
        db = FakeDb(
            {
                "organizations": [{"id": ORG, "industry": "hvac"}],
                "customers": [],
                "leads": [],
                "quotes": [],
            }
        )
        with _ClientCtx(db) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(tv_router, "geocode_cached", return_value=None),
            ):
                resp = client.post(
                    "/api/v1/tradeview/route", json={"leak_ids": ["nope"]}
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order"], [])
        self.assertIn("unknown", resp.json()["note"].lower())

    def test_route_requires_auth(self):
        resp = TestClient(app).post("/api/v1/tradeview/route", json={"leak_ids": []})
        self.assertIn(resp.status_code, (401, 403))


class TechAssignmentSurfaceTest(unittest.TestCase):
    """Rosa round-3 MINOR 1 (RED): assign_tech must be reachable in the
    product — every action from GET /actions carries its tech_assignment,
    including the honest no-roster reason."""

    def test_actions_carry_tech_assignment_with_honest_no_roster_reason(self):
        db = FakeDb(
            {
                "organizations": [{"id": ORG, "industry": "hvac"}],
                "customers": [
                    {
                        "id": "q-1",
                        "org_id": ORG,
                        "name": "Acme Heating",
                        "address": "123 Colfax Ave, Denver, CO",
                    }
                ],
                "leads": [],
                "quotes": [],
            }
        )
        with _ClientCtx(db) as client:
            with (
                mock.patch.object(
                    tv_router, "run_leak_scan", return_value=_brief([_finding()])
                ),
                mock.patch.object(
                    tv_router,
                    "geocode_cached",
                    return_value=GeocodeResult(39.7, -105.0, "x", "photon"),
                ),
            ):
                resp = client.get("/api/v1/tradeview/actions")
        self.assertEqual(resp.status_code, 200)
        actions = resp.json()["actions"]
        self.assertTrue(actions)
        by_entity = {a["entity_id"]: a for a in actions}
        # Located action (q-1 has an address): honest no-roster reason.
        ta = by_entity["q-1"]["tech_assignment"]
        self.assertIsNotNone(ta, "every action must carry tech_assignment")
        self.assertIsNone(ta["tech"])
        self.assertIn("no technician roster connected", ta["reason"])
        self.assertIsNone(ta["decision"])
        # Unlocated action (q-2 has no address): honest no-location reason.
        ta2 = by_entity["q-2"]["tech_assignment"]
        self.assertIsNotNone(ta2)
        self.assertIsNone(ta2["tech"])
        self.assertIn("no location", ta2["reason"])


if __name__ == "__main__":
    unittest.main()

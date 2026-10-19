"""TW-622 Twistor Telemetry — router: capture, queries, admin, import (TDD RED)."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.telemetry import router as telemetry_router
from app.telemetry.router import _require_actor, _require_org


def _mock_db():
    store = {
        "tlm_events": [],
        "tlm_insights": [],
        "tlm_budgets": [
            {
                "org_id": "org-1",
                "monthly_cap": 10_000_000,
                "used": 0,
                "period_start": "2026-10-01",
                "warn_sent": False,
            }
        ],
    }

    class Chain:
        def __init__(self, table, filters=None):
            self.table = table
            self.filters = filters or []

        def _clone(self, f):
            return Chain(self.table, self.filters + [f])

        def eq(self, col, val):
            return self._clone(("eq", col, val))

        def in_(self, col, vals):
            return self._clone(("in", col, set(vals)))

        def gte(self, col, val):
            return self._clone(("gte", col, val))

        def lt(self, col, val):
            return self._clone(("lt", col, val))

        def order(self, *a, **k):
            return self

        def limit(self, n):
            return self._clone(("limit", n))

        def delete(self):
            return self

        def _filtered(self):
            out = list(store[self.table])
            lim = None
            for f in self.filters:
                if f[0] == "eq":
                    out = [r for r in out if r.get(f[1]) == f[2]]
                elif f[0] == "in":
                    out = [r for r in out if r.get(f[1]) in f[2]]
                elif f[0] == "gte":
                    out = [r for r in out if r.get(f[1]) >= f[2]]
                elif f[0] == "lt":
                    out = [r for r in out if r.get(f[1]) < f[2]]
                elif f[0] == "limit":
                    lim = f[1]
            if lim is not None:
                out = out[:lim]
            return out

        def execute(self):
            m = MagicMock()
            m.data = self._filtered()
            return m

    def _table(name):
        t = MagicMock()

        class TChain(Chain):
            def __init__(self, filters=None):
                super().__init__(name, filters)

        t.select.side_effect = lambda *a, **k: TChain()

        def _insert(rows):
            rows = rows if isinstance(rows, list) else [rows]
            saved = [
                dict(r, id=f"r-{len(store[name]) + i}") for i, r in enumerate(rows)
            ]
            store[name].extend(saved)
            m = MagicMock()
            m.data = saved
            ex = MagicMock()
            ex.execute.return_value = m
            return ex

        t.insert.side_effect = _insert

        def _upsert(row, on_conflict=None):
            if name == "tlm_budgets":
                store[name] = [dict(row)]
            else:
                store[name].append(dict(row))
            m = MagicMock()
            m.data = [dict(row)]
            ex = MagicMock()
            ex.execute.return_value = m
            return ex

        t.upsert.side_effect = _upsert

        class UpdateChain(TChain):
            def __init__(self, payload, filters=None):
                super().__init__(filters)
                self.payload = payload

            def _clone(self, f):
                return UpdateChain(self.payload, self.filters + [f])

            def execute(self):
                updated = []
                for r in self._filtered():
                    r.update(self.payload)
                    updated.append(dict(r))
                m = MagicMock()
                m.data = updated
                return m

        t.update.side_effect = lambda row: UpdateChain(row)

        class DeleteChain(TChain):
            def _clone(self, f):
                return DeleteChain(self.filters + [f])

            def execute(self):
                doomed = self._filtered()
                store[name] = [r for r in store[name] if r not in doomed]
                m = MagicMock()
                m.data = doomed
                return m

        # table-level eq() used by service.delete_insight: route to a chain
        t.eq.side_effect = lambda col, val: DeleteChain().eq(col, val)
        t.delete.side_effect = lambda: DeleteChain()
        return t

    db = MagicMock()
    db.table.side_effect = _table
    db._store = store
    return db


def make_client():
    app = FastAPI()
    app.include_router(telemetry_router.router)
    db = _mock_db()
    app.dependency_overrides[_require_org] = lambda: "org-1"
    app.dependency_overrides[_require_actor] = lambda: "tester"
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=False)
    c._mock_db = db
    return c


def test_capture_202_and_scrubbed():
    c = make_client()
    r = c.post(
        "/telemetry/capture",
        json={
            "events": [
                {
                    "event": "signup",
                    "distinct_id": "u1",
                    "properties": {"email": "a@b.com", "plan": "pro"},
                },
            ]
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["received"] == 1 and body["stored"] == 1
    stored = c._mock_db._store["tlm_events"][0]
    assert stored["properties"]["email"] != "a@b.com"
    assert stored["distinct_id_hash"] != "u1"


def test_capture_empty_batch_422():
    c = make_client()
    r = c.post("/telemetry/capture", json={"events": []})
    assert r.status_code == 422


def test_trends_round_trip():
    c = make_client()
    c.post(
        "/telemetry/capture",
        json={
            "events": [
                {
                    "event": "signup",
                    "distinct_id": "u1",
                    "occurred_at": "2026-10-01T12:00:00+00:00",
                },
                {
                    "event": "signup",
                    "distinct_id": "u2",
                    "occurred_at": "2026-10-03T12:00:00+00:00",
                },
            ]
        },
    )
    r = c.post(
        "/telemetry/trends",
        json={
            "event": "signup",
            "start": "2026-10-01T00:00:00+00:00",
            "end": "2026-10-04T00:00:00+00:00",
            "bucket": "day",
        },
    )
    assert r.status_code == 200, r.text
    assert [row["count"] for row in r.json()["rows"]] == [1, 0, 1]


def test_funnels_round_trip():
    c = make_client()
    c.post(
        "/telemetry/capture",
        json={
            "events": [
                {
                    "event": "sent",
                    "distinct_id": "u1",
                    "occurred_at": "2026-10-01T12:00:00+00:00",
                },
                {
                    "event": "opened",
                    "distinct_id": "u1",
                    "occurred_at": "2026-10-01T13:00:00+00:00",
                },
                {
                    "event": "sent",
                    "distinct_id": "u2",
                    "occurred_at": "2026-10-01T12:00:00+00:00",
                },
            ]
        },
    )
    r = c.post(
        "/telemetry/funnels",
        json={
            "funnel": {"steps": [{"event": "sent"}, {"event": "opened"}]},
            "start": "2026-10-01T00:00:00+00:00",
            "end": "2026-10-08T00:00:00+00:00",
        },
    )
    assert r.status_code == 200, r.text
    assert [s["count"] for s in r.json()["steps"]] == [2, 1]


def test_insight_crud_round_trip():
    c = make_client()
    r = c.post(
        "/telemetry/admin/insights",
        json={
            "key": "weekly-signups",
            "kind": "trend",
            "definition": {"event": "signup"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1
    r = c.get("/telemetry/admin/insights")
    assert len(r.json()["insights"]) == 1
    r = c.get("/telemetry/admin/insights/weekly-signups")
    assert r.status_code == 200
    r = c.patch(
        "/telemetry/admin/insights/weekly-signups",
        json={"definition": {"event": "signup", "bucket": "week"}},
    )
    assert r.json()["version"] == 2
    r = c.delete("/telemetry/admin/insights/weekly-signups")
    assert r.status_code == 204
    r = c.get("/telemetry/admin/insights/weekly-signups")
    assert r.status_code == 404


def test_insight_bad_kind_422():
    c = make_client()
    r = c.post(
        "/telemetry/admin/insights",
        json={"key": "k", "kind": "heatmap", "definition": {}},
    )
    assert r.status_code == 422


def test_posthog_import_reports_unmapped():
    c = make_client()
    r = c.post(
        "/telemetry/admin/import/posthog",
        json={
            "export": {
                "events": [{"event": "a", "distinct_id": "u1", "properties": {}}],
                "funnels": [{"name": "f", "steps": [{"name": "a"}, {"name": "b"}]}],
                "session_replays": [{"id": "r1"}],
            }
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["events_ingested"] == 1
    assert body["funnels_mapped"] == 1
    assert any("replay" in u for u in body["unmapped"])


def test_datafeed_export_suppresses_small_cohorts():
    c = make_client()
    c.post(
        "/telemetry/capture",
        json={
            "events": [
                {
                    "event": "job_done",
                    "distinct_id": f"a{i}",
                    "properties": {"trade": "hvac"},
                    "occurred_at": "2026-10-01T12:00:00+00:00",
                }
                for i in range(8)
            ]
            + [
                {
                    "event": "job_done",
                    "distinct_id": "solo",
                    "properties": {"trade": "solar"},
                    "occurred_at": "2026-10-01T12:00:00+00:00",
                },
            ]
        },
    )
    r = c.post(
        "/telemetry/datafeed/export",
        json={
            "event": "job_done",
            "group_props": ["trade"],
            "min_cohort": 5,
            "start": "2026-10-01T00:00:00+00:00",
            "end": "2026-10-02T00:00:00+00:00",
        },
    )
    assert r.status_code == 200, r.text
    trades = [row["cohort"]["trade"] for row in r.json()["rows"]]
    assert trades == ["hvac"]

"""TW-621 Twistor Flags — router: eval + admin round trip (TDD)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.flags import router as flags_router
from app.flags.router import _require_actor, _require_org


def _table_mock(rows=None):
    """Slightly honest supabase-table mock: eq filters, update merges."""
    store = {"rows": [dict(r) for r in (rows or [])]}

    class _Chain:
        def __init__(self, filters=None):
            self.filters = filters or []

        def eq(self, col, val):
            return _Chain(self.filters + [(col, val)])

        def order(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def _filtered(self):
            out = store["rows"]
            for col, val in self.filters:
                out = [r for r in out if r.get(col) == val]
            return out

        def execute(self):
            m = MagicMock()
            m.data = self._filtered()
            return m

    class _UpdateChain(_Chain):
        def __init__(self, payload, filters=None):
            super().__init__(filters)
            self.payload = payload

        def execute(self):
            m = MagicMock()
            updated = []
            for r in self._filtered():
                r.update(self.payload)
                updated.append(dict(r))
            m.data = updated
            return m

    class _DeleteChain(_Chain):
        def execute(self):
            m = MagicMock()
            doomed = self._filtered()
            store["rows"] = [r for r in store["rows"] if r not in doomed]
            m.data = doomed
            return m

    tbl = MagicMock()
    tbl.insert.side_effect = lambda row: _exec_one(dict(row, id="r-1"))
    tbl.update.side_effect = lambda row: _UpdateChain(row)
    tbl.delete.side_effect = lambda: _DeleteChain()
    tbl.select.side_effect = lambda *a, **k: _Chain()
    return tbl


def _exec_one(row):
    m = MagicMock()
    m.data = [row]
    ex = MagicMock()
    ex.execute.return_value = m
    return ex


_FLAG_ROW = {
    "id": "f-1",
    "org_id": "org-1",
    "key": "new-checkout",
    "definition": {
        "key": "new-checkout",
        "enabled": True,
        "variations": [{"value": False}, {"value": True}],
        "default_variation": 0,
        "targeting": [
            {
                "name": "beta",
                "conditions": [{"attribute": "plan", "op": "eq", "value": "beta"}],
                "then": {"variation": 1},
            }
        ],
    },
    "version": 1,
}


@pytest.fixture()
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(flags_router.router)
    tables = {"flg_flags": _table_mock([dict(_FLAG_ROW)]), "flg_audit": _table_mock([])}
    db = MagicMock()
    db.table.side_effect = lambda name: tables.get(name, _table_mock([]))
    app.dependency_overrides[_require_org] = lambda: "org-1"
    app.dependency_overrides[_require_actor] = lambda: "tester@example.com"
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestEvaluateEndpoint:
    def test_evaluate_serves_rule(self, client):
        r = client.post(
            "/flags/evaluate",
            json={
                "flag_key": "new-checkout",
                "targeting_key": "u1",
                "attributes": {"plan": "beta"},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["value"] is True
        assert body["reason"] == "targeting_rule"

    def test_evaluate_serves_default(self, client):
        r = client.post(
            "/flags/evaluate",
            json={
                "flag_key": "new-checkout",
                "targeting_key": "u2",
                "attributes": {"plan": "free"},
            },
        )
        assert r.status_code == 200
        assert r.json()["value"] is False

    def test_evaluate_missing_flag_404(self, client):
        r = client.post("/flags/evaluate", json={"flag_key": "nope"})
        assert r.status_code == 404

    def test_bulk_evaluate(self, client):
        r = client.post(
            "/flags/evaluate/bulk",
            json={
                "evaluations": [
                    {"flag_key": "new-checkout", "attributes": {"plan": "beta"}},
                    {"flag_key": "missing"},
                ]
            },
        )
        assert r.status_code == 200
        evals = r.json()["evaluations"]
        assert evals[0]["value"] is True
        assert evals[1]["error"] == "flag not found"

    def test_bulk_cap(self, client):
        r = client.post(
            "/flags/evaluate/bulk",
            json={"evaluations": [{"flag_key": "x"}] * 101},
        )
        assert r.status_code == 400


class TestAdminEndpoints:
    def test_create_and_get(self, client):
        r = client.post(
            "/flags/admin",
            json={
                "definition": {
                    "key": "fresh",
                    "enabled": True,
                    "variations": [{"value": "a"}, {"value": "b"}],
                    "default_variation": 0,
                }
            },
        )
        assert r.status_code == 201
        assert r.json()["key"] == "fresh"

    def test_create_invalid_422(self, client):
        r = client.post(
            "/flags/admin",
            json={
                "definition": {"key": "bad", "variations": [], "default_variation": 0}
            },
        )
        assert r.status_code == 422

    def test_list_and_get(self, client):
        r = client.get("/flags/admin")
        assert r.status_code == 200
        assert any(f["key"] == "new-checkout" for f in r.json()["flags"])
        r = client.get("/flags/admin/new-checkout")
        assert r.status_code == 200
        assert r.json()["key"] == "new-checkout"

    def test_flip(self, client):
        r = client.post("/flags/admin/new-checkout/flip", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_delete(self, client):
        r = client.delete("/flags/admin/new-checkout")
        assert r.status_code == 204

    def test_audit(self, client):
        r = client.get("/flags/admin/new-checkout/audit")
        assert r.status_code == 200
        assert "audit" in r.json()

    def test_ld_import_strict_rejects_unmapped(self, client):
        r = client.post(
            "/flags/admin/import/launchdarkly",
            json={
                "ld_flag": {
                    "key": "ld-1",
                    "on": True,
                    "variations": [{"value": False}, {"value": True}],
                    "offVariation": 0,
                    "fallthrough": {"variation": 0},
                    "rules": [
                        {
                            "description": "r",
                            "clauses": [
                                {
                                    "attribute": "v",
                                    "op": "semVerGreaterThan",
                                    "values": ["1.0"],
                                    "negate": False,
                                }
                            ],
                            "variation": 1,
                        }
                    ],
                },
                "strict": True,
            },
        )
        assert r.status_code == 422

    def test_apply_schedules(self, client):
        r = client.post("/flags/admin/apply-schedules")
        assert r.status_code == 200
        assert r.json() == {"applied": 0}

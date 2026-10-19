"""TW-622 Twistor Telemetry — service: ingest, insights, budgets (TDD RED)."""

from unittest.mock import MagicMock

import pytest

from app.telemetry import models, service


def _table_mock(rows=None):
    rows = rows if rows is not None else []
    tbl = MagicMock()

    def _exec(data):
        m = MagicMock()
        m.data = data
        ex = MagicMock()
        ex.execute.return_value = m
        return ex

    def _insert(rows):
        rows = rows if isinstance(rows, list) else [rows]
        return _exec([dict(r, id=f"row-{i}") for i, r in enumerate(rows)])

    tbl.insert.side_effect = _insert

    class _UpdateChain:
        def __init__(self, payload):
            self._payload = payload

        def eq(self, *a, **k):
            return self

        def execute(self):
            m = MagicMock()
            m.data = [dict(self._payload)]
            return m

    tbl.update.side_effect = lambda row: _UpdateChain(row)
    tbl.delete.return_value = chain = MagicMock()
    chain.execute.return_value = MagicMock(data=list(rows))
    for method in ("eq", "order", "limit", "in_", "neq", "gte", "lte"):
        getattr(chain, method).return_value = chain
    tbl.select.return_value = chain
    tbl.upsert.side_effect = lambda row, on_conflict=None: _exec([dict(row)])
    return tbl


def _db_with(budget=None, events=None):
    tables = {}
    db = MagicMock()

    def _table(name):
        if name not in tables:
            if name == "tlm_budgets":
                tables[name] = _table_mock([budget] if budget else [])
            elif name == "tlm_events":
                tables[name] = _table_mock(events or [])
            else:
                tables[name] = _table_mock([])
        return tables[name]

    db.table.side_effect = _table
    db._tables = tables
    return db


def _budget_row(used=0, cap=1000):
    return {
        "org_id": "org-1",
        "monthly_cap": cap,
        "used": used,
        "period_start": "2026-10-01",
        "warn_sent": False,
    }


def _event(name="signup", uid="u1", props=None):
    return models.TelemetryEvent(event=name, distinct_id=uid, properties=props or {})


def test_ingest_scrubs_and_hashes_before_insert():
    db = _db_with(budget=_budget_row())
    res = service.ingest_events(
        db, "org-1", [_event(props={"email": "a@b.com", "plan": "pro"})]
    )
    assert res["received"] == 1
    assert res["stored"] == 1
    inserted = db._tables["tlm_events"].insert.call_args[0][0]
    assert isinstance(inserted, list) and len(inserted) == 1
    row = inserted[0]
    assert row["properties"]["email"] != "a@b.com"
    assert row["properties"]["plan"] == "pro"
    assert row["distinct_id_hash"] != "u1"
    assert len(row["distinct_id_hash"]) == 64


def test_ingest_marks_sampled_rows():
    db = _db_with(budget=_budget_row(used=5000, cap=1000))
    events = [_event(uid=f"u{i}") for i in range(50)]
    res = service.ingest_events(db, "org-1", events)
    assert res["received"] == 50
    assert res["sampled"] is True
    assert 0 < res["stored"] < 50
    # every stored row carries its sample rate
    stored_rows = []
    for call in db._tables["tlm_events"].insert.call_args_list:
        stored_rows.extend(call[0][0])
    assert len(stored_rows) == res["stored"]
    for row in stored_rows:
        assert row["sampled"] is True
        assert 0.0 < row["sample_rate"] < 1.0


def test_ingest_updates_budget_usage_by_received():
    db = _db_with(budget=_budget_row(used=100, cap=1000))
    service.ingest_events(db, "org-1", [_event(), _event()])
    # budget row updated — received count, not stored count, is honest metering
    assert db._tables["tlm_budgets"].upsert.called


def test_ingest_creates_budget_row_when_missing():
    db = _db_with(budget=None)
    res = service.ingest_events(db, "org-1", [_event()])
    assert res["stored"] == 1
    assert db._tables["tlm_budgets"].upsert.called


def test_ingest_rejects_empty_batch():
    db = _db_with(budget=_budget_row())
    with pytest.raises(service.TelemetryError):
        service.ingest_events(db, "org-1", [])


def test_insight_crud_round_trip():
    db = _db_with()
    created = service.create_insight(
        db, "org-1", "weekly-signups", "trend", {"event": "signup"}, "dre"
    )
    assert created["key"] == "weekly-signups"
    assert created["version"] == 1
    assert created["created_by"] == "dre"


def test_insight_rejects_bad_kind():
    db = _db_with()
    with pytest.raises(service.TelemetryError):
        service.create_insight(db, "org-1", "k", "heatmap", {}, "dre")


def test_insight_update_bumps_version():
    db = _db_with()
    row = {"key": "k", "kind": "trend", "definition": {"event": "a"}, "version": 3}
    db._tables["tlm_insights"] = _table_mock([row])
    out = service.update_insight(db, "org-1", "k", {"event": "b"}, "dre")
    assert out["version"] == 4
    assert out["definition"] == {"event": "b"}

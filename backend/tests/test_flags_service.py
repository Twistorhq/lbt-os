"""TW-621 Twistor Flags — service: CRUD, audit trail, scheduled flips (TDD RED)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.flags import models, service


def _table_mock(rows=None):
    rows = rows if rows is not None else []
    tbl = MagicMock()

    def _exec(data):
        m = MagicMock()
        m.data = data
        ex = MagicMock()
        ex.execute.return_value = m
        return ex

    def _insert(row):
        return _exec([dict(row, id=f"row-{len(rows)}")])

    tbl.insert.side_effect = _insert
    tbl.update.side_effect = lambda row: _exec([dict(row)])
    tbl.upsert.side_effect = lambda row, on_conflict=None: _exec([dict(row)])
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=list(rows))
    for method in ("eq", "order", "limit", "in_", "neq", "gte", "lte"):
        getattr(chain, method).return_value = chain
    tbl.select.return_value = chain
    for method in ("eq", "neq"):
        getattr(tbl, method).return_value = chain
    tbl.delete.return_value = chain
    return tbl


def _db_with(flag_row=None, audit_rows=None):
    tables = {}
    db = MagicMock()

    def _table(name):
        if name not in tables:
            if name == "flg_flags":
                tables[name] = _table_mock([flag_row] if flag_row else [])
            elif name == "flg_audit":
                tables[name] = _table_mock(audit_rows or [])
            else:
                tables[name] = _table_mock([])
        return tables[name]

    db.table.side_effect = _table
    db._tables = tables
    return db


def _defn(**kw):
    base = dict(
        key="new-checkout",
        name="New checkout",
        enabled=True,
        variations=[{"value": False}, {"value": True}],
        default_variation=0,
        targeting=[],
    )
    base.update(kw)
    return models.FlagDefinition(**base)


def _flag_row(**kw):
    base = dict(
        id="f-1",
        org_id="org-1",
        key="new-checkout",
        definition=_defn().model_dump(),
        version=1,
    )
    base.update(kw)
    return base


class TestCreate:
    def test_create_stores_definition_and_audits(self):
        db = _db_with()
        row = service.create_flag(db, "org-1", _defn(), actor="admin@example.com")
        assert row["org_id"] == "org-1"
        assert row["key"] == "new-checkout"
        audit_tbl = db._tables["flg_audit"]
        inserted = audit_tbl.insert.call_args[0][0]
        assert inserted["action"] == "create"
        assert inserted["actor"] == "admin@example.com"
        assert inserted["flag_key"] == "new-checkout"

    def test_create_rejects_invalid_definition(self):
        db = _db_with()
        with pytest.raises(models.DefinitionError):
            service.create_flag(
                db,
                "org-1",
                {"key": "bad", "variations": [], "default_variation": 0},
                actor="a",
            )


class TestRead:
    def test_get_flag_returns_definition(self):
        db = _db_with(_flag_row())
        flag = service.get_flag(db, "org-1", "new-checkout")
        assert flag is not None
        assert flag.key == "new-checkout"
        assert flag.enabled is True

    def test_get_missing_flag_returns_none(self):
        db = _db_with()
        assert service.get_flag(db, "org-1", "nope") is None

    def test_list_flags_scopes_to_org(self):
        db = _db_with(_flag_row())
        flags = service.list_flags(db, "org-1")
        assert len(flags) == 1
        db.table("flg_flags").select.return_value.eq.assert_called_with(
            "org_id", "org-1"
        )


class TestUpdate:
    def test_update_bumps_version_and_audits(self):
        db = _db_with(_flag_row())
        row = service.update_flag(
            db, "org-1", "new-checkout", {"enabled": False}, actor="ops@example.com"
        )
        assert row["version"] == 2
        assert row["definition"]["enabled"] is False
        inserted = db._tables["flg_audit"].insert.call_args[0][0]
        assert inserted["action"] == "update"
        assert "enabled" in inserted["summary"]

    def test_update_missing_flag_raises(self):
        db = _db_with()
        with pytest.raises(service.FlagNotFound):
            service.update_flag(db, "org-1", "nope", {"enabled": False}, actor="a")

    def test_update_rejects_invalid_result(self):
        db = _db_with(_flag_row())
        with pytest.raises(models.DefinitionError):
            service.update_flag(
                db, "org-1", "new-checkout", {"default_variation": 99}, actor="a"
            )

    def test_flip_is_audited_as_flip(self):
        db = _db_with(_flag_row())
        service.flip_flag(db, "org-1", "new-checkout", False, actor="ops@example.com")
        inserted = db._tables["flg_audit"].insert.call_args[0][0]
        assert inserted["action"] == "flip"
        assert "enabled False" in inserted["summary"]


class TestDelete:
    def test_delete_removes_and_audits(self):
        db = _db_with(_flag_row())
        service.delete_flag(db, "org-1", "new-checkout", actor="admin@example.com")
        inserted = db._tables["flg_audit"].insert.call_args[0][0]
        assert inserted["action"] == "delete"

    def test_delete_missing_raises(self):
        db = _db_with()
        with pytest.raises(service.FlagNotFound):
            service.delete_flag(db, "org-1", "nope", actor="a")


class TestSchedules:
    def _row_with_schedule(self, at):
        return _flag_row(
            definition=_defn(
                scheduled=[{"at": at.isoformat(), "enabled": False}]
            ).model_dump()
        )

    def test_due_schedule_flips_flag(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        db = _db_with(self._row_with_schedule(past))
        applied = service.apply_due_schedules(db, "org-1", datetime.now(timezone.utc))
        assert applied == 1
        updated = db._tables["flg_flags"].update.call_args[0][0]
        assert updated["definition"]["enabled"] is False
        assert updated["definition"]["scheduled"] == []
        inserted = db._tables["flg_audit"].insert.call_args[0][0]
        assert inserted["action"] == "schedule_applied"

    def test_future_schedule_not_applied(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        db = _db_with(self._row_with_schedule(future))
        applied = service.apply_due_schedules(db, "org-1", datetime.now(timezone.utc))
        assert applied == 0

    def test_naive_at_treated_as_utc(self):
        past_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db = _db_with(self._row_with_schedule(past_naive))
        applied = service.apply_due_schedules(db, "org-1", datetime.now(timezone.utc))
        assert applied == 1


class TestAuditTrail:
    def test_audit_lists_most_recent_first(self):
        rows = [
            {"flag_key": "k", "action": "flip", "actor": "a", "at": "2026-10-19T10:00:00+00:00"},
            {"flag_key": "k", "action": "create", "actor": "a", "at": "2026-10-19T09:00:00+00:00"},
        ]
        db = _db_with(audit_rows=rows)
        entries = service.list_audit(db, "org-1", "k")
        assert [e["action"] for e in entries] == ["flip", "create"]

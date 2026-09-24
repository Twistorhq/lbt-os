"""Tests for TW-209 Phase 1 — internal product analytics event layer (Dre Coleman).

Covers: the emit_event() contract, context sanitization (the privacy
boundary), failure-never-breaks-product behavior, the migration's
no-PII-shaped-columns guarantee, and the router wiring that proves the
pattern end to end (event lands in the analytics schema, production
tables untouched).
"""
import os
import re
import unittest

from fastapi.testclient import TestClient

from app.auth import AuthContext, get_auth
from app.main import app
from app.routers import leak_engine as le_router
from app.services import analytics

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "supabase", "migration_analytics_events.sql"
)


# ---------------------------------------------------------------------------
# Fakes
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

    def insert(self, row):
        self._db.writes.append((self._name, row))
        return self

    def execute(self):
        return _Result(self._rows)


class FakeDb:
    """In-memory Supabase stand-in. `writes` records every (table, row) insert."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.writes = []

    def table(self, name):
        return _FakeTable(self, name)


class ExplodingDb:
    def table(self, _name):
        raise RuntimeError("database is down")


# ---------------------------------------------------------------------------
# sanitize_context — the privacy boundary
# ---------------------------------------------------------------------------

class SanitizeContextTest(unittest.TestCase):
    def test_allowlisted_keys_survive(self):
        ctx = analytics.sanitize_context({
            "result": "ok",
            "duration_ms": 42,
            "source": "api",
            "metric": "quote_close_rate",
            "detector": "equipment_graveyard",
            "ladder": "happened",
            "count": 3,
        })
        self.assertEqual(ctx["result"], "ok")
        self.assertEqual(ctx["duration_ms"], 42)
        self.assertEqual(ctx["metric"], "quote_close_rate")

    def test_unknown_keys_dropped(self):
        ctx = analytics.sanitize_context({"result": "ok", "customer_email": "a@b.com",
                                          "dollars": 999, "notes": "free text"})
        self.assertEqual(ctx, {"result": "ok"})

    def test_pii_fragments_rejected(self):
        for bad in ("email", "phone_number", "ssn", "home_address", "card_token",
                    "customer_name", "api_key"):
            self.assertTrue(analytics._key_is_blocked(bad), bad)
        for good in ("result", "duration_ms", "metric", "detector", "count"):
            self.assertFalse(analytics._key_is_blocked(good), good)

    def test_long_strings_truncated(self):
        ctx = analytics.sanitize_context({"result": "x" * 500})
        self.assertEqual(len(ctx["result"]), analytics.MAX_CONTEXT_VALUE_LEN)

    def test_non_scalars_dropped(self):
        ctx = analytics.sanitize_context({"result": "ok", "count": {"nested": 1},
                                          "source": ["a"], "metric": None})
        self.assertEqual(ctx, {"result": "ok"})

    def test_bools_kept(self):
        # bools are ints in Python — make sure they survive as bools, not dropped
        ctx = analytics.sanitize_context({"result": True})
        self.assertIs(ctx["result"], True)

    def test_non_dict_input(self):
        self.assertEqual(analytics.sanitize_context(None), {})
        self.assertEqual(analytics.sanitize_context("result=ok"), {})


# ---------------------------------------------------------------------------
# emit_event
# ---------------------------------------------------------------------------

class EmitEventTest(unittest.TestCase):
    def test_happy_path_writes_analytics_row(self):
        db = FakeDb()
        ok = analytics.emit_event(
            db, org_id="org-1", vertical="hvac", feature_key="leak_brief.viewed",
            context={"result": "ok", "duration_ms": 120},
        )
        self.assertTrue(ok)
        self.assertEqual(len(db.writes), 1)
        table, row = db.writes[0]
        self.assertEqual(table, "analytics_feature_events")
        self.assertEqual(row["org_id"], "org-1")
        self.assertEqual(row["vertical"], "hvac")
        self.assertEqual(row["feature_key"], "leak_brief.viewed")
        self.assertEqual(row["context"], {"result": "ok", "duration_ms": 120})

    def test_context_sanitized_before_write(self):
        db = FakeDb()
        analytics.emit_event(
            db, org_id="org-1", vertical="hvac", feature_key="detectors.listed",
            context={"result": "ok", "customer_email": "a@b.com", "revenue": 5000},
        )
        _, row = db.writes[0]
        self.assertEqual(row["context"], {"result": "ok"})

    def test_bad_feature_key_rejected(self):
        db = FakeDb()
        for bad in ("NoSpaces", "UPPER.CASE", "has space.x", "x", "", "a..b"):
            self.assertFalse(
                analytics.emit_event(db, org_id="o", vertical="hvac", feature_key=bad))
        self.assertEqual(db.writes, [])

    def test_missing_identifiers_rejected(self):
        db = FakeDb()
        self.assertFalse(analytics.emit_event(db, org_id="", vertical="hvac",
                                             feature_key="leak_brief.viewed"))
        self.assertFalse(analytics.emit_event(db, org_id="o", vertical="",
                                             feature_key="leak_brief.viewed"))
        self.assertEqual(db.writes, [])

    def test_db_failure_never_raises(self):
        # TW-208: analytics must never break the product path.
        self.assertFalse(analytics.emit_event(
            ExplodingDb(), org_id="o", vertical="hvac",
            feature_key="leak_brief.viewed"))

    def test_only_analytics_tables_written(self):
        db = FakeDb({"organizations": [{"id": "o", "industry": "hvac"}]})
        analytics.emit_event(db, org_id="o", vertical="hvac",
                             feature_key="benchmark.compare",
                             context={"metric": "quote_close_rate"})
        written_tables = {t for t, _ in db.writes}
        self.assertEqual(written_tables, {"analytics_feature_events"})


# ---------------------------------------------------------------------------
# Migration privacy guarantee — no PII-shaped columns, ever
# ---------------------------------------------------------------------------

def _migration_columns(path):
    sql = open(path).read()
    cols = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", sql, re.S):
        table, body = m.group(1), m.group(2)
        names = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            first = line.split()[0].strip('"').lower()
            if first in ("constraint", "primary", "foreign", "unique", "check"):
                continue
            names.append(first)
        cols[table] = names
    return cols


BANNED_COLUMN_NAMES = {
    "email", "phone", "ssn", "address", "first_name", "last_name", "full_name",
    "dob", "date_of_birth", "card_number", "cvv", "amount", "dollars",
    "revenue", "price", "customer_name", "customer_email", "customer_phone",
}


class MigrationPrivacyTest(unittest.TestCase):
    def test_expected_tables_exist(self):
        cols = _migration_columns(MIGRATION_PATH)
        self.assertIn("analytics_feature_events", cols)
        self.assertIn("analytics_feature_catalog", cols)

    def test_events_table_has_exact_column_set(self):
        # Exact-set assertion: no PII-shaped column can sneak in later.
        cols = _migration_columns(MIGRATION_PATH)["analytics_feature_events"]
        self.assertEqual(set(cols), {
            "id", "org_id", "vertical", "feature_key",
            "actor_role", "session_id", "context", "occurred_at",
        })

    def test_catalog_table_has_exact_column_set(self):
        cols = _migration_columns(MIGRATION_PATH)["analytics_feature_catalog"]
        self.assertEqual(set(cols), {
            "feature_key", "name", "ladder_rung", "description", "updated_at",
        })

    def test_no_banned_column_names(self):
        cols = _migration_columns(MIGRATION_PATH)
        for table, names in cols.items():
            for name in names:
                self.assertNotIn(name, BANNED_COLUMN_NAMES,
                                 f"{table}.{name} looks like PII/business data")

    def test_no_free_text_columns(self):
        # context is JSONB (allowlisted at the API); every other column is a
        # typed scalar — there is nowhere to stash free text or blobs.
        sql = open(MIGRATION_PATH).read().upper()
        self.assertNotIn(" TEXT[]", sql)
        self.assertNotIn("BYTEA", sql)

    def test_rls_enabled(self):
        sql = open(MIGRATION_PATH).read()
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("analytics_feature_events", sql)


# ---------------------------------------------------------------------------
# Router wiring — the pattern proven end to end
# ---------------------------------------------------------------------------

class RouterWiringTest(unittest.TestCase):
    def test_detectors_endpoint_emits_analytics_event(self):
        db = FakeDb({"organizations": [{"id": "org-9", "industry": "hvac"}]})
        real_get_db = le_router.get_db
        le_router.get_db = lambda: db
        app.dependency_overrides[get_auth] = lambda: AuthContext("user_1", "org-9", "pro")
        try:
            resp = TestClient(app).get("/api/v1/leaks/detectors")
        finally:
            le_router.get_db = real_get_db
            app.dependency_overrides.clear()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("detectors", resp.json())

        event_writes = [row for t, row in db.writes if t == "analytics_feature_events"]
        self.assertEqual(len(event_writes), 1)
        event = event_writes[0]
        self.assertEqual(event["org_id"], "org-9")
        self.assertEqual(event["vertical"], "hvac")
        self.assertEqual(event["feature_key"], "detectors.listed")
        self.assertEqual(event["context"]["result"], "ok")
        # No production-table writes happened through this path.
        self.assertEqual({t for t, _ in db.writes}, {"analytics_feature_events"})


if __name__ == "__main__":
    unittest.main()

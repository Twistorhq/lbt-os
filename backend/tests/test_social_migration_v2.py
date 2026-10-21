"""TW-661 Twistor Social v2 migration contract tests (TDD RED).

Rosa filed TW-661 during the TW-660 review: the merged schema has
no opens-at column and no held-seat accounting, so RSVP early-access
waves (reward catalog, Section 7.7) and host-held seat pools have
nothing lawful to anchor to, and couple RSVPs are atomic only at the
service layer. This file pins the v2 migration contract:

- rsn_events.rsvp_opens_at + rsn_event_series.default_rsvp_opens_at,
- rsn_event_seat_holds with status/release CHECK discipline and the
  same RLS deny-all posture as every other rsn_ table,
- the accounting/release functions (active held seats, expiry
  release) and the rsn_rsvp_couple RPC (advisory-locked, both rows
  or neither),
- scope guard: no evt_ (TW-651) or dps_ (TW-653) objects, no new
  rsn_ CREATE TABLE beyond the seat-holds table.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
V2_SQL_PATH = REPO_ROOT / "supabase" / "migration_social_v2.sql"


@pytest.fixture(scope="module")
def sql() -> str:
    assert V2_SQL_PATH.exists(), "supabase/migration_social_v2.sql missing"
    return V2_SQL_PATH.read_text()


class TestOpensAtColumns:
    def test_events_rsvp_opens_at(self, sql):
        assert "ALTER TABLE rsn_events" in sql
        assert "ADD COLUMN IF NOT EXISTS rsvp_opens_at TIMESTAMPTZ" in sql

    def test_series_default_rsvp_opens_at(self, sql):
        assert "ALTER TABLE rsn_event_series" in sql
        assert "ADD COLUMN IF NOT EXISTS default_rsvp_opens_at TIMESTAMPTZ" in sql

    def test_opens_at_index(self, sql):
        assert "idx_rsn_events_rsvp_opens_at" in sql


class TestSeatHoldsTable:
    def test_table_created_idempotently(self, sql):
        assert "CREATE TABLE IF NOT EXISTS rsn_event_seat_holds (" in sql

    def test_columns_and_checks(self, sql):
        m = re.search(
            r"CREATE TABLE IF NOT EXISTS rsn_event_seat_holds \((.*?)\n\);",
            sql,
            re.DOTALL,
        )
        assert m, "seat holds CREATE TABLE block not found"
        block = m.group(1)
        for needle in (
            "event_id UUID NOT NULL REFERENCES rsn_events(id)",
            "held_by UUID NOT NULL REFERENCES idn_users(id)",
            "seats INTEGER NOT NULL CHECK (seats > 0)",
            "release_at TIMESTAMPTZ NOT NULL",
            "released_at TIMESTAMPTZ",
            "'held', 'released', 'expired', 'consumed'",
        ):
            assert needle in block, needle

    def test_held_released_consistency_check(self, sql):
        # A hold is either live (held, no released_at) or settled
        # (released/expired/consumed, released_at stamped). A row in
        # neither state would corrupt the accounting.
        assert "released_at IS NULL" in sql and "released_at IS NOT NULL" in sql

    def test_rls_deny_all(self, sql):
        assert "ALTER TABLE rsn_event_seat_holds ENABLE ROW LEVEL SECURITY;" in sql
        assert "rsn_event_seat_holds_deny_all" in sql
        assert "ON rsn_event_seat_holds FOR ALL USING (false)" in sql


class TestFunctions:
    def test_active_held_seats_function(self, sql):
        assert "CREATE OR REPLACE FUNCTION rsn_active_held_seats(" in sql
        # Only live holds count: status held and not yet at release.
        assert "status = 'held'" in sql
        assert "release_at > p_now" in sql

    def test_release_expired_function(self, sql):
        assert "CREATE OR REPLACE FUNCTION rsn_release_expired_seat_holds(" in sql
        assert "status = 'expired'" in sql
        assert "release_at <= p_now" in sql

    def test_couple_rsvp_rpc(self, sql):
        assert "CREATE OR REPLACE FUNCTION rsn_rsvp_couple(" in sql
        # Both-or-neither: the pair write is serialized per event so
        # concurrent couple RSVPs cannot interleave with capacity.
        assert "pg_advisory_xact_lock" in sql
        assert sql.count("INSERT INTO rsn_rsvps") >= 2


class TestScopeGuard:
    def test_no_foreign_prefix_tables(self, sql):
        assert not re.search(r"CREATE TABLE IF NOT EXISTS (evt_|dps_)", sql)

    def test_only_one_new_table(self, sql):
        created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert created == ["rsn_event_seat_holds"]

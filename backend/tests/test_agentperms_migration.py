"""TW-768 — agt_ migration contract tests.

Pins the schema contract from Diego's TW-764 Spec 2:
- the four agt_ tables exist, created idempotently,
- RLS enabled on every table with a deny-all policy on each
  (service role only; the backend filters org_id manually),
- the eight action classes are the CHECK vocabulary,
- retained copies store locator metadata only — no content column
  (the registry must never become a second copy of the data it
  indexes for purge),
- scope guard: this migration creates only agt_ tables.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_agent_permissions_tw768.sql"
SQL = SQL_PATH.read_text()

AGT_TABLES = [
    "agt_grants",
    "agt_permission_events",
    "agt_revocations",
    "agt_retained_copies",
]

ACTION_CLASSES = [
    "email.send",
    "sms.send",
    "call.outbound",
    "booking.create",
    "booking.reschedule",
    "payment.link_send",
    "payment.charge",
    "purchase",
]


class TestTables:
    def test_all_tables_created_idempotently(self):
        for table in AGT_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in SQL

    def test_scope_guard_only_agt_tables(self):
        created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SQL)
        assert created and all(t.startswith("agt_") for t in created)

    def test_action_class_vocabulary_in_check(self):
        for action_class in ACTION_CLASSES:
            assert f"'{action_class}'" in SQL

    def test_grants_unique_per_org_and_class(self):
        assert "UNIQUE (org_id, action_class)" in SQL


class TestRlsDenyAll:
    def test_rls_enabled_on_every_table(self):
        for table in AGT_TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL

    def test_deny_all_policy_on_every_table(self):
        for table in AGT_TABLES:
            assert f"CREATE POLICY {table}_deny_all ON {table}" in SQL
            assert "USING (false)" in SQL


class TestCapLawPinnedInSchema:
    """Rosa's fix round: the cap law lives in the schema as well as
    the model layer, so a write path that bypasses GrantStatement
    still cannot state a broken grant."""

    def test_cap_only_on_money_classes(self):
        assert "agt_grants_cap_only_on_money" in SQL
        assert (
            "cap_cents IS NULL OR action_class IN ('payment.charge', 'purchase')" in SQL
        )

    def test_granted_money_rows_require_cap(self):
        assert "agt_grants_granted_money_requires_cap" in SQL
        assert "status <> 'granted'" in SQL
        assert "cap_cents IS NOT NULL" in SQL

    def test_constraints_added_for_preexisting_tables(self):
        # The migration is idempotent and re-runnable: the cap-law
        # constraints are also added via the add-if-missing guard.
        assert "ALTER TABLE agt_grants ADD CONSTRAINT" in SQL
        assert "pg_constraint" in SQL


class TestNeverStoreContent:
    def test_retained_copies_hold_no_content_column(self):
        section = SQL.split("CREATE TABLE IF NOT EXISTS agt_retained_copies")[1]
        section = section.split(");")[0]
        for banned in ("content", "body", "payload", "data "):
            assert banned not in section, banned
        assert "locator TEXT" in section

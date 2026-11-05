"""TW-951 (TW-948-B2): agent memory migration contract tests.

Pins the additive schema the B2 service runs on:
- the three agt_memory_ tables exist, created idempotently,
  and the migration creates nothing outside the agt_ scope,
- every table carries org, agent, and workspace scoping,
- RLS is enabled with a deny-all policy on every table,
- entries are unique per (org, agent, collection, entry_key),
- gate modes and collections are pinned by CHECK constraints,
- the DOWN section documents the reversal.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_agent_memory_tw951.sql"
SQL = SQL_PATH.read_text()

AGT_TABLES = [
    "agt_memory_entries",
    "agt_memory_audit_events",
    "agt_memory_gate_settings",
]


def _section(table: str) -> str:
    part = SQL.split(f"CREATE TABLE IF NOT EXISTS {table}")[1]
    return part.split(");")[0]


class TestTables:
    def test_all_tables_created_idempotently(self):
        for table in AGT_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in SQL

    def test_scope_guard_only_agt_tables(self):
        created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SQL)
        assert created and all(t.startswith("agt_") for t in created)
        assert sorted(created) == sorted(AGT_TABLES)

    def test_every_table_scoped(self):
        for table in AGT_TABLES:
            section = _section(table)
            assert "org_id" in section
            assert "agent_identity_id" in section
            assert "workspace_id" in section

    def test_down_section_documents_reversal(self):
        assert "DOWN section" in SQL
        for table in AGT_TABLES:
            assert f"DROP TABLE IF EXISTS {table}" in SQL


class TestConstraints:
    def test_entry_uniqueness(self):
        section = _section("agt_memory_entries")
        assert "UNIQUE (org_id, agent_identity_id, collection, entry_key)" in section

    def test_collections_pinned(self):
        section = _section("agt_memory_entries")
        assert "'business_facts'" in section
        assert "'owner_preferences'" in section

    def test_gate_modes_pinned(self):
        section = _section("agt_memory_gate_settings")
        assert "'gated'" in section
        assert "'relaxed'" in section

    def test_audit_events_carry_receipt(self):
        section = _section("agt_memory_audit_events")
        assert "receipt_id" in section
        assert "staged_write_id" in section

    def test_audit_event_vocabulary_includes_gate_change(self):
        section = _section("agt_memory_audit_events")
        assert "'memory_gate_mode_changed'" in section
        # Gate events have no staged write, collection, or entry:
        # those columns must be nullable in the audit table.
        assert "staged_write_id UUID REFERENCES" in section
        assert "staged_write_id UUID NOT NULL" not in section


class TestRls:
    def test_rls_enabled_and_deny_all(self):
        for table in AGT_TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL
            assert f"{table}_deny_all" in SQL

"""TW-949 (TW-948-B1): persistence migration contract tests.

Pins the schema shape from the build map Section 4 B1 entry:
- the six agt_ tables exist, created idempotently, and the
  migration creates nothing outside the agt_ scope,
- every table carries org, agent, and workspace scoping,
- RLS is enabled with a deny-all policy on every table,
- lineage lives on sessions as parent_session_id,
- messages keep raw body in bronze columns only, with the
  full-text index on the masked projection, never on bronze,
- fire identity is UNIQUE at the database layer,
- the DOWN section documents the reversal.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_persistence_tw949.sql"
SQL = SQL_PATH.read_text()

AGT_TABLES = [
    "agt_sessions",
    "agt_session_messages",
    "agt_jobs",
    "agt_job_fires",
    "agt_job_continuity",
    "agt_staged_writes",
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

    def test_down_section_documents_reversal(self):
        assert "DOWN section" in SQL
        for table in AGT_TABLES:
            assert f"DROP TABLE IF EXISTS {table}" in SQL


class TestScoping:
    def test_every_table_carries_org_agent_workspace(self):
        for table in AGT_TABLES:
            section = _section(table)
            assert "org_id UUID NOT NULL REFERENCES organizations(id)" in section, table
            assert (
                "agent_identity_id UUID NOT NULL REFERENCES agt_identities(id)"
                in section
            ), table
            assert "workspace_id TEXT NOT NULL" in section, table


class TestRlsDenyAll:
    def test_rls_enabled_on_every_table(self):
        for table in AGT_TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL

    def test_deny_all_policy_on_every_table(self):
        for table in AGT_TABLES:
            assert f"CREATE POLICY {table}_deny_all ON {table}" in SQL
        assert "USING (false)" in SQL


class TestLineageAndPiiShape:
    def test_parent_session_id_for_lineage(self):
        section = _section("agt_sessions")
        assert "parent_session_id UUID REFERENCES agt_sessions(id)" in section

    def test_messages_bronze_plus_masked_projection(self):
        section = _section("agt_session_messages")
        assert "body_bronze TEXT" in section
        assert "body_hash TEXT NOT NULL" in section
        assert "body_masked TEXT" in section
        assert "search_text TEXT" in section
        assert "session_id UUID NOT NULL REFERENCES agt_sessions(id)" in section
        assert "UNIQUE (session_id, seq)" in section

    def test_fts_index_on_masked_projection_never_bronze(self):
        assert "USING gin (to_tsvector('english', search_text))" in SQL
        assert "to_tsvector('english', body_bronze)" not in SQL

    def test_staged_writes_have_no_bronze_column(self):
        section = _section("agt_staged_writes")
        assert "bronze" not in section
        assert "proposed_body_masked TEXT" in section
        assert "pinned_target_hash TEXT" in section
        assert "'memory'" in section and "'skill'" in section


class TestFireIdempotencyShape:
    def test_fire_key_unique_at_database_layer(self):
        section = _section("agt_job_fires")
        assert "fire_key TEXT NOT NULL UNIQUE" in section

    def test_same_job_same_minute_unique(self):
        section = _section("agt_job_fires")
        assert "UNIQUE (job_id, scheduled_for)" in section

    def test_continuity_one_record_per_job(self):
        section = _section("agt_job_continuity")
        assert "UNIQUE (job_id)" in section
        assert "surfaced_item_ids JSONB" in section

    def test_job_name_unique_per_org_agent(self):
        section = _section("agt_jobs")
        assert "UNIQUE (org_id, agent_identity_id, name)" in section

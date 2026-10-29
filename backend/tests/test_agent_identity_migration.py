"""TW-767 Twistor Agent Identity — migration contract tests.

Pin the schema contract from the TW-764 Spec 1:
- the three agt_ tables exist, created idempotently (IF NOT EXISTS),
- org-scoped: every table carries org_id REFERENCES organizations
  ON DELETE CASCADE; exactly one identity per org (UNIQUE org_id),
- the agent's email is globally unique (one address, one org),
- queue idempotency is per (provider_message_id, org_id),
- RLS enabled on every table with a deny-all policy on each,
- bronze discipline in DDL: the sender hash column exists beside the
  raw bronze columns on the inbound queue,
- scope guard: this migration creates only agt_ tables.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_agent_identity_v1.sql"

AGT_TABLES = ["agt_identities", "agt_inbound_messages", "agt_audit"]


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert SQL_PATH.exists()


def test_every_table_created_idempotently():
    sql = _sql()
    for table in AGT_TABLES:
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(", sql), (
            f"{table} missing or not idempotent"
        )


def test_scope_guard_only_agt_tables():
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _sql())
    assert created, "no tables created"
    assert all(t.startswith("agt_") for t in created)
    assert sorted(created) == sorted(AGT_TABLES)


def test_every_table_is_org_scoped_with_cascade():
    sql = _sql()
    for table in AGT_TABLES:
        block = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split(");", 1)[
            0
        ]
        assert (
            "org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE"
            in block
        ), table


def test_one_identity_per_org_and_one_owner_per_address():
    sql = _sql()
    block = sql.split("CREATE TABLE IF NOT EXISTS agt_identities (", 1)[1].split(
        ");", 1
    )[0]
    assert "UNIQUE (org_id)" in block
    assert "UNIQUE (email_address)" in block


def test_identity_defaults_are_fail_closed():
    sql = _sql()
    block = sql.split("CREATE TABLE IF NOT EXISTS agt_identities (", 1)[1].split(
        ");", 1
    )[0]
    assert "DEFAULT 'unprovisioned'" in block
    assert '"mode": "unconfigured"' in block


def test_inbound_queue_idempotency_and_bronze_hash():
    sql = _sql()
    block = sql.split("CREATE TABLE IF NOT EXISTS agt_inbound_messages (", 1)[1].split(
        ");", 1
    )[0]
    assert "UNIQUE (provider_message_id, org_id)" in block
    assert "from_address_hash TEXT NOT NULL" in block
    assert "DEFAULT 'queued'" in block


def test_rls_enabled_with_deny_all_on_every_table():
    sql = _sql()
    for table in AGT_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in sql, table
        assert (
            f"CREATE POLICY {table}_deny_all ON {table} FOR ALL USING (false);" in sql
        ), table


def test_audit_actions_are_the_registry_vocabulary():
    sql = _sql()
    for action in (
        "provision",
        "persona_update",
        "coverage_update",
        "phone_bind",
        "status_change",
        "inbound_enqueued",
    ):
        assert f"'{action}'" in sql, action

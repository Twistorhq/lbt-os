"""TW-800 - agt_tool_* migration contract tests.

Same discipline as the TW-768 migration tests: the SQL file is the
contract. Pins the append-only audit + approvals shape, the
vocabulary CHECKs, the per-org chain uniqueness that makes a chain
collision fail the write, and RLS deny-all on both tables.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_agent_tool_gateway_tw800.sql"
SQL = SQL_PATH.read_text()

TABLES = ("agt_tool_calls", "agt_tool_approvals")


class TestMigrationContract:
    def test_all_tables_created_idempotently(self):
        for table in TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in SQL

    def test_scope_guard_only_agt_tables(self):
        creates = [line for line in SQL.splitlines() if line.startswith("CREATE TABLE")]
        assert creates and all("agt_" in line for line in creates)

    def test_rls_enabled_on_every_table(self):
        for table in TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL

    def test_deny_all_policy_on_every_table(self):
        for table in TABLES:
            assert f"{table}_deny_all" in SQL
            assert f"ON {table} FOR ALL USING (false)" in SQL

    def test_audit_kind_vocabulary_in_check(self):
        for kind in (
            "requested",
            "approval_requested",
            "approved",
            "approval_denied",
            "approval_expired",
            "executed",
            "denied",
            "failed",
        ):
            assert f"'{kind}'" in SQL

    def test_approval_status_vocabulary_in_check(self):
        for status in (
            "pending",
            "approved",
            "denied",
            "expired",
            "executed",
            "fenced",
        ):
            assert f"'{status}'" in SQL

    def test_action_class_vocabulary_in_check(self):
        for cls in (
            "email.send",
            "sms.send",
            "call.outbound",
            "booking.create",
            "booking.reschedule",
            "payment.link_send",
            "payment.charge",
            "purchase",
        ):
            assert f"'{cls}'" in SQL

    def test_chain_uniqueness_pinned(self):
        # A seq or hash collision must fail the write: the gateway
        # fails the call closed when its audit write fails.
        assert "UNIQUE (org_id, seq)" in SQL
        assert "UNIQUE (org_id, row_hash)" in SQL

    def test_one_approval_per_call(self):
        assert "call_id UUID NOT NULL UNIQUE" in SQL

    def test_audit_row_holds_fingerprint_not_values(self):
        # args_hash + args_keys live on the audit table; a bare args
        # column lives only on the approvals table (execution needs
        # the values after an approval, briefly).
        calls_sql = SQL.split("CREATE TABLE IF NOT EXISTS agt_tool_calls")[1].split(
            ");"
        )[0]
        assert "args_hash TEXT NOT NULL" in calls_sql
        assert "args_keys JSONB" in calls_sql
        assert "\n    args JSONB" not in calls_sql

    def test_pgcrypto_for_uuid_defaults(self):
        assert 'CREATE EXTENSION IF NOT EXISTS "pgcrypto"' in SQL

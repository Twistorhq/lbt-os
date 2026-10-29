"""TW-768 follow-up — agt_ agent-identity FK migration contract tests.

The TW-768 migration carried agent_identity_id by value because
Spec 1's agt_identities table did not exist yet, and its header
promised the FK "in a follow-up once that table exists." TW-767
has merged; this pins the follow-up migration's contract:

- every agt_ table's agent_identity_id references agt_identities(id),
- delete semantics follow the fail-closed doctrine: grants (current
  state) CASCADE so a deleted identity's grants die rather than
  widening to org-wide via SET NULL; the history / revocation /
  retained-copy tables SET NULL so consent history, revocation
  clocks, and purge obligations survive an identity deletion,
- the migration is idempotent (add-if-missing) and alters only the
  four agt_ tables — it creates nothing.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "supabase" / "migration_agent_permissions_tw768_fk.sql"
SQL = SQL_PATH.read_text()

AGT_TABLES = [
    "agt_grants",
    "agt_permission_events",
    "agt_revocations",
    "agt_retained_copies",
]


class TestForeignKeys:
    def test_every_agt_table_links_to_identities(self):
        for table in AGT_TABLES:
            pattern = (
                rf"ALTER TABLE {table}\s+ADD CONSTRAINT {table}_agent_identity_fk\s+"
                rf"FOREIGN KEY \(agent_identity_id\) REFERENCES agt_identities\(id\)"
            )
            assert re.search(pattern, SQL), table

    def test_grants_cascade(self):
        # Current state dies with the identity; SET NULL here would
        # silently widen a per-agent grant into an org-wide grant.
        assert re.search(
            r"agt_grants_agent_identity_fk.*?ON DELETE CASCADE", SQL, re.DOTALL
        )

    def test_history_revocation_and_copies_set_null(self):
        for table in (
            "agt_permission_events",
            "agt_revocations",
            "agt_retained_copies",
        ):
            pattern = rf"{table}_agent_identity_fk.*?ON DELETE SET NULL"
            assert re.search(pattern, SQL, re.DOTALL), table


class TestIdempotencyAndScope:
    def test_add_if_missing_for_every_constraint(self):
        for table in AGT_TABLES:
            guard = f"conname = '{table}_agent_identity_fk'"
            assert guard in SQL, table

    def test_creates_no_tables(self):
        assert "CREATE TABLE" not in SQL

    def test_alters_only_agt_tables(self):
        altered = set(re.findall(r"ALTER TABLE (\w+)", SQL))
        assert altered == set(AGT_TABLES)

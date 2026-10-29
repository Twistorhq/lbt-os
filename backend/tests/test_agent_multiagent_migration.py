"""TW-787 multi-agent amendment — migration + rate contract tests.

The amendment lands as follow-up migrations (the TW-768 FK pattern):
the v1 files stay untouched, these files evolve the schema.

Pinned:
- agt_identities: role, archetype slot, included flag, per-agent
  slug; the one-per-org unique becomes one-per-(org, slug); legacy
  rows backfill as the included 'primary' agent,
- the audit vocabulary grows the roster actions,
- agt_grants: the (org, action class) unique becomes
  (org, agent, action class) with NULLS NOT DISTINCT so the
  org-wide row is also unique,
- the agent.hire toll rate exists, derives from the single named
  constant, and sits inside Justynn's $20-$30 band.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_SQL = REPO_ROOT / "supabase" / "migration_agent_identity_tw787.sql"
PERMS_SQL = REPO_ROOT / "supabase" / "migration_agent_permissions_tw787.sql"

ROLES = (
    "everything",
    "general",
    "hr",
    "sales",
    "marketing",
    "ads",
    "seo",
    "inbox",
    "payroll",
    "frontend_dev",
    "backend_dev",
    "executive",
)


def test_files_exist():
    assert IDENTITY_SQL.exists()
    assert PERMS_SQL.exists()


def test_identity_amendment_creates_no_tables():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in sql
    sql = PERMS_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in sql


def test_identity_columns_added_idempotently():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    for column in (
        "agent_slug TEXT",
        "role TEXT NOT NULL DEFAULT 'everything'",
        "archetype_key TEXT",
        "included_with_subscription BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql, column


def test_one_per_org_unique_becomes_one_per_org_slug():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT" in sql and "agt_identities_org_id_key" in sql
    assert "UNIQUE (org_id, agent_slug)" in sql


def test_legacy_row_backfills_as_included_primary():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    assert "agent_slug = 'primary'" in sql
    assert "included_with_subscription = TRUE" in sql
    assert "agent_slug IS NULL" in sql


def test_role_vocabulary_pinned_in_schema_and_model():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    for role in ROLES:
        assert f"'{role}'" in sql, role
    from app.agent_identity.models import AGENT_ROLES

    assert tuple(AGENT_ROLES) == ROLES


def test_audit_vocabulary_grows_roster_actions():
    sql = IDENTITY_SQL.read_text(encoding="utf-8")
    for action in (
        "provision",
        "persona_update",
        "coverage_update",
        "phone_bind",
        "status_change",
        "inbound_enqueued",
        "agent_hire",
        "role_assign",
        "archetype_assign",
    ):
        assert f"'{action}'" in sql, action


def test_grants_unique_scopes_per_agent():
    sql = PERMS_SQL.read_text(encoding="utf-8")
    assert "agt_grants_org_id_action_class_key" in sql
    assert "DROP CONSTRAINT" in sql
    assert "UNIQUE NULLS NOT DISTINCT (org_id, agent_identity_id, action_class)" in sql


def test_hire_fee_is_one_named_constant_in_the_approved_band():
    from app.config import AGENT_HIRE_FEE_CENTS, TOLL_RATES

    assert 2000 <= AGENT_HIRE_FEE_CENTS <= 3000
    assert AGENT_HIRE_FEE_CENTS == 2500  # default $25; Justynn confirms the number
    assert TOLL_RATES["agent.hire"] == AGENT_HIRE_FEE_CENTS

"""TW-954 (TW-948-B7) — migration file shape (FILE ONLY, never applied).

Pins: the policy table with B1 scope columns and a per-scope
uniqueness law, RLS deny-all in the B1 guarded style, the additive
extends_unattended column on agt_grants, and a documented DOWN.
No floor state lives in this file: the floor is code
(agentperms/floor.py) and no column here can switch it off.
"""

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migration_agentperms_policy_tw954.sql"
)


def sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert MIGRATION.exists()


def test_policy_table_carries_b1_scope_columns_and_uniqueness():
    text = sql()
    assert "CREATE TABLE IF NOT EXISTS agt_unattended_policies" in text
    for column in ("org_id", "agent_identity_id", "workspace_id"):
        assert column in text
    assert (
        "UNIQUE (org_id, agent_identity_id, workspace_id)" in text
    )


def test_policy_table_defaults_to_disabled():
    text = sql()
    assert "enabled BOOLEAN NOT NULL DEFAULT FALSE" in text


def test_grants_gain_the_unattended_extension_column_additively():
    text = sql()
    assert (
        "ALTER TABLE agt_grants ADD COLUMN IF NOT EXISTS "
        "extends_unattended BOOLEAN NOT NULL DEFAULT FALSE" in text
    )


def test_rls_deny_all_guard_matches_b1_style():
    text = sql()
    assert "ALTER TABLE agt_unattended_policies ENABLE ROW LEVEL SECURITY" in text
    assert "agt_unattended_policies_deny_all" in text
    assert "USING (false)" in text


def test_no_floor_switch_is_stored_anywhere():
    text = sql().lower()
    for forbidden in ("floor_disabled", "dnc_override", "approvals_off"):
        assert forbidden not in text


def test_down_section_is_documented_and_reversible():
    text = sql()
    assert "-- DOWN" in text
    assert "DROP TABLE IF EXISTS agt_unattended_policies" in text
    assert "DROP COLUMN IF EXISTS extends_unattended" in text

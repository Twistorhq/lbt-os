"""TW-650 Twistor Social — vfy_/rsn_ migration contract tests (TDD RED).

These tests pin the schema contract from the TW-646 architecture spec
Section 4, the merged OpenSpec design (tasks.md items 9, 13, 14), and the
fix-round deltas Rosa verified in TW-648:

- every specced table exists, created idempotently (IF NOT EXISTS),
- RLS deny-all on every table (ENABLE + explicit deny-all policy),
- the PII doctrine in DDL form: no raw DOB anywhere, document fields are
  keyed HMAC hashes, check-ins carry NO coordinate columns, ever,
- the fix-round deltas: rsn_rsvps `requested` sub-state with host decision
  fields, rsn_close_friends, rsn_ambassadors, audience-scope columns,
- scope guard: no evt_ (TW-651) or dps_ (TW-653) tables in these files.

The migrations are static SQL (Supabase pattern, same as Identity /
Notify / Flags / Telemetry), so the contract is verified by parsing the
DDL. Live-DB execution evidence is a separate launch-gate step.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SQL_PATH = REPO_ROOT / "supabase" / "migration_verify_v1.sql"
SOCIAL_SQL_PATH = REPO_ROOT / "supabase" / "migration_social_v1.sql"


@pytest.fixture(scope="module")
def verify_sql() -> str:
    assert VERIFY_SQL_PATH.exists(), "supabase/migration_verify_v1.sql missing"
    return VERIFY_SQL_PATH.read_text()


@pytest.fixture(scope="module")
def social_sql() -> str:
    assert SOCIAL_SQL_PATH.exists(), "supabase/migration_social_v1.sql missing"
    return SOCIAL_SQL_PATH.read_text()


VFY_TABLES = [
    "vfy_engine_versions",
    "vfy_consents",
    "vfy_sessions",
    "vfy_documents",
    "vfy_biometrics",
    "vfy_signals",
    "vfy_identity_anchors",
    "vfy_age_attestations",
]

RSN_TABLES = [
    "rsn_profiles",
    "rsn_couple_links",
    "rsn_groups",
    "rsn_group_memberships",
    "rsn_blocks",
    "rsn_close_friends",
    "rsn_event_series",
    "rsn_events",
    "rsn_event_locations",
    "rsn_rsvps",
    "rsn_guest_passes",
    "rsn_location_reveals",
    "rsn_checkins",
    "rsn_encounter_confirms",
    "rsn_welfare_checks",
    "rsn_reviews",
    "rsn_trusted_contacts",
    "rsn_trust_events",
    "rsn_trust_scores",
    "rsn_collectible_rules",
    "rsn_collectibles",
    "rsn_vouches",
    "rsn_posts",
    "rsn_reactions",
    "rsn_comments",
    "rsn_voice_usage",
    "rsn_reports",
    "rsn_bans",
    "rsn_sanctions",
    "rsn_appeals",
    "rsn_evidence_packets",
    "rsn_disclosures",
    "rsn_audit_log",
    "rsn_plan_catalog",
    "rsn_organizer_plans",
    "rsn_ambassadors",
]


def _table_block(sql: str, table: str) -> str:
    """Return the CREATE TABLE block for `table` (closing ');' at col 0)."""
    m = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);",
        sql,
        re.DOTALL,
    )
    assert m, f"no CREATE TABLE block found for {table}"
    return m.group(1)


class TestFilesAndTables:
    def test_verify_file_has_all_vfy_tables(self, verify_sql):
        for t in VFY_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {t} (" in verify_sql, t

    def test_social_file_has_all_rsn_tables(self, social_sql):
        for t in RSN_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {t} (" in social_sql, t

    def test_table_count(self, verify_sql, social_sql):
        assert len(re.findall(r"CREATE TABLE IF NOT EXISTS vfy_", verify_sql)) == 8
        assert len(re.findall(r"CREATE TABLE IF NOT EXISTS rsn_", social_sql)) == 36

    def test_no_bare_create_table(self, verify_sql, social_sql):
        for sql in (verify_sql, social_sql):
            bare = [
                line
                for line in sql.splitlines()
                if line.startswith("CREATE TABLE") and "IF NOT EXISTS" not in line
            ]
            assert bare == [], f"non-idempotent CREATE TABLE: {bare}"

    def test_scope_guard_no_evt_or_dps(self, verify_sql, social_sql):
        for sql in (verify_sql, social_sql):
            assert not re.search(r"CREATE TABLE IF NOT EXISTS (evt_|dps_)", sql)


class TestRlsDenyAll:
    @pytest.mark.parametrize("table", VFY_TABLES)
    def test_vfy_table_rls(self, verify_sql, table):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in verify_sql
        assert f"{table}_deny_all" in verify_sql

    @pytest.mark.parametrize("table", RSN_TABLES)
    def test_rsn_table_rls(self, social_sql, table):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in social_sql
        assert f"{table}_deny_all" in social_sql

    def test_policies_are_idempotent(self, verify_sql, social_sql):
        # Policy creation must be guarded (pg_policies check in a DO block),
        # never a bare CREATE POLICY that fails on re-run.
        for sql in (verify_sql, social_sql):
            assert "pg_policies" in sql
            assert not re.search(r"^CREATE POLICY ", sql, re.MULTILINE)


class TestPiiDoctrineInDdl:
    def test_no_raw_dob_anywhere(self, verify_sql, social_sql):
        for sql in (verify_sql, social_sql):
            assert not re.search(r"\bdate_of_birth\b", sql)
            # bare `dob` column is forbidden; dob_hash is the only form
            assert not re.search(r"^\s+dob\s", sql, re.MULTILINE)

    def test_document_fields_are_hashes(self, verify_sql):
        block = _table_block(verify_sql, "vfy_documents")
        for col in ("name_hash", "dob_hash", "doc_number_hash"):
            assert col in block
        assert "legal_name" not in block

    def test_checkins_have_no_coordinate_columns(self, social_sql):
        block = _table_block(social_sql, "rsn_checkins").lower()
        for token in ("latitude", "longitude", "coordinate", " geo_", "point("):
            assert token not in block, token
        assert "proof_type" in block

    def test_biometrics_template_is_encrypted_bytea_with_consent(self, verify_sql):
        block = _table_block(verify_sql, "vfy_biometrics")
        assert "template_encrypted BYTEA" in block
        assert "consent_id" in block
        assert "REFERENCES vfy_consents" in block

    def test_age_attestation_carries_band_and_next_band_date(self, verify_sql):
        block = _table_block(verify_sql, "vfy_age_attestations")
        for band in ("under_16", "age_16_17", "age_18_20", "age_21_plus"):
            assert band in block
        assert "next_band_date" in block
        assert "REFERENCES vfy_identity_anchors" in block

    def test_trusted_contact_is_hash_only(self, social_sql):
        block = _table_block(social_sql, "rsn_trusted_contacts")
        assert "contact_channel_hash" in block


class TestFixRoundDeltas:
    def test_rsvp_requested_substate_with_host_decision_fields(self, social_sql):
        block = _table_block(social_sql, "rsn_rsvps")
        assert "'requested'" in block
        assert "host_decided_by" in block
        assert "host_decided_at" in block
        for status in ("going", "waitlist", "declined", "no_show"):
            assert f"'{status}'" in block

    def test_close_friends_table(self, social_sql):
        block = _table_block(social_sql, "rsn_close_friends")
        assert "owner_id" in block and "friend_id" in block
        assert "UNIQUE (owner_id, friend_id)" in block

    def test_ambassadors_table(self, social_sql):
        block = _table_block(social_sql, "rsn_ambassadors")
        for status in ("invited", "vetting", "active", "suspended", "revoked"):
            assert f"'{status}'" in block
        assert "vetted_by" in block and "activated_at" in block

    @pytest.mark.parametrize("table", ["rsn_posts", "rsn_events"])
    def test_audience_scope_columns(self, social_sql, table):
        block = _table_block(social_sql, table)
        assert "audience_scope" in block
        for scope in ("group", "connections", "close_friends"):
            assert f"'{scope}'" in block

    def test_home_venue_type_on_events(self, social_sql):
        block = _table_block(social_sql, "rsn_events")
        assert "venue_type" in block and "'home'" in block


class TestSpecInvariants:
    @pytest.mark.parametrize("table", ["rsn_groups", "rsn_events", "rsn_event_series"])
    def test_min_age_band_mandatory(self, social_sql, table):
        block = _table_block(social_sql, table)
        assert re.search(r"min_age_band .*NOT NULL", block), table
        for band in ("age_16_plus", "age_18_plus", "age_21_plus"):
            assert band in block

    def test_bans_and_sanctions_anchor_to_vfy(self, social_sql):
        for table in ("rsn_bans", "rsn_sanctions", "rsn_appeals"):
            block = _table_block(social_sql, table)
            assert "REFERENCES vfy_identity_anchors" in block, table

    def test_plan_catalog_seeded_with_null_prices(self, social_sql):
        assert "INSERT INTO rsn_plan_catalog" in social_sql
        assert "ON CONFLICT" in social_sql
        for key in ("organizer_free", "organizer_plus"):
            assert key in social_sql
        # Prices stay NULL until Justynn sets launch prices (spec S4).
        seed = social_sql.split("INSERT INTO rsn_plan_catalog")[1].split(";")[0]
        assert "NULL" in seed

    def test_couple_link_ordered_pair(self, social_sql):
        block = _table_block(social_sql, "rsn_couple_links")
        assert "user_low_id" in block and "user_high_id" in block
        assert "user_low_id < user_high_id" in block

    def test_checkin_proof_types(self, social_sql):
        block = _table_block(social_sql, "rsn_checkins")
        for proof in ("geo_soft", "qr_strong", "host_confirm"):
            assert f"'{proof}'" in block

    def test_welfare_enums(self, social_sql):
        block = _table_block(social_sql, "rsn_welfare_checks")
        for resp in ("fine", "need_a_minute", "need_help", "no_response"):
            assert f"'{resp}'" in block
        assert "'silent_alert'" in block

    def test_sanction_levels(self, social_sql):
        block = _table_block(social_sql, "rsn_sanctions")
        for level in ("warning", "restriction", "purge"):
            assert f"'{level}'" in block

    def test_trust_event_types(self, social_sql):
        block = _table_block(social_sql, "rsn_trust_events")
        for etype in (
            "verification_tier_set",
            "identity_confirm_positive",
            "identity_strike",
            "review_received",
            "no_show",
            "report_substantiated",
            "report_unfounded",
            "vouch_received",
            "checkin_completed",
            "series_streak",
        ):
            assert f"'{etype}'" in block

    def test_voice_usage_unique_week(self, social_sql):
        block = _table_block(social_sql, "rsn_voice_usage")
        assert "UNIQUE (user_id, week_start)" in block

    def test_down_sections_present(self, verify_sql, social_sql):
        for sql in (verify_sql, social_sql):
            assert "DOWN section" in sql

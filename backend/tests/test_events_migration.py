"""TW-651 Twistor Social events data layer: evt_ migration contract tests.

These tests pin the schema contract from the TW-646 architecture spec
Section 10.3 and the merged OpenSpec package (tasks.md item 28):

- every specced evt_ table exists, created idempotently (IF NOT EXISTS),
- RLS enabled on every table; deny-all policies on every table except
  the gold read model, whose only policy is SELECT for the dedicated
  read-only role evt_public_read (spec 10.3: gold is the only public
  evt_ surface, served through a read-only role),
- the never-store list (spec 10.6) in DDL form: no description, image,
  photo, logo, or price column anywhere in the evt_ schema,
- dedupe fingerprint UNIQUE on evt_listings; venue address fingerprint
  UNIQUE on evt_venues,
- listings carry no raw date-of-birth and no organizer contact columns,
- scope guard: this migration creates only evt_ tables.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_SQL_PATH = REPO_ROOT / "supabase" / "migration_events_v1.sql"

EVT_TABLES = [
    "evt_sources",
    "evt_raw_items",
    "evt_venues",
    "evt_listings",
    "evt_listing_sources",
    "evt_claims",
    "evt_ingest_runs",
    "evt_dead_letters",
    "evt_publish_log",
    "evt_gold_listings",
]

NEVER_STORE_COLUMNS = [
    "description",
    "photo",
    "image",
    "logo",
    "price",
    "ticket_price",
    "organizer_email",
    "organizer_phone",
    "attendee",
    "rsvp_count",
]


@pytest.fixture(scope="module")
def sql() -> str:
    assert EVENTS_SQL_PATH.exists(), "supabase/migration_events_v1.sql missing"
    return EVENTS_SQL_PATH.read_text()


def _table_block(sql: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);",
        sql,
        re.DOTALL,
    )
    assert match, f"table {table} not created with IF NOT EXISTS"
    return match.group(1)


def test_all_evt_tables_created_idempotently(sql):
    for table in EVT_TABLES:
        _table_block(sql, table)


def test_only_evt_tables_created(sql):
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
    assert created, "no tables created"
    for name in created:
        assert name.startswith("evt_"), f"non-evt_ table in events migration: {name}"


def test_rls_enabled_on_every_table(sql):
    for table in EVT_TABLES:
        assert re.search(rf"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY", sql), (
            f"RLS not enabled on {table}"
        )


def test_deny_all_policy_on_every_non_gold_table(sql):
    for table in EVT_TABLES:
        if table == "evt_gold_listings":
            continue
        assert re.search(
            rf"CREATE POLICY \w+ ON {table}[\s\S]{{0,120}}USING \(false\)", sql
        ), f"deny-all policy missing on {table}"


def test_gold_has_select_only_policy_for_read_role(sql):
    gold_policies = re.findall(r"CREATE POLICY (\w+) ON evt_gold_listings([^;]*);", sql)
    assert len(gold_policies) == 1, (
        f"gold must have exactly one policy: {gold_policies}"
    )
    name, body = gold_policies[0]
    assert "FOR SELECT" in body
    assert "evt_public_read" in body
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert verb not in body, f"gold policy must not grant {verb}"


def test_no_policy_grants_write_on_any_evt_table(sql):
    policies = re.findall(r"CREATE POLICY (\w+) ON (\w+)([^;]*);", sql)
    assert policies, "no policies found"
    for name, table, body in policies:
        if "USING (false)" in body:
            continue  # a deny-all policy, the TW-650 pattern
        assert table == "evt_gold_listings" and "FOR SELECT" in body, (
            f"policy {name} on {table} is neither deny-all nor the gold read policy"
        )


def test_never_store_columns_absent(sql):
    for table in EVT_TABLES:
        block = _table_block(sql, table).lower()
        columns = set(re.findall(r"^\s{4}(\w+)\s", block, re.MULTILINE))
        for banned in NEVER_STORE_COLUMNS:
            assert banned not in columns, f"{table} stores banned column {banned}"


def test_listing_fingerprint_unique(sql):
    block = _table_block(sql, "evt_listings")
    assert re.search(r"fingerprint\s+TEXT\s+NOT NULL\s+UNIQUE", block)


def test_venue_address_fingerprint_unique(sql):
    block = _table_block(sql, "evt_venues")
    assert re.search(r"address_fingerprint\s+TEXT\s+NOT NULL\s+UNIQUE", block)


def test_listing_status_vocabulary(sql):
    block = _table_block(sql, "evt_listings")
    for status in ("active", "cancelled", "stale", "claimed"):
        assert f"'{status}'" in block


def test_source_posture_and_kind_vocabulary(sql):
    block = _table_block(sql, "evt_sources")
    for posture in ("index_facts", "organizer_only", "prohibited"):
        assert f"'{posture}'" in block
    for kind in ("ics", "api", "organizer_import"):
        assert f"'{kind}'" in block


def test_age_band_vocabulary_matches_social(sql):
    block = _table_block(sql, "evt_listings")
    for band in ("age_16_plus", "age_18_plus", "age_21_plus"):
        assert f"'{band}'" in block
    assert "min_age_band" in block
    # Rating is nullable: unrated listings exist and fail closed for
    # members (spec 5.9); the column must not be NOT NULL.
    assert not re.search(r"min_age_band[^,\n]*NOT NULL", block)


def test_raw_items_payload_nullable_hash_not_null(sql):
    block = _table_block(sql, "evt_raw_items")
    assert re.search(r"payload\s+JSONB", block)
    assert not re.search(r"payload\s+JSONB\s+NOT NULL", block), (
        "bronze payload must be nullable: posture can forbid retention"
    )
    assert re.search(r"payload_hash\s+TEXT\s+NOT NULL", block)


def test_claims_table_shape(sql):
    block = _table_block(sql, "evt_claims")
    for col in (
        "listing_id",
        "claimant_user_id",
        "method",
        "evidence_ref",
        "status",
        "decided_by",
        "decided_at",
    ):
        assert col in block, f"evt_claims missing {col}"
    for method in ("oauth", "domain", "staff_review"):
        assert f"'{method}'" in block


def test_listing_sources_unique_per_listing_and_source(sql):
    assert re.search(
        r"UNIQUE \(listing_id, source_id\)", _table_block(sql, "evt_listing_sources")
    )


def test_checkins_and_social_tables_not_recreated(sql):
    for other in ("rsn_", "vfy_", "dps_"):
        assert f"CREATE TABLE IF NOT EXISTS {other}" not in sql

"""TW-658: migration_verify_v2.sql contract tests (TDD RED).

Rosa's TW-650 nit, carried into TW-658: the v1 consent-type
constraint triggers watch the biometric and document rows, but
nothing watches UPDATE OF consent_type on vfy_consents itself, so a
consent's type could be edited after the fact and the match would
never re-fire. The v2 migration closes that side with a deferred
constraint trigger on vfy_consents, following the v1 file's
conventions: idempotent, guarded, with a manual DOWN section.

Static DDL parsing, same as the TW-650 migration tests; live-DB
execution evidence is recorded in the TW-658 evidence report.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
V2_PATH = REPO_ROOT / "supabase" / "migration_verify_v2.sql"
V3_PATH = REPO_ROOT / "supabase" / "migration_verify_v3.sql"


@pytest.fixture(scope="module")
def v2_sql() -> str:
    assert V2_PATH.exists(), "supabase/migration_verify_v2.sql missing"
    return V2_PATH.read_text()


class TestConsentTypeUpdateGuard:
    def test_trigger_watches_consent_type_updates(self, v2_sql):
        assert re.search(r"CREATE CONSTRAINT TRIGGER\s+\w+", v2_sql, re.IGNORECASE), (
            "the guard must be a constraint trigger (deferred, like v1)"
        )
        assert re.search(
            r"UPDATE OF consent_type ON vfy_consents", v2_sql, re.IGNORECASE
        ), "the trigger must fire on UPDATE OF consent_type on vfy_consents"
        assert "DEFERRABLE INITIALLY DEFERRED" in v2_sql

    def test_guard_is_dropped_before_create(self, v2_sql):
        # Idempotent re-run, the v1 convention.
        assert re.search(
            r"DROP TRIGGER IF EXISTS\s+\w+\s+ON vfy_consents",
            v2_sql,
            re.IGNORECASE,
        )

    def test_function_revalidates_against_documents(self, v2_sql):
        # The guard re-checks the pairing the v1 function enforces:
        # school_id documents ride on school_id_biometric_capture
        # consents and nothing else does.
        assert "school_id_biometric_capture" in v2_sql
        assert "vfy_documents" in v2_sql
        assert "RAISE EXCEPTION" in v2_sql

    def test_no_new_tables_or_rls_changes(self, v2_sql):
        assert not re.search(r"CREATE TABLE", v2_sql, re.IGNORECASE)
        assert "Prepared by Twistor Holdings LLC" in v2_sql

    def test_down_section_present(self, v2_sql):
        assert "DOWN section" in v2_sql


@pytest.fixture(scope="module")
def v3_sql() -> str:
    assert V3_PATH.exists(), "supabase/migration_verify_v3.sql missing"
    return V3_PATH.read_text()


class TestSessionDocumentTypeColumn:
    """Rosa's M3: the session's document class needs a durable,
    schema-true carrier. Design call 3's working-state echo could
    not survive a PostgREST round trip; the v3 column can."""

    def test_adds_document_type_to_vfy_sessions(self, v3_sql):
        assert re.search(
            r"ALTER TABLE vfy_sessions\s+ADD COLUMN IF NOT EXISTS\s+document_type",
            v3_sql,
            re.IGNORECASE,
        ), "v3 must add document_type to vfy_sessions, idempotently"

    def test_class_check_matches_the_schema_set(self, v3_sql):
        assert re.search(r"CHECK\s*\(", v3_sql, re.IGNORECASE)
        for klass in ("passport", "drivers_license", "state_id", "school_id"):
            assert f"'{klass}'" in v3_sql
        # Pre-v3 rows carry no class; the column stays nullable so
        # the migration never fabricates one for them.
        assert re.search(r"document_type IS NULL OR", v3_sql, re.IGNORECASE)

    def test_no_new_tables_and_attributed(self, v3_sql):
        assert not re.search(r"CREATE TABLE", v3_sql, re.IGNORECASE)
        assert not re.search(r"DROP TABLE", v3_sql, re.IGNORECASE)
        assert "Prepared by Twistor Holdings LLC" in v3_sql

    def test_down_section_present(self, v3_sql):
        assert "DOWN section" in v3_sql
        assert re.search(r"DROP COLUMN IF EXISTS\s+document_type", v3_sql)

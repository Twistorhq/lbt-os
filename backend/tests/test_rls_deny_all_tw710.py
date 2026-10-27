"""TW-710 — deny-all RLS for the 19 tables appsecradar run 1 confirmed
were created without ENABLE ROW LEVEL SECURITY (TRIAGE.md class 1,
CONFIRMED by Rex).

The fix is one migration, supabase/migration_rls_deny_all_tw710.sql,
following the TW-650 social-schema pattern: ENABLE ROW LEVEL SECURITY
plus an explicit, pg_policies-guarded deny-all policy per table
(FOR ALL USING (false)). The service role bypasses RLS by design and
stays the only reader/writer; the anon/PostgREST surface gets nothing.

Two layers of evidence:

- Static contract tests (always run): the migration covers exactly
  the 19 tables, each source table really exists in its origin
  migration, RLS is enabled per table, the deny-all policy per table
  is guarded so the file is safe to re-run, and no other table is
  touched.

- A live Postgres probe (runs only when TW710_TEST_DATABASE_URL and a
  psql binary are configured): builds minimal stand-ins for the 19
  tables, seeds a sentinel row as a BYPASSRLS service_role, applies
  the migration twice (idempotency), then proves per table that the
  anon role can read nothing and write nothing, while service_role
  still reads and writes. This is the PostgREST-equivalent check:
  PostgREST serves requests under the anon role, so role-level denial
  is exactly what an anon-key holder would hit.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "supabase" / "migration_rls_deny_all_tw710.sql"

TABLES = [
    "sci_labs",
    "sci_calibrations",
    "sci_bronze_artifacts",
    "sci_dataset_versions",
    "sci_corrections",
    "sci_manifests",
    "sci_attestations",
    "sci_benchmarks",
    "sci_benchmark_runs",
    "sci_electro_measurements",
    "sci_electro_gold",
    "sci_hightc_measurements",
    "sci_hightc_gold",
    "workspace_exports",
    "migration_runs",
    "lead_stage_history",
    "plan_audit_log",
    "stripe_events",
    "visitor_events",
]

# Where each table is created today (verified on main @ 8c4194f).
SOURCE_FILES = {
    "sci_labs": "migration_sci_core_v1.sql",
    "sci_calibrations": "migration_sci_core_v1.sql",
    "sci_bronze_artifacts": "migration_sci_core_v1.sql",
    "sci_dataset_versions": "migration_sci_core_v1.sql",
    "sci_corrections": "migration_sci_core_v1.sql",
    "sci_manifests": "migration_sci_core_v1.sql",
    "sci_attestations": "migration_sci_core_v1.sql",
    "sci_benchmarks": "migration_sci_core_v1.sql",
    "sci_benchmark_runs": "migration_sci_core_v1.sql",
    "sci_electro_measurements": "migration_sci_electrocatalysis_v1.sql",
    "sci_electro_gold": "migration_sci_electrocatalysis_v1.sql",
    "sci_hightc_measurements": "migration_sci_high_tc_v1.sql",
    "sci_hightc_gold": "migration_sci_high_tc_v1.sql",
    "workspace_exports": "migration_data_portability.sql",
    "migration_runs": "migration_data_portability.sql",
    "lead_stage_history": "migration_revenue_intelligence.sql",
    "plan_audit_log": "migration_revenue_intelligence.sql",
    "stripe_events": "migration_stripe_events.sql",
    "visitor_events": "migration_visitor_events.sql",
}


@pytest.fixture(scope="module")
def sql() -> str:
    assert MIGRATION_PATH.exists(), "supabase/migration_rls_deny_all_tw710.sql missing"
    return MIGRATION_PATH.read_text()


class TestTablesExistAtSource:
    @pytest.mark.parametrize("table", TABLES)
    def test_source_migration_creates_table(self, table):
        source = REPO_ROOT / "supabase" / SOURCE_FILES[table]
        text = source.read_text()
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", text), (
            f"{table} not created in {SOURCE_FILES[table]}"
        )


class TestStaticContract:
    def test_exactly_the_19_tables_get_rls_enabled(self, sql):
        enabled = re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY;", sql)
        assert sorted(enabled) == sorted(TABLES)
        assert len(enabled) == 19

    @pytest.mark.parametrize("table", TABLES)
    def test_table_rls_enabled(self, sql, table):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in sql

    @pytest.mark.parametrize("table", TABLES)
    def test_table_deny_all_policy(self, sql, table):
        assert re.search(
            rf"CREATE POLICY {table}_deny_all ON {table} FOR ALL USING \(false\)",
            sql,
        ), f"deny-all policy missing on {table}"

    def test_no_other_policies_created(self, sql):
        created = re.findall(r"CREATE POLICY (\w+) ON (\w+)", sql)
        assert sorted(name for name, _ in created) == sorted(
            f"{t}_deny_all" for t in TABLES
        )
        assert sorted(table for _, table in created) == sorted(TABLES)

    def test_policies_are_idempotent(self, sql):
        # Same guard as TW-650/TW-651: policy creation sits in a DO
        # block behind a pg_policies check, never a bare CREATE POLICY
        # that would fail on re-run.
        assert "pg_policies" in sql
        assert not re.search(r"^CREATE POLICY ", sql, re.MULTILINE)

    def test_no_permissive_policy_anywhere(self, sql):
        for name, body in re.findall(r"CREATE POLICY (\w+)([^;]*);", sql):
            assert "USING (false)" in body, f"policy {name} is not deny-all"

    def test_down_section_present(self, sql):
        assert "DOWN section" in sql


# ---------------------------------------------------------------
# Live Postgres probe (opt-in: needs a disposable database)
# ---------------------------------------------------------------

PSQL = os.environ.get("TW710_PSQL") or shutil.which("psql")
DATABASE_URL = os.environ.get("TW710_TEST_DATABASE_URL")


def _psql(args, check=True):
    result = subprocess.run(
        [PSQL, DATABASE_URL, "-X", "-v", "ON_ERROR_STOP=1", "-tA", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"psql failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _psql_as(role, statement):
    result = _psql(["-c", f"SET ROLE {role}; {statement}"], check=False)
    # psql echoes the SET command tag as the first stdout line; the
    # caller wants the wrapped statement's own output.
    if result.stdout.startswith("SET\n"):
        result.stdout = result.stdout[len("SET\n") :]
    return result


@pytest.mark.skipif(
    not (PSQL and DATABASE_URL),
    reason=(
        "live probe needs TW710_TEST_DATABASE_URL pointing at a "
        "disposable Postgres and a psql binary (TW710_PSQL)"
    ),
)
class TestLivePostgresProbe:
    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def _probe_db(cls):
        setup = [
            "DROP ROLE IF EXISTS anon;",
            "DROP ROLE IF EXISTS service_role;",
            "CREATE ROLE anon NOLOGIN;",
            "CREATE ROLE service_role NOLOGIN BYPASSRLS;",
        ]
        for table in TABLES:
            setup.append(f"DROP TABLE IF EXISTS {table};")
            setup.append(f"CREATE TABLE {table} (probe_marker int);")
            setup.append(f"GRANT ALL ON {table} TO anon, service_role;")
            setup.append(
                f"SET ROLE service_role; INSERT INTO {table} VALUES (710); RESET ROLE;"
            )
        _psql(["-c", "\n".join(setup)])

        # Apply the migration twice: the second run proves the
        # pg_policies guards make it idempotent.
        for _ in range(2):
            with tempfile.NamedTemporaryFile(
                "w", suffix=".sql", delete=False
            ) as handle:
                handle.write(MIGRATION_PATH.read_text())
                migration_copy = handle.name
            try:
                _psql(["-f", migration_copy])
            finally:
                os.unlink(migration_copy)

        yield

        teardown = [f"DROP TABLE IF EXISTS {t};" for t in TABLES]
        teardown += ["DROP ROLE IF EXISTS anon;", "DROP ROLE IF EXISTS service_role;"]
        _psql(["-c", "\n".join(teardown)], check=False)

    @pytest.mark.parametrize("table", TABLES)
    def test_catalog_rls_on_and_deny_policy_present(self, table):
        out = _psql(
            [
                "-c",
                "SELECT c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                f"WHERE c.relname = '{table}' AND n.nspname = 'public';",
            ]
        ).stdout.strip()
        assert out == "t", f"RLS not enabled on {table}"
        out = _psql(
            [
                "-c",
                "SELECT qual FROM pg_policies WHERE schemaname = 'public' "
                f"AND tablename = '{table}' "
                f"AND policyname = '{table}_deny_all';",
            ]
        ).stdout.strip()
        assert out == "false", f"deny-all policy wrong on {table}: {out!r}"

    @pytest.mark.parametrize("table", TABLES)
    def test_anon_reads_nothing(self, table):
        out = _psql_as("anon", f"SELECT count(*) FROM {table};")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "0", f"anon can read {table}"

    @pytest.mark.parametrize("table", TABLES)
    def test_anon_writes_nothing(self, table):
        insert = _psql_as("anon", f"INSERT INTO {table} VALUES (1);")
        assert insert.returncode != 0, f"anon INSERT succeeded on {table}"
        assert "row-level security" in insert.stderr
        update = _psql_as("anon", f"UPDATE {table} SET probe_marker = 1;")
        assert update.returncode == 0, update.stderr
        assert "UPDATE 0" in update.stdout, f"anon UPDATE touched {table}"
        delete = _psql_as("anon", f"DELETE FROM {table};")
        assert delete.returncode == 0, delete.stderr
        assert "DELETE 0" in delete.stdout, f"anon DELETE touched {table}"

    @pytest.mark.parametrize("table", TABLES)
    def test_service_role_still_works(self, table):
        out = _psql_as("service_role", f"SELECT count(*) FROM {table};")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "1", f"service_role lost read on {table}"
        insert = _psql_as("service_role", f"INSERT INTO {table} VALUES (711);")
        assert insert.returncode == 0, insert.stderr
        assert "INSERT 0 1" in insert.stdout
        out = _psql_as("service_role", f"SELECT count(*) FROM {table};")
        assert out.stdout.strip() == "2", f"service_role lost write on {table}"

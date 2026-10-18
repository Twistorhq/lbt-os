"""Tests for the TW-593 Track 2 turbulence lane (Dre Coleman).

Prepared by Twistor Holdings LLC.

Covers: schema constraints, provenance-chain walk, version immutability,
correction supersession, seed integrity, and toll emission (mocked).
No live database: the migration SQL is asserted textually and the router
runs against a Supabase-shaped fake.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = os.path.join(REPO_ROOT, "supabase", "migration_sci_turbulence_v1.sql")

from app.auth import AuthContext, get_auth  # noqa: E402
from app.data_layer.science.turbulence import (  # noqa: E402
    models,
    schemas,
    seed,
)
from app.data_layer.science.turbulence import router as turb_router  # noqa: E402


def _read_migration() -> str:
    with open(MIGRATION) as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Fake Supabase (same shape as the repo's other router tests)
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, db, name):
        self._db = db
        self._name = name
        self._rows = list(db.tables.get(name, []))

    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def insert(self, row):
        if isinstance(row, list):
            self._db.tables.setdefault(self._name, []).extend(row)
        else:
            self._db.tables.setdefault(self._name, []).append(row)
        return self

    def execute(self):
        return _Result(self._rows)


class FakeDb:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def table(self, name):
        return _FakeTable(self, name)


def _seed_db() -> FakeDb:
    rows = seed.build_seed_records()
    versions = [{
        "id": "ver-1", "lane": "turbulence", "dataset_name": "twistor-turbulence-flowbench",
        "semver": "1.0.0", "row_count": 30, "content_root_hash": "deadbeef" * 8,
        "benchmark_pin": "closure-challenge-v1", "doi": None,
        "supersedes_id": None, "released_at": "2026-10-18T00:00:00Z",
        "release_notes": "V1 synthetic seed",
    }]
    gold = [{"id": f"gold-{r['id']}", "dataset_version_id": "ver-1",
             "measurement_id": r["id"], "semver": "1.0.0",
             "attestation_summary": {}, "synthetic": True} for r in rows]
    benches = [{"id": "bench-1", "lane": "turbulence",
                "name": "closure-challenge-v1", "metric": "reynolds_stress_mae",
                "status": "open"}]
    runs = [{"id": "run-1", "benchmark_id": "bench-1", "submitter": "gnn-closure-x",
             "score": 0.031, "manifest_id": "man-1",
             "submitted_at": "2026-10-18T01:00:00Z"}]
    return FakeDb({
        "sci_turbulence_measurements": rows,
        "sci_turbulence_gold": gold,
        "sci_dataset_versions": versions,
        "sci_benchmarks": benches,
        "sci_benchmark_runs": runs,
    })


def _client(db: FakeDb) -> TestClient:
    app = FastAPI()
    app.include_router(turb_router.router, prefix="/data/v1/science/turbulence")
    app.dependency_overrides[get_auth] = lambda: AuthContext("user_1", "org-1", "pro")
    real = turb_router.get_db
    turb_router.get_db = lambda: db
    client = TestClient(app)
    client._turb_real_get_db = real  # stash for teardown
    return client


def _teardown(client: TestClient) -> None:
    turb_router.get_db = client._turb_real_get_db


# ---------------------------------------------------------------------------
# 1. Schema constraints
# ---------------------------------------------------------------------------

class ConstraintTests(unittest.TestCase):
    def test_migration_file_exists(self):
        self.assertTrue(os.path.exists(MIGRATION))

    def test_silver_table_and_provenance_fks(self):
        sql = _read_migration()
        self.assertIn("CREATE TABLE IF NOT EXISTS sci_turbulence_measurements", sql)
        self.assertIn("REFERENCES sci_bronze_artifacts(id)", sql)
        self.assertIn("REFERENCES sci_labs(id)", sql)
        self.assertIn("REFERENCES sci_calibrations(id)", sql)

    def test_calibration_nullable_for_simulations(self):
        sql = _read_migration()
        # calibration_id has no NOT NULL: simulations carry no instrument
        self.assertIn("calibration_id uuid REFERENCES sci_calibrations(id),", sql)

    def test_conditions_lane_schema_check(self):
        sql = _read_migration()
        self.assertIn("conditions ?& array['domain', 'bc']", sql)

    def test_reynolds_positive_check(self):
        sql = _read_migration()
        self.assertIn("reynolds IS NULL OR reynolds > 0", sql)

    def test_solver_allowlist_check(self):
        sql = _read_migration()
        self.assertIn("('DNS', 'LES', 'RANS', 'URANS', 'DES')", sql)

    def test_dns_no_closure_check(self):
        sql = _read_migration()
        self.assertIn("chk_turb_dns_has_no_closure", sql)

    def test_gold_table_and_immutability_trigger(self):
        sql = _read_migration()
        self.assertIn("CREATE TABLE IF NOT EXISTS sci_turbulence_gold", sql)
        self.assertIn("sci_turbulence_gold_immutable", sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON sci_turbulence_gold", sql)
        self.assertIn("UNIQUE (dataset_version_id, measurement_id)", sql)

    def test_pydantic_rejects_nonpositive_reynolds(self):
        base = dict(artifact_id="a", lab_id="l", measured_at="2026-10-18T00:00:00Z",
                    case_name="channel_flow",
                    conditions={"domain": "d", "bc": "b"}, reynolds=-5.0)
        with self.assertRaises(ValidationError):
            schemas.MeasurementIngest(**base)

    def test_pydantic_rejects_conditions_missing_bc(self):
        base = dict(artifact_id="a", lab_id="l", measured_at="2026-10-18T00:00:00Z",
                    case_name="jet", conditions={"domain": "d"})
        with self.assertRaises(ValidationError):
            schemas.MeasurementIngest(**base)

    def test_pydantic_rejects_dns_with_closure(self):
        base = dict(artifact_id="a", lab_id="l", measured_at="2026-10-18T00:00:00Z",
                    case_name="channel_flow", solver="DNS",
                    closure_model="k-epsilon",
                    conditions={"domain": "d", "bc": "b"})
        with self.assertRaises(ValidationError):
            schemas.MeasurementIngest(**base)

    def test_pydantic_accepts_valid_record(self):
        rec = schemas.MeasurementIngest(
            artifact_id="a", lab_id="l", measured_at="2026-10-18T00:00:00Z",
            case_name="channel_flow", reynolds=2000.0, solver="LES",
            closure_model="smagorinsky",
            conditions={"domain": "2pi x 2 x pi", "bc": "periodic/no-slip"})
        self.assertEqual(rec.solver, "LES")
        self.assertTrue(rec.synthetic)


# ---------------------------------------------------------------------------
# 2. Provenance chain walk (§3.1)
# ---------------------------------------------------------------------------

class ProvenanceTests(unittest.TestCase):
    def _parts(self):
        meas = {"id": "m-1", "case_name": "channel_flow", "solver": "DNS",
                "synthetic": True}
        art = {"id": "art-1", "content_hash": "abc123",
               "collector_signature": "sig-9"}
        lab = {"id": "lab-1", "name": "Seed Lab", "verification_status": "verified"}
        cal = {"id": "cal-1", "instrument_id": "hotwire-02",
               "standard_ref": "NIST-1234"}
        gold = {"id": "g-1", "dataset_version_id": "ver-1"}
        return gold, meas, art, lab, cal

    def test_chain_order_and_links(self):
        gold, meas, art, lab, cal = self._parts()
        chain = models.walk_provenance(gold_row=gold, measurement=meas,
                                       artifact=art, lab=lab, calibration=cal)
        self.assertEqual([c["link"] for c in chain], list(models.CHAIN_LINKS))
        self.assertEqual(chain[2]["content_hash"], "abc123")
        self.assertEqual(chain[2]["collector_signature"], "sig-9")
        self.assertEqual(chain[4]["standard_ref"], "NIST-1234")

    def test_simulation_run_has_explicit_no_calibration_link(self):
        gold, meas, art, lab, _cal = self._parts()
        chain = models.walk_provenance(gold_row=gold, measurement=meas,
                                       artifact=art, lab=lab, calibration=None)
        self.assertEqual(chain[-1]["link"], "calibration")
        self.assertIsNone(chain[-1]["id"])
        self.assertIn("no instrument calibration", chain[-1]["note"])


# ---------------------------------------------------------------------------
# 3. Version immutability (§3.2)
# ---------------------------------------------------------------------------

class ImmutabilityTests(unittest.TestCase):
    def test_dataset_version_model_is_frozen(self):
        v = models.DatasetVersion(id="ver-1", dataset_name="twistor-turbulence-flowbench",
                                  semver="1.0.0", row_count=30,
                                  content_root_hash="ff" * 32)
        with self.assertRaises(ValidationError):
            v.semver = "1.0.1"  # new version = new row, never an update

    def test_gold_mutation_trigger_present(self):
        sql = _read_migration()
        self.assertIn("RAISE EXCEPTION 'sci_turbulence_gold is immutable", sql)


# ---------------------------------------------------------------------------
# 4. Correction supersession (§3.3)
# ---------------------------------------------------------------------------

class CorrectionTests(unittest.TestCase):
    def test_correction_supersedes_without_mutating_history(self):
        original = {"id": "m-1", "case_name": "jet", "reynolds": 11000.0,
                    "fidelity_metrics": {"l2_error_mean_velocity": 0.02}}
        before = dict(original)
        v2 = models.DatasetVersion(id="ver-2", dataset_name="twistor-turbulence-flowbench",
                                   semver="1.0.1", row_count=30,
                                   content_root_hash="ee" * 32,
                                   supersedes_id="ver-1",
                                   release_notes="fix jet Re_D typo")
        correction, version_row = models.apply_correction(
            measurement=original, reason="Re_D was 1100, not 11000",
            corrected_values={"reynolds": 1100.0},
            corrected_by_lab_id="lab-2", new_dataset_version=v2)
        # history untouched
        self.assertEqual(original, before)
        # correction carries the fix forward
        self.assertEqual(correction["corrects_silver_table"],
                         "sci_turbulence_measurements")
        self.assertEqual(correction["corrects_silver_id"], "m-1")
        self.assertEqual(correction["corrected_values"], {"reynolds": 1100.0})
        self.assertEqual(correction["new_dataset_version_id"], "ver-2")
        self.assertEqual(version_row["supersedes_id"], "ver-1")
        self.assertEqual(version_row["semver"], "1.0.1")


# ---------------------------------------------------------------------------
# 5. Seed integrity
# ---------------------------------------------------------------------------

class SeedTests(unittest.TestCase):
    def test_thirty_synthetic_rows(self):
        rows = seed.build_seed_records()
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(r["synthetic"] for r in rows))

    def test_case_and_solver_coverage(self):
        rows = seed.build_seed_records()
        self.assertEqual({r["case_name"] for r in rows},
                         {"channel_flow", "boundary_layer", "jet"})
        self.assertTrue({"DNS", "LES", "RANS"} <= {r["solver"] for r in rows})

    def test_reynolds_range(self):
        rows = seed.build_seed_records()
        res = [r["reynolds"] for r in rows]
        self.assertGreaterEqual(min(res), 1e3)
        self.assertLessEqual(max(res), 1e5)

    def test_conditions_keys_and_dns_no_closure(self):
        for r in seed.build_seed_records():
            self.assertIn("domain", r["conditions"])
            self.assertIn("bc", r["conditions"])
            self.assertIsNone(r["calibration_id"])  # simulations
            if r["solver"] == "DNS":
                self.assertIn(r["closure_model"], (None, "none", "resolved"))
            self.assertGreater(r["reynolds"], 0)

    def test_seed_is_deterministic(self):
        self.assertEqual(seed.build_seed_records(), seed.build_seed_records())

    def test_seed_summary(self):
        s = seed.seed_summary()
        self.assertEqual(s["row_count"], 30)
        self.assertTrue(s["synthetic"])


# ---------------------------------------------------------------------------
# 6. Router + toll emission (mocked)
# ---------------------------------------------------------------------------

class RouterTests(unittest.TestCase):
    def _call(self, db, method, path, **kw):
        client = _client(db)
        try:
            return getattr(client, method)(path, **kw)
        finally:
            _teardown(client)

    def test_list_datasets(self):
        resp = self._call(_seed_db(), "get", "/data/v1/science/turbulence/datasets")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["semver"], "1.0.0")
        self.assertEqual(body[0]["content_root_hash"], "deadbeef" * 8)

    def test_get_dataset_version_404(self):
        resp = self._call(_seed_db(), "get",
                          "/data/v1/science/turbulence/datasets/9.9.9")
        self.assertEqual(resp.status_code, 404)

    def test_query_filters(self):
        db = _seed_db()
        resp = self._call(db, "get", "/data/v1/science/turbulence/query",
                          params={"case_name": "channel_flow",
                                  "closure_model": "smagorinsky"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["row_count"] >= 1)
        self.assertTrue(all(r["case_name"] == "channel_flow" for r in body["rows"]))
        self.assertTrue(all(r["closure_model"] == "smagorinsky"
                            for r in body["rows"]))
        self.assertEqual(body["as_of"]["semver"], "1.0.0")
        self.assertTrue(body["provenance"]["synthetic"])

    def test_query_reynolds_range(self):
        resp = self._call(_seed_db(), "get", "/data/v1/science/turbulence/query",
                          params={"reynolds_min": 50000, "reynolds_max": 100000})
        body = resp.json()
        self.assertTrue(body["row_count"] >= 1)
        for r in body["rows"]:
            self.assertGreaterEqual(r["reynolds"], 50000)
            self.assertLessEqual(r["reynolds"], 100000)

    def test_benchmarks_leaderboard(self):
        resp = self._call(_seed_db(), "get",
                          "/data/v1/science/turbulence/benchmarks")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["metric"], "reynolds_stress_mae")
        self.assertEqual(body[0]["leaderboard"][0]["submitter"], "gnn-closure-x")

    def test_ingest_accepts_and_dead_letters(self):
        db = _seed_db()
        good = {"artifact_id": "a1", "lab_id": "lab-1",
                "measured_at": "2026-10-18T00:00:00Z",
                "case_name": "jet", "reynolds": 5000.0, "solver": "LES",
                "closure_model": "wale",
                "conditions": {"domain": "round jet", "bc": "inflow/outflow"}}
        # invalid: DNS with a closure model -> rejected by pydantic (422)
        bad = dict(good, solver="DNS", closure_model="k-epsilon")
        resp = self._call(db, "post", "/data/v1/science/turbulence/ingest",
                          json={"records": [good, bad]})
        self.assertEqual(resp.status_code, 422)  # batch-level validation

        resp = self._call(db, "post", "/data/v1/science/turbulence/ingest",
                          json={"records": [good]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["accepted"], 1)
        self.assertEqual(body["dead_letters"], [])
        self.assertTrue(body["batch_id"].startswith("sci:"))
        self.assertEqual(len(db.tables["sci_turbulence_measurements"]), 31)

    def test_toll_emission_attempted_with_idempotency_key(self):
        db = _seed_db()
        with mock.patch.object(turb_router, "record_toll_event") as toll:
            resp = self._call(db, "get",
                              "/data/v1/science/turbulence/query",
                              params={"case_name": "jet"})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(toll.called)
            _, kw = toll.call_args
            self.assertEqual(kw["event_type"], "data.science.query")
            self.assertTrue(kw["idempotency_key"].startswith("sci:"))
            self.assertEqual(kw["org_id"], "org-1")

    def test_toll_event_types_per_endpoint(self):
        db = _seed_db()
        with mock.patch.object(turb_router, "record_toll_event") as toll:
            self._call(db, "get", "/data/v1/science/turbulence/benchmarks")
            events = {c.kwargs["event_type"] for c in toll.call_args_list}
            self.assertIn("data.science.benchmark", events)
        with mock.patch.object(turb_router, "record_toll_event") as toll:
            good = {"artifact_id": "a1", "lab_id": "lab-1",
                    "measured_at": "2026-10-18T00:00:00Z", "case_name": "jet",
                    "conditions": {"domain": "d", "bc": "b"}}
            self._call(db, "post", "/data/v1/science/turbulence/ingest",
                       json={"records": [good]})
            events = {c.kwargs["event_type"] for c in toll.call_args_list}
            self.assertIn("data.science.ingest", events)

    def test_toll_valueerror_is_logged_not_raised(self):
        db = _seed_db()
        with mock.patch.object(turb_router, "record_toll_event",
                               side_effect=ValueError("Unknown toll event type")):
            resp = self._call(db, "get", "/data/v1/science/turbulence/datasets")
            self.assertEqual(resp.status_code, 200)  # tolls never break reads


if __name__ == "__main__":
    unittest.main()

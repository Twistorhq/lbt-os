"""TW-569 Niche 1 (trades benchmark layer): TDD prove-it tests.

Gates under test (Rosa's TW-564 findings):
- Major 1: cohort anonymity — no query path from a cohort row to a
  contributor org (shared-asset data_events carry artifact_id=None).
- Major 5: idempotent tolling — duplicate Idempotency-Key never double-tolls.
- DATA LAYER DOCTRINE (LAW): dynamic volume-tiered pricing — the applied
  tier is stamped on the toll row and moves with platform volume.
- Minor 13: hold-and-suppress — 3 consecutive passing refreshes to publish.
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.data_layer import toll_worker
from app.data_layer.benchmarks import emitters, rollup
from app.routers import data_v1


def _fake_db():
    """Supabase-client-shaped mock with table-level call capture."""
    db = MagicMock()
    result = MagicMock()
    result.data = []
    tbl = db.table.return_value
    tbl.insert.return_value.execute.return_value = result
    tbl.update.return_value.eq.return_value.execute.return_value = result
    tbl.upsert.return_value.execute.return_value = result
    sel = tbl.select.return_value
    sel.execute.return_value = result
    sel.eq.return_value.execute.return_value = result
    sel.gte.return_value.execute.return_value = result
    sel.order.return_value.execute.return_value = result
    sel.order.return_value.limit.return_value.execute.return_value = result
    sel.eq.return_value.limit.return_value.execute.return_value = result
    sel.gte.return_value.limit.return_value.execute.return_value = result
    return db, tbl


TIERS = [
    {"upto_calls": 1000, "per_call_cents": 25, "tier": "tier_0"},
    {"upto_calls": 10000, "per_call_cents": 20, "tier": "tier_1"},
    {"upto_calls": None, "per_call_cents": 15, "tier": "tier_2"},
]


class VolumeTierTests(unittest.TestCase):
    def test_tier_resolves_below_first_threshold(self):
        self.assertEqual(toll_worker.resolve_tier(0, TIERS)["tier"], "tier_0")
        self.assertEqual(toll_worker.resolve_tier(999, TIERS)["tier"], "tier_0")

    def test_tier_steps_down_as_volume_crosses_thresholds(self):
        self.assertEqual(toll_worker.resolve_tier(1000, TIERS)["tier"], "tier_1")
        self.assertEqual(toll_worker.resolve_tier(9999, TIERS)["tier"], "tier_1")
        self.assertEqual(toll_worker.resolve_tier(10000, TIERS)["tier"], "tier_2")
        self.assertEqual(toll_worker.resolve_tier(10**9, TIERS)["tier"], "tier_2")

    def test_compute_charge_applies_tier_rate(self):
        plan = {"per_call_cents": 25, "derivative_params": {"volume_tiers": TIERS}}
        low = toll_worker.compute_charge(plan, volume=10)
        high = toll_worker.compute_charge(plan, volume=50_000)
        self.assertEqual((low["amount_cents"], low["applied_tier"]), (25, "tier_0"))
        self.assertEqual((high["amount_cents"], high["applied_tier"]), (15, "tier_2"))

    def test_compute_charge_without_tiers_uses_plan_base(self):
        plan = {"per_call_cents": 25, "derivative_params": {}}
        c = toll_worker.compute_charge(plan, volume=10**9)
        self.assertEqual(c["amount_cents"], 25)
        self.assertIsNone(c["applied_tier"])


class TollDedupeTests(unittest.TestCase):
    def test_duplicate_idempotency_key_never_double_tolls(self):
        db, tbl = _fake_db()
        # data_toll_dedupe HAS the key: already tolled.
        dedupe_tbl = MagicMock()
        dedupe_res = MagicMock()
        dedupe_res.data = [{"idempotency_key": "abc-123"}]
        dedupe_tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = dedupe_res

        def table_side_effect(name):
            return dedupe_tbl if name == "data_toll_dedupe" else tbl

        db.table.side_effect = table_side_effect

        job = {
            "id": "job-1",
            "idempotency_key": "abc-123",
            "key_id": "k1",
            "billing_org_id": "org-1",
            "endpoint": "GET /x",
            "plan_id": None,
        }
        with patch.object(toll_worker.toll_service, "record_toll_event") as rec:
            outcome = toll_worker._process_job(db, job, volume=0)
        self.assertEqual(outcome, "skipped")
        rec.assert_not_called()

    def test_first_seen_key_emits_toll_with_tier_stamp(self):
        db, tbl = _fake_db()
        dedupe_tbl = MagicMock()
        dedupe_res = MagicMock()
        dedupe_res.data = []
        dedupe_tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = dedupe_res
        plan_tbl = MagicMock()
        plan_res = MagicMock()
        plan_res.data = [
            {
                "id": "p1",
                "per_call_cents": 25,
                "derivative_type": None,
                "derivative_params": {"volume_tiers": TIERS},
            }
        ]
        plan_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = plan_res

        def table_side_effect(name):
            if name == "data_toll_dedupe":
                return dedupe_tbl
            if name == "data_api_plans":
                return plan_tbl
            return tbl

        db.table.side_effect = table_side_effect

        job = {
            "id": "job-1",
            "idempotency_key": "fresh-key",
            "key_id": "k1",
            "billing_org_id": "org-1",
            "endpoint": "GET /x",
            "plan_id": "p1",
        }
        with patch.object(toll_worker.toll_service, "record_toll_event") as rec:
            outcome = toll_worker._process_job(db, job, volume=50_000)
        self.assertEqual(outcome, "done")
        rec.assert_called_once()
        kwargs = rec.call_args[1]
        self.assertEqual(kwargs["amount_cents"], 15)
        self.assertEqual(kwargs["applied_tier"], "tier_2")
        self.assertEqual(kwargs["org_id"], "org-1")  # billing org, never orphan


class EmitterMathTests(unittest.TestCase):
    def test_recovery_rate_and_response_median(self):
        recs = [
            {
                "sms_sent_at": "2026-10-10T10:05:00+00:00",
                "recovered_at": "2026-10-10T11:00:00+00:00",
                "call_started_at": "2026-10-10T10:00:00+00:00",
            },
            {
                "sms_sent_at": "2026-10-10T12:15:00+00:00",
                "recovered_at": None,
                "call_started_at": "2026-10-10T12:00:00+00:00",
            },
        ]
        snap = emitters.compute_snapshot("o1", recs, [], [], 0)
        by_name = {m["metric_name"]: m["metric_value"] for m in snap["metrics"]}
        self.assertAlmostEqual(by_name["missed_call_recovery_rate"], 0.5)
        self.assertAlmostEqual(by_name["time_to_first_response_min"], 10.0)

    def test_estimate_close_rate_and_job_values(self):
        quotes = [
            {"status": "won"},
            {"status": "won"},
            {"status": "lost"},
            {"status": "draft"},
        ]
        jobs = [
            {"status": "completed", "total": 400.0},
            {"status": "completed", "total": 600.0},
            {"status": "scheduled", "total": 999.0},
        ]
        snap = emitters.compute_snapshot("o1", [], quotes, jobs, 100)
        by_name = {m["metric_name"]: m["metric_value"] for m in snap["metrics"]}
        self.assertAlmostEqual(by_name["estimate_close_rate"], 2 / 3)
        self.assertAlmostEqual(by_name["avg_job_value_cents"], 50000.0)
        self.assertAlmostEqual(by_name["toll_spend_per_booked_job_cents"], 50.0)

    def test_empty_inputs_yield_no_metrics_not_zeros(self):
        snap = emitters.compute_snapshot("o1", [], [], [], 0)
        self.assertEqual(snap["metrics"], [])

    def test_cohort_key_format(self):
        self.assertEqual(emitters.cohort_key("HVAC", "CO", "smb"), "hvac:co:smb")
        self.assertEqual(emitters.shop_size_bucket(1), "solo")
        self.assertEqual(emitters.shop_size_bucket(7), "smb")
        self.assertEqual(emitters.shop_size_bucket(25), "mid")
        self.assertEqual(emitters.shop_size_bucket(200), "large")


class CohortAnonymityTests(unittest.TestCase):
    def _rows(self, n_orgs, key="hvac:co:smb"):
        return [
            {
                "org_id": f"org-{i}",
                "cohort_key": key,
                "metric_name": "estimate_close_rate",
                "metric_value": 0.5,
                "period": "30d",
            }
            for i in range(n_orgs)
        ]

    def test_k_anonymity_blocks_small_cohorts(self):
        stats = rollup.compute_cohort_stats(self._rows(4))
        self.assertFalse(stats[0]["meets_threshold"])
        self.assertIsNone(stats[0]["p50"])

    def test_k_anonymity_passes_at_five(self):
        stats = rollup.compute_cohort_stats(self._rows(5))
        self.assertTrue(stats[0]["meets_threshold"])
        self.assertEqual(stats[0]["n_orgs"], 5)
        self.assertAlmostEqual(stats[0]["p50"], 0.5)

    def test_duplicate_org_counted_once(self):
        rows = self._rows(3) + self._rows(3)  # same 3 orgs twice
        stats = rollup.compute_cohort_stats(rows)
        self.assertEqual(stats[0]["n_orgs"], 3)
        self.assertFalse(stats[0]["meets_threshold"])

    def test_rollup_lineage_terminates_at_batch(self):
        """Major 1: shared-asset data_events carry artifact_id=None."""
        db, tbl = _fake_db()
        silver_tbl = MagicMock()
        silver_res = MagicMock()
        silver_res.data = []
        silver_tbl.select.return_value.execute.return_value = silver_res
        db.table.side_effect = lambda name: (
            silver_tbl if name == "benchmark_org_metrics" else tbl
        )
        rollup.refresh_gold(db, batch_id="batch-1")
        event_calls = [
            c
            for c in tbl.insert.call_args_list
            if c[0][0].get("silver_table") == "benchmark_cohort_stats"
        ]
        self.assertTrue(event_calls, "expected a shared-asset lineage event")
        for c in event_calls:
            row = c[0][0]
            self.assertIsNone(row["artifact_id"])
            self.assertEqual(row["promotion_batch_id"], "batch-1")


class HoldAndSuppressTests(unittest.TestCase):
    def _db_with_existing(self, passes, published):
        db, tbl = _fake_db()
        silver_tbl = MagicMock()
        rows = [
            {
                "org_id": f"org-{i}",
                "cohort_key": "hvac:co:smb",
                "metric_name": "estimate_close_rate",
                "metric_value": 0.6,
                "period": "30d",
                "computed_at": "2026-10-16T00:00:00+00:00",
            }
            for i in range(6)
        ]
        silver_res = MagicMock()
        silver_res.data = rows
        silver_tbl.select.return_value.execute.return_value = silver_res
        cohort_tbl = MagicMock()
        cohort_res = MagicMock()
        cohort_res.data = (
            [{"id": "c1", "consecutive_passes": passes, "published": published}]
            if passes is not None
            else []
        )
        cohort_tbl.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = cohort_res

        def table_side_effect(name):
            if name == "benchmark_org_metrics":
                return silver_tbl
            if name == "benchmark_cohort_stats":
                return cohort_tbl
            return tbl

        db.table.side_effect = table_side_effect
        return db, cohort_tbl

    def test_three_consecutive_passes_to_publish(self):
        for passes, expect_published in [(0, False), (1, False), (2, True)]:
            db, cohort_tbl = self._db_with_existing(passes, False)
            rollup.refresh_gold(db, batch_id="b")
            payload = cohort_tbl.upsert.call_args[0][0]
            self.assertEqual(payload["published"], expect_published, f"passes={passes}")
            self.assertEqual(payload["consecutive_passes"], passes + 1)

    def test_below_threshold_flips_published_false(self):
        db, tbl = _fake_db()
        silver_tbl = MagicMock()
        rows = [
            {
                "org_id": f"org-{i}",
                "cohort_key": "hvac:co:smb",
                "metric_name": "estimate_close_rate",
                "metric_value": 0.6,
                "period": "30d",
                "computed_at": "2026-10-16T00:00:00+00:00",
            }
            for i in range(2)  # below k=5
        ]
        silver_res = MagicMock()
        silver_res.data = rows
        silver_tbl.select.return_value.execute.return_value = silver_res
        cohort_tbl = MagicMock()
        cohort_res = MagicMock()
        cohort_res.data = [{"id": "c1", "consecutive_passes": 3, "published": True}]
        cohort_tbl.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = cohort_res

        def table_side_effect(name):
            return {
                "benchmark_org_metrics": silver_tbl,
                "benchmark_cohort_stats": cohort_tbl,
            }.get(name, tbl)

        db.table.side_effect = table_side_effect
        out = rollup.refresh_gold(db, batch_id="b")
        payload = cohort_tbl.update.call_args[0][0]
        self.assertFalse(payload["published"])
        self.assertEqual(payload["consecutive_passes"], 0)
        self.assertEqual(out["suppressed"], 1)


class ApiContractTests(unittest.TestCase):
    def test_missing_idempotency_key_is_400(self):
        with patch.object(data_v1, "get_db") as mock_db:
            with self.assertRaises(HTTPException) as ctx:
                data_v1.get_benchmarks(
                    cohort_key="hvac:co:smb",
                    metric=None,
                    period="30d",
                    x_api_key="k",
                    idempotency_key=None,
                )
        self.assertEqual(ctx.exception.status_code, 400)
        mock_db.assert_not_called()  # rejected before any DB touch

    def test_unknown_metric_is_400(self):
        with patch.object(data_v1, "get_db"):
            with self.assertRaises(HTTPException) as ctx:
                data_v1.get_benchmarks(
                    cohort_key="hvac:co:smb",
                    metric="not_a_metric",
                    period="30d",
                    x_api_key="k",
                    idempotency_key="idem-1",
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_api_key_hash_roundtrip(self):
        h = data_v1.hash_api_key("sk_test_abc123")
        self.assertTrue(data_v1.verify_api_key("sk_test_abc123", h))
        self.assertFalse(data_v1.verify_api_key("sk_test_wrong", h))

    def test_rate_limit_429s(self):
        data_v1._buckets.clear()
        for _ in range(3):
            data_v1._check_rate_limit("k-test", 3)
        with self.assertRaises(HTTPException) as ctx:
            data_v1._check_rate_limit("k-test", 3)
        self.assertEqual(ctx.exception.status_code, 429)


class FixRoundTests(unittest.TestCase):
    """TW-569 Rosa round-1 findings: blocker + 4 majors, all with tests."""

    def test_redact_uses_real_hmac_not_static_salt(self):
        """BLOCKER 1: org_id -> real HMAC-SHA256, 64 hex chars, keyed."""
        import os

        os.environ["DATALAYER_HMAC_SECRET"] = "test-secret-xyz"
        from app.data_layer.benchmarks import promote

        promote._redaction_spec.cache_clear()
        out = promote._redact({"org_id": "org-123", "cohort_key": "hvac:co:smb"})
        ref = out["org_id"]
        self.assertTrue(ref.startswith("hmac:"))
        digest = ref.split("hmac:")[1]
        self.assertEqual(len(digest), 64)  # full SHA-256, not truncated
        # Deterministic for the same secret...
        out2 = promote._redact({"org_id": "org-123"})
        self.assertEqual(out2["org_id"], ref)
        # ...and different without the static-salt construction.
        import hashlib

        fake = (
            "hmac:" + hashlib.sha256(b"trades_benchmarks:v1:org-123").hexdigest()[:16]
        )
        self.assertNotEqual(ref, fake)

    def test_redact_drops_pii_and_unlisted_fields(self):
        """BLOCKER 1: the YAML spec is actually loaded and enforced."""
        from app.data_layer.benchmarks import promote

        promote._redaction_spec.cache_clear()
        out = promote._redact(
            {
                "org_id": "o1",
                "phone": "+15551234567",
                "email": "a@b.c",
                "customer_id": "c9",
                "notes": "called twice",
                "surprise_field": "should not leak",
                "cohort_key": "hvac:co:smb",
                "metrics": [],
            }
        )
        for gone in ("phone", "email", "customer_id", "notes", "surprise_field"):
            self.assertNotIn(gone, out)
        self.assertIn("cohort_key", out)  # aggregates pass through

    def test_schema_contract_rejects_unknown_metric(self):
        """MAJOR 1: v1.json is enforced; bad snapshots raise."""
        import jsonschema

        from app.data_layer.benchmarks import promote

        promote._batch_schema.cache_clear()
        bad = {
            "org_id": "o1",
            "cohort_key": "hvac:co:smb",
            "period": "30d",
            "computed_at": "2026-10-16T00:00:00+00:00",
            "metrics": [{"metric_name": "not_a_real_metric", "metric_value": 1.0}],
        }
        with self.assertRaises(jsonschema.ValidationError):
            promote._validate_snapshot(bad)
        good = dict(
            bad, metrics=[{"metric_name": "estimate_close_rate", "metric_value": 0.5}]
        )
        promote._validate_snapshot(good)  # must not raise

    def test_idempotency_namespace_is_per_client(self):
        """MAJOR 2: same idempotency key, different key_ids -> both tolled."""
        db, tbl = _fake_db()
        dedupe_tbl = MagicMock()
        dedupe_res = MagicMock()
        dedupe_res.data = []
        dedupe_tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = dedupe_res
        plan_tbl = MagicMock()
        plan_res = MagicMock()
        plan_res.data = []
        plan_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = plan_res

        seen_queries = []

        def table_side_effect(name):
            if name == "data_toll_dedupe":
                return dedupe_tbl
            if name == "data_api_plans":
                return plan_tbl
            return tbl

        db.table.side_effect = table_side_effect

        for key_id in ("key-A", "key-B"):
            job = {
                "id": f"job-{key_id}",
                "idempotency_key": "SAME-KEY",
                "key_id": key_id,
                "billing_org_id": "org-1",
                "endpoint": "GET /x",
                "plan_id": None,
            }
            with patch.object(toll_worker.toll_service, "record_toll_event") as rec:
                outcome = toll_worker._process_job(db, job, volume=0)
            self.assertEqual(outcome, "done")
            seen_queries.append(rec.call_args[1]["idempotency_key"])
        # Namespaced: the two tolls carry distinct idempotency keys.
        self.assertEqual(seen_queries, ["toll:key-A:SAME-KEY", "toll:key-B:SAME-KEY"])

    def test_replay_promotion_is_silver_noop(self):
        """MAJOR 4: artifact already promoted -> no silver re-insert."""
        from app.data_layer.benchmarks import promote

        db, tbl = _fake_db()
        # _get_source_id
        src_tbl = MagicMock()
        src_res = MagicMock()
        src_res.data = [{"id": "src-1"}]
        src_tbl.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = src_res
        # bronze_artifacts: already promoted
        bronze_tbl = MagicMock()
        bronze_res = MagicMock()
        bronze_res.data = [{"id": "art-1", "promotion_status": "promoted"}]
        bronze_tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = bronze_res

        def table_side_effect(name):
            return {"data_sources": src_tbl, "bronze_artifacts": bronze_tbl}.get(
                name, tbl
            )

        db.table.side_effect = table_side_effect

        snap = {
            "org_id": "org-9",
            "cohort_key": "hvac:co:smb",
            "period": "30d",
            "computed_at": "2026-10-16T00:00:00+00:00",
            "metrics": [{"metric_name": "estimate_close_rate", "metric_value": 0.5}],
        }
        with patch.object(promote.emitters, "emit_org_snapshot", return_value=snap):
            ok = promote._promote_org(db, "src-1", "run-1", "org-9")
        self.assertTrue(ok)
        # No silver upsert happened: find the benchmark_org_metrics table mock
        # and assert upsert was never called on it.
        silver_upsert_calls = [c for c in tbl.upsert.call_args_list]
        self.assertEqual(silver_upsert_calls, [])

    def test_authenticate_uses_indexed_exact_match(self):
        """MAJOR 3: no full-table scan; the query pins key_hash."""
        db, tbl = _fake_db()
        captured = {}

        class CapTable:
            def select(self, *a):
                return self

            def eq(self, col, val):
                captured[col] = val
                return self

            def limit(self, *a):
                return self

            def execute(self):
                r = MagicMock()
                r.data = []
                return r

        db.table.side_effect = lambda name: CapTable()
        with self.assertRaises(HTTPException) as ctx:
            data_v1._authenticate(db, "sk_test_abc123")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("key_hash", captured)
        # The queried hash must equal the deterministic HMAC of the key.
        self.assertEqual(captured["key_hash"], data_v1.hash_api_key("sk_test_abc123"))


if __name__ == "__main__":
    unittest.main()

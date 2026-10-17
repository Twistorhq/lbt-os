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
        dedupe_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = dedupe_res

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
        dedupe_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = dedupe_res
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
        snap = emitters.compute_snapshot("o1", [], recs, [], [], 0)
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
        snap = emitters.compute_snapshot("o1", [], [], quotes, jobs, 100)
        by_name = {m["metric_name"]: m["metric_value"] for m in snap["metrics"]}
        self.assertAlmostEqual(by_name["estimate_close_rate"], 2 / 3)
        self.assertAlmostEqual(by_name["avg_job_value_cents"], 50000.0)
        self.assertAlmostEqual(by_name["toll_spend_per_booked_job_cents"], 50.0)

    def test_empty_inputs_yield_no_metrics_not_zeros(self):
        snap = emitters.compute_snapshot("o1", [], [], [], [], 0)
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
            payload = cohort_tbl.update.call_args[0][0]
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
                    request=MagicMock(),
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
                    request=MagicMock(),
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


if __name__ == "__main__":
    unittest.main()

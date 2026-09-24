"""Tests for the TW-201 leak-detection engine (Zeke Okafor).

Detectors are plugins: one engine, twelve aim-points. v1 ships the three
HVAC detectors live for the Oct 2 launch. Leak findings are never
fabricated: no data means an honest empty state.
"""
import unittest
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def _days_ago(n):
    return (_now() - timedelta(days=n)).isoformat()


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._table = None

    def select(self, _cols):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def in_(self, col, values):
        self._rows = [r for r in self._rows if r.get(col) in values]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return FakeResult(self._rows)


class FakeDB:
    """In-memory fake of the Supabase tables the detectors read."""

    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self._tables[name])


ORG = "org-123"

ASSETS = [
    # 18-year-old furnace: past expected life -> happened
    {"id": "a1", "org_id": ORG, "customer_id": "c1", "customer_name": "Smith",
     "asset_type": "furnace", "brand": "Carrier", "install_date": _days_ago(18 * 365),
     "expected_life_years": 18},
    # 9.5-year-old AC: enters the 10y window within 12 months -> will
    {"id": "a2", "org_id": ORG, "customer_id": "c2", "customer_name": "Jones",
     "asset_type": "ac", "brand": "Trane", "install_date": _days_ago(int(9.5 * 365)),
     "expected_life_years": 12},
    # 3-year-old furnace: healthy -> no finding
    {"id": "a3", "org_id": ORG, "customer_id": "c3", "customer_name": "Lee",
     "asset_type": "furnace", "brand": "Lennox", "install_date": _days_ago(3 * 365),
     "expected_life_years": 18},
    # other org's ancient furnace: must never leak across orgs
    {"id": "a9", "org_id": "org-other", "customer_id": "c9", "customer_name": "Other",
     "asset_type": "furnace", "brand": "Rheem", "install_date": _days_ago(25 * 365),
     "expected_life_years": 18},
]

PLANS = [
    # member who skipped both seasonal visits -> watch
    {"id": "p1", "org_id": ORG, "customer_id": "c1", "customer_name": "Smith",
     "plan_name": "Comfort Club", "status": "active", "billing_status": "current",
     "visits_per_year": 2, "visits_completed": 0, "started_at": _days_ago(300)},
    # card failure -> urgent involuntary churn watch
    {"id": "p2", "org_id": ORG, "customer_id": "c2", "customer_name": "Jones",
     "plan_name": "Comfort Club", "status": "active", "billing_status": "card_failed",
     "visits_per_year": 2, "visits_completed": 2, "started_at": _days_ago(300)},
    # healthy member -> no finding
    {"id": "p3", "org_id": ORG, "customer_id": "c3", "customer_name": "Lee",
     "plan_name": "Comfort Club", "status": "active", "billing_status": "current",
     "visits_per_year": 2, "visits_completed": 2, "started_at": _days_ago(300)},
]

QUOTES = [
    # $12k quote, never followed up, 20 days old -> resurrection candidate
    {"id": "q1", "org_id": ORG, "customer_id": "c1", "customer_name": "Smith",
     "total": 12000.0, "status": "sent", "sent_at": _days_ago(20),
     "last_follow_up_at": None, "follow_up_count": 0},
    # followed up 2 days ago -> leave it alone
    {"id": "q2", "org_id": ORG, "customer_id": "c2", "customer_name": "Jones",
     "total": 8500.0, "status": "sent", "sent_at": _days_ago(10),
     "last_follow_up_at": _days_ago(2), "follow_up_count": 2},
    # won quote -> not a leak
    {"id": "q3", "org_id": ORG, "customer_id": "c3", "customer_name": "Lee",
     "total": 4000.0, "status": "won", "sent_at": _days_ago(30),
     "last_follow_up_at": _days_ago(25), "follow_up_count": 1},
]

LEADS = [
    # stalled proposal-stage lead, 25 days untouched -> fallback resurrection
    {"id": "l1", "org_id": ORG, "name": "Garcia", "status": "proposal",
     "estimated_value": 9500.0, "stage_changed_at": _days_ago(25),
     "created_at": _days_ago(40)},
]


def _db(**overrides):
    tables = {
        "organizations": [{"id": ORG, "industry": "hvac"}],
        "service_assets": ASSETS,
        "service_plans": PLANS,
        "quotes": QUOTES,
        "leads": LEADS,
    }
    tables.update(overrides)
    return FakeDB(tables)


from app.leak_engine import registry  # noqa: E402
from app.leak_engine.engine import run_leak_scan  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_hvac_detectors_registered(self):
        names = {d.name: d for d in registry.all_detectors()}
        for expected in ("equipment-age-graveyard", "plan-churn-risk", "quote-resurrection"):
            self.assertIn(expected, names, f"detector {expected} not registered")
            self.assertEqual(names[expected].vertical, "hvac")

    def test_detector_declares_inputs(self):
        names = {d.name: d for d in registry.all_detectors()}
        self.assertIn("service_assets", names["equipment-age-graveyard"].requires)
        self.assertIn("service_plans", names["plan-churn-risk"].requires)
        self.assertIn("quotes", names["quote-resurrection"].requires)


class EquipmentGraveyardTest(unittest.TestCase):
    def test_past_life_unit_is_happened_finding(self):
        findings = run_leak_scan(_db(), ORG)["what_happened"]
        grave = [f for f in findings if f["detector"] == "equipment-age-graveyard"]
        self.assertEqual(len(grave), 1)
        self.assertEqual(grave[0]["count"], 1)
        self.assertIn("Smith", str(grave[0]["entities"]))
        self.assertFalse(grave[0]["is_demo"])

    def test_entering_window_unit_is_will_finding(self):
        findings = run_leak_scan(_db(), ORG)["what_will_happen"]
        grave = [f for f in findings if f["detector"] == "equipment-age-graveyard"]
        self.assertEqual(len(grave), 1)
        self.assertIn("Jones", str(grave[0]["entities"]))

    def test_other_org_assets_never_leak(self):
        brief = run_leak_scan(_db(), ORG)
        blob = str(brief)
        self.assertNotIn("org-other", blob)
        self.assertNotIn("Rheem", blob)


class PlanChurnTest(unittest.TestCase):
    def test_skipped_visits_is_watch(self):
        findings = run_leak_scan(_db(), ORG)["what_happened"]
        churn = [f for f in findings if f["detector"] == "plan-churn-risk"]
        self.assertTrue(any("Smith" in str(f["entities"]) for f in churn))

    def test_card_failure_is_urgent(self):
        findings = run_leak_scan(_db(), ORG)["what_should_we_do"]
        churn = [f for f in findings if f["detector"] == "plan-churn-risk"]
        urgent = [f for f in churn if f["severity"] == "urgent"]
        self.assertTrue(any("Jones" in str(f["entities"]) for f in urgent))


class QuoteResurrectionTest(unittest.TestCase):
    def test_stalled_quote_is_resurrection_candidate(self):
        findings = run_leak_scan(_db(), ORG)["what_should_we_do"]
        res = [f for f in findings if f["detector"] == "quote-resurrection"]
        self.assertTrue(any("Smith" in str(f["entities"]) for f in res))
        total_value = sum(f.get("estimated_value") or 0 for f in res)
        self.assertGreaterEqual(total_value, 12000.0)

    def test_recently_followed_up_quote_left_alone(self):
        brief = run_leak_scan(_db(), ORG)
        self.assertNotIn("q2", str(brief))

    def test_stalled_proposal_lead_falls_back(self):
        db = _db(quotes=[])
        findings = run_leak_scan(db, ORG)["what_should_we_do"]
        res = [f for f in findings if f["detector"] == "quote-resurrection"]
        self.assertTrue(any("Garcia" in str(f["entities"]) for f in res))


class EngineTest(unittest.TestCase):
    def test_brief_groups_by_ladder(self):
        brief = run_leak_scan(_db(), ORG)
        self.assertIn("what_happened", brief)
        self.assertIn("what_will_happen", brief)
        self.assertIn("what_should_we_do", brief)
        self.assertIn("totals", brief)
        self.assertGreater(brief["totals"]["findings"], 0)

    def test_missing_table_degrades_honestly(self):
        db = _db()
        del db._tables["service_assets"]
        brief = run_leak_scan(db, ORG)  # must not raise
        self.assertIn("service_assets", brief["data_status"]["insufficient"])

    def test_empty_tables_means_empty_findings_not_fake_ones(self):
        db = _db(service_assets=[], service_plans=[], quotes=[], leads=[])
        brief = run_leak_scan(db, ORG)
        self.assertEqual(brief["totals"]["findings"], 0)
        self.assertNotIn("demo", str(brief).lower())


if __name__ == "__main__":
    unittest.main()


from app.services import benchmarks as bm  # noqa: E402


class BenchmarkDB(FakeDB):
    def __init__(self, tables):
        super().__init__(tables)
        self.inserted = []

    def table(self, name):
        if name == "benchmark_org_metrics":
            return _InsertCapture(self, name)
        return super().table(name)


class _InsertCapture(FakeQuery):
    def __init__(self, db, name):
        super().__init__(db._tables.get(name, []))
        self._db = db
        self._name = name

    def insert(self, rows):
        self._db.inserted.extend(rows if isinstance(rows, list) else [rows])
        return self


class BenchmarkTest(unittest.TestCase):
    def test_no_consent_means_no_recording(self):
        db = BenchmarkDB({"organizations": [{"id": ORG, "benchmark_consent": False}]})
        result = bm.record_org_metrics(db, ORG, "hvac", {"quote_close_rate": 24.0})
        self.assertEqual(result["recorded"], 0)
        self.assertEqual(result["reason"], "no_consent")
        self.assertEqual(db.inserted, [])

    def test_consent_records_private_metrics(self):
        db = BenchmarkDB({"organizations": [{"id": ORG, "benchmark_consent": True}]})
        result = bm.record_org_metrics(db, ORG, "hvac", {"quote_close_rate": 24.0})
        self.assertEqual(result["recorded"], 1)
        self.assertEqual(db.inserted[0]["org_id"], ORG)
        self.assertEqual(db.inserted[0]["metric_name"], "quote_close_rate")

    def test_small_cohort_reports_insufficient(self):
        db = _db()
        db._tables["benchmark_org_metrics"] = [
            {"org_id": ORG, "metric_name": "quote_close_rate",
             "metric_value": 22.0, "computed_at": _days_ago(1)}
        ]
        db._tables["benchmark_cohort_stats"] = [
            {"cohort_key": "hvac:smb", "metric_name": "quote_close_rate",
             "p50": 34.0, "mean": 33.1, "n_orgs": 3, "period": "30d"}
        ]
        result = bm.get_cohort_comparison(db, ORG, "hvac", "quote_close_rate")
        self.assertEqual(result["status"], "insufficient_cohort_data")

    def test_full_cohort_compares_without_leaking(self):
        db = _db()
        db._tables["benchmark_org_metrics"] = [
            {"org_id": ORG, "metric_name": "quote_close_rate",
             "metric_value": 22.0, "computed_at": _days_ago(1)}
        ]
        db._tables["benchmark_cohort_stats"] = [
            {"cohort_key": "hvac:smb", "metric_name": "quote_close_rate",
             "p50": 34.0, "mean": 33.1, "n_orgs": 7, "period": "30d"}
        ]
        result = bm.get_cohort_comparison(db, ORG, "hvac", "quote_close_rate")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["yours"], 22.0)
        self.assertEqual(result["cohort_median"], 34.0)
        self.assertEqual(result["cohort_size"], 7)


from app.leak_engine import detectors_for, vertical_for_industry  # noqa: E402


class ProductionShapeRegressionTest(unittest.TestCase):
    """Rosa B1/B2: fixtures must match production data shapes."""

    def test_naive_date_install_date_does_not_raise(self):
        # PostgREST DATE columns arrive as naive "YYYY-MM-DD" (Rosa B1).
        db = _db(service_assets=[
            {"id": "ax", "org_id": ORG, "customer_id": "c1",
             "customer_name": "Naive", "asset_type": "furnace",
             "brand": "Carrier", "install_date": "2005-01-15",
             "expected_life_years": 18},
        ])
        brief = run_leak_scan(db, ORG)  # must not raise, must not error
        self.assertNotIn("equipment-age-graveyard",
                         brief["data_status"].get("errors", []))
        grave = [f for f in brief["what_happened"]
                 if f["detector"] == "equipment-age-graveyard"]
        self.assertEqual(len(grave), 1)
        self.assertIn("Naive", str(grave[0]["entities"]))

    def test_canonical_air_conditioner_value_matches(self):
        # The documented import contract says "air_conditioner" (Rosa B2).
        db = _db(service_assets=[
            {"id": "ax", "org_id": ORG, "customer_id": "c1",
             "customer_name": "Cool", "asset_type": "air_conditioner",
             "brand": "Trane", "install_date": _days_ago(int(11 * 365)),
             "expected_life_years": 15},
        ])
        findings = run_leak_scan(db, ORG)["what_happened"]
        grave = [f for f in findings
                 if f["detector"] == "equipment-age-graveyard"]
        self.assertEqual(len(grave), 1)
        self.assertIn("Cool", str(grave[0]["entities"]))


class VerticalMappingTest(unittest.TestCase):
    def test_industry_maps_to_vertical(self):
        self.assertEqual(vertical_for_industry("hvac"), "hvac")
        self.assertEqual(vertical_for_industry("electrician"), "electrical")
        self.assertEqual(vertical_for_industry(None), "hvac")  # pilot default
        self.assertEqual(vertical_for_industry("nonsense"), "hvac")

    def test_detectors_filter_by_vertical(self):
        hvac_detectors = detectors_for("hvac")
        self.assertTrue(all(d.vertical == "hvac" for d in hvac_detectors))
        self.assertGreater(len(hvac_detectors), 0)
        dental_detectors = detectors_for("dental")
        self.assertEqual(len(dental_detectors), 0)  # no dental detectors yet

    def test_dollars_at_stake_aggregates(self):
        brief = run_leak_scan(_db(), ORG)
        self.assertGreaterEqual(brief["totals"]["dollars_at_stake"], 12000.0)

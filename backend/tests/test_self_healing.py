"""Tests for the TW-208 self-healing pipeline standard (Zeke Okafor).

One error never crushes the full process. Covers the shared primitives,
CSV ingestion, the leak engine, and benchmark recording:

(a) a poison row/record fails alone, never its batch;
(b) transient failures retry with backoff;
(c) poison items land in a dead-letter capture;
(d) partial completion reports exactly what was skipped.
"""
import asyncio
import unittest
from datetime import datetime, timezone

from app import self_healing as sh
from app.leak_engine import engine as leak_engine
from app.leak_engine.registry import Detector
from app.services import benchmarks as bm
from app.services import manual_import


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, _cols):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def order(self, _col, desc=False):
        return self

    def insert(self, rows):
        self._rows.extend(rows if isinstance(rows, list) else [rows])
        return self

    def execute(self):
        return FakeResult(self._rows)


class FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self._tables[name])


class FakeUploadFile:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        return self._data if size < 0 else self._data[:size]


class FlakyInsertDB(FakeDB):
    """Bulk inserts fail with a transient error `failures` times, then work."""

    def __init__(self, tables, failures=1):
        super().__init__(tables)
        self.failures = failures
        self.inserted = []
        self.attempts = 0

    def table(self, name):
        db = self

        class Q(FakeQuery):
            def insert(self, rows):
                db.attempts += 1
                if db.attempts <= db.failures:
                    raise ConnectionError("connection reset by peer")
                db.inserted.extend(rows if isinstance(rows, list) else [rows])
                return self

            def execute(self):
                return FakeResult([])

        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return Q(self._tables[name])


class PoisonRowDB(FakeDB):
    """Bulk insert explodes when the batch contains the poison row; single-row
    inserts fail only for the poison row itself (permanent DB-level error)."""

    def __init__(self, tables):
        super().__init__(tables)
        self.inserted = []

    def table(self, name):
        db = self

        class Q(FakeQuery):
            def insert(self, rows):
                rows = rows if isinstance(rows, list) else [rows]
                if len(rows) > 1 and any(r.get("name") == "POISON" for r in rows):
                    raise RuntimeError("bulk insert hit poison row")
                for r in rows:
                    if r.get("name") == "POISON":
                        raise ValueError('null value in column "name" violates not-null')
                    db.inserted.append(r)
                return self

            def execute(self):
                return FakeResult([])

        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return Q(self._tables[name])


class DeadDB:
    """Every DB call fails transiently."""

    def table(self, _name):
        raise TimeoutError("connection timed out")


class LegacyLogDB(FakeDB):
    """csv_import_logs exists but predates the TW-208 details columns."""

    def __init__(self):
        super().__init__({"csv_import_logs": []})
        self.legacy_rows = []

    def table(self, name):
        db = self

        class Q(FakeQuery):
            def insert(self, row):
                if "details" in row or "skipped_rows" in row:
                    raise Exception('column "details" does not exist')
                db.legacy_rows.append(row)
                return self

            def execute(self):
                return FakeResult([])

        return Q(self._tables[name])


ORG = "org-208"

# Grandma's rows: the canonical good import batch every poison test protects.
GRANDMA_ROWS = "name,email\nAdaeze,ada@example.com\nChidi,chidi@example.com\n"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class RetryTest(unittest.TestCase):
    def test_success_first_try_no_sleep(self):
        sleeps = []
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        self.assertEqual(sh.retry_with_backoff(fn, sleep=sleeps.append), "ok")
        self.assertEqual(calls, [1])
        self.assertEqual(sleeps, [])

    def test_transient_failures_retry_with_growing_backoff(self):
        sleeps = []
        attempts = []

        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("connection reset by peer")
            return "recovered"

        result = sh.retry_with_backoff(fn, attempts=3, base_delay=0.5, sleep=sleeps.append)
        self.assertEqual(result, "recovered")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertGreaterEqual(sleeps[0], 0.5 * 0.5)
        self.assertLessEqual(sleeps[0], 0.5 * 1.5)
        self.assertGreaterEqual(sleeps[1], 1.0 * 0.5)
        self.assertLessEqual(sleeps[1], 1.0 * 1.5)

    def test_gives_up_after_max_attempts(self):
        sleeps = []

        def fn():
            raise TimeoutError("connection timed out")

        with self.assertRaises(TimeoutError):
            sh.retry_with_backoff(fn, attempts=3, base_delay=0.01, sleep=sleeps.append)
        self.assertEqual(len(sleeps), 2)

    def test_permanent_errors_never_retry(self):
        sleeps = []
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("bad input, will never heal")

        with self.assertRaises(ValueError):
            sh.retry_with_backoff(fn, attempts=3, base_delay=0.01, sleep=sleeps.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_is_transient_classification(self):
        self.assertTrue(sh.is_transient(ConnectionError("reset")))
        self.assertTrue(sh.is_transient(TimeoutError("timed out")))
        self.assertTrue(sh.is_transient(RuntimeError("503 service unavailable")))
        self.assertFalse(sh.is_transient(ValueError("bad value")))
        self.assertFalse(sh.is_transient(KeyError("missing")))


class IsolatedBatchTest(unittest.TestCase):
    def test_poison_item_skipped_rest_complete(self):
        def fn(item):
            if item == "POISON":
                raise ValueError("rotten")
            return item.upper()

        results, skipped = sh.run_isolated(["a", "POISON", "b"], fn)
        self.assertEqual(results, ["A", "B"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].index, 1)
        self.assertIn("rotten", skipped[0].error)

    def test_all_good_means_no_skips(self):
        results, skipped = sh.run_isolated([1, 2], lambda x: x * 2)
        self.assertEqual(results, [2, 4])
        self.assertEqual(skipped, [])


class DeadLetterTest(unittest.TestCase):
    def test_dlq_collects_and_summarizes(self):
        dlq = sh.DeadLetterQueue("csv-import")
        dlq.collect(3, {"name": ""}, ValueError("missing name"))
        dlq.collect(7, {"name": "x"}, ValueError("bad amount"))
        summary = dlq.summarize()
        self.assertEqual(summary["pipeline"], "csv-import")
        self.assertEqual(summary["skipped"], 2)
        self.assertEqual(len(summary["sample"]), 2)
        self.assertEqual(summary["sample"][0]["index"], 3)


# ---------------------------------------------------------------------------
# CSV ingestion
# ---------------------------------------------------------------------------

class ImportIsolationTest(unittest.TestCase):
    def test_poison_rows_skipped_good_rows_imported(self):
        csv_data = (
            "name,email,estimated_value\n"
            "Adaeze,ada@example.com,100\n"
            ",noname@example.com,50\n"          # missing name -> skip
            "Chidi,chidi@example.com,notanumber\n"  # bad numeric -> skip
            "Ngozi,ngozi@example.com,200\n"
        )
        db = FakeDB({"leads": [], "csv_import_logs": []})
        result = run(manual_import.import_csv_rows(
            db, ORG, "leads", FakeUploadFile(csv_data.encode()), filename="test.csv"))
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["row_count"], 4)
        self.assertEqual(result["skipped_rows"], 2)
        self.assertEqual(len(db._tables["leads"]), 2)
        reasons = " ".join(s["reason"] for s in result["skipped"])
        self.assertIn("name", reasons)

    def test_transient_chunk_failure_retries_then_imports_all(self):
        db = FlakyInsertDB({"leads": [], "csv_import_logs": []}, failures=1)
        result = run(manual_import.import_csv_rows(
            db, ORG, "leads", FakeUploadFile(GRANDMA_ROWS.encode())))
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped_rows"], 0)
        self.assertEqual(len(db.inserted), 2)
        self.assertGreaterEqual(db.attempts, 2)

    def test_db_level_poison_row_isolated_by_per_row_fallback(self):
        csv_data = "name,email\nAdaeze,ada@example.com\nPOISON,p@p.com\nChidi,chidi@example.com\n"
        db = PoisonRowDB({"leads": [], "csv_import_logs": []})
        result = run(manual_import.import_csv_rows(
            db, ORG, "leads", FakeUploadFile(csv_data.encode())))
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped_rows"], 1)
        self.assertEqual(result["skipped"][0]["row"], 2)
        names = [r["name"] for r in db.inserted]
        self.assertEqual(names, ["Adaeze", "Chidi"])

    def test_import_log_records_skipped_details(self):
        csv_data = "name,email\nAdaeze,ada@example.com\n,bad@example.com\n"
        db = FakeDB({"leads": [], "csv_import_logs": []})
        run(manual_import.import_csv_rows(db, ORG, "leads", FakeUploadFile(csv_data.encode())))
        logs = db._tables["csv_import_logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["skipped_rows"], 1)
        self.assertEqual(logs[0]["status"], "success")
        self.assertTrue(logs[0]["details"]["skipped"])

    def test_import_log_falls_back_when_details_columns_missing(self):
        csv_data = GRANDMA_ROWS
        db = LegacyLogDB()
        # leads table missing here is fine: LegacyLogDB only serves the log
        # table; use a full FakeDB subclass instead for the import itself.
        full = FakeDB({"leads": []})
        logged = {}

        orig_table = full.table

        def table(name):
            if name == "csv_import_logs":
                return LegacyLogDB().table(name)
            return orig_table(name)

        full.table = table
        result = run(manual_import.import_csv_rows(
            full, ORG, "leads", FakeUploadFile(csv_data.encode())))
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped_rows"], 0)

    def test_whole_file_problems_still_reject(self):
        from fastapi import HTTPException
        db = FakeDB({"leads": [], "csv_import_logs": []})
        with self.assertRaises(HTTPException):
            run(manual_import.import_csv_rows(
                db, ORG, "leads", FakeUploadFile(b"name\n")))


# ---------------------------------------------------------------------------
# Leak engine
# ---------------------------------------------------------------------------

def _engine_db():
    return FakeDB({"organizations": [{"id": ORG, "industry": "hvac"}]})


class EngineResilienceTest(unittest.TestCase):
    def setUp(self):
        self._orig = leak_engine.detectors_for

    def tearDown(self):
        leak_engine.detectors_for = self._orig

    def test_transient_detector_failure_retries_then_succeeds(self):
        calls = []

        def flaky(db, org_id):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("connection reset by peer")
            return [{"ladder": "happened", "severity": "info", "title": "t",
                     "estimated_value": 10.0}]

        leak_engine.detectors_for = lambda v: [Detector(name="flaky", vertical="hvac",
                                                         requires=[], run=flaky)]
        brief = leak_engine.run_leak_scan(_engine_db(), ORG)
        self.assertEqual(len(calls), 3)
        self.assertEqual(brief["totals"]["findings"], 1)

    def test_permanent_detector_failure_degrades_with_partial_flag(self):
        def broken(db, org_id):
            raise ValueError("programmer error, will never heal")

        leak_engine.detectors_for = lambda v: [Detector(name="broken", vertical="hvac",
                                                         requires=[], run=broken)]
        brief = leak_engine.run_leak_scan(_engine_db(), ORG)
        self.assertIn("broken", brief["data_status"]["errors"])
        self.assertTrue(brief["data_status"]["partial"])
        self.assertEqual(brief["totals"]["findings"], 0)

    def test_hostile_estimated_value_cannot_kill_brief_totals(self):
        def hostile(db, org_id):
            return [{"ladder": "happened", "severity": "urgent", "title": "t",
                     "estimated_value": "N/A"},
                    {"ladder": "should", "severity": "watch", "title": "t2",
                     "estimated_value": None}]

        leak_engine.detectors_for = lambda v: [Detector(name="hostile", vertical="hvac",
                                                         requires=[], run=hostile)]
        brief = leak_engine.run_leak_scan(_engine_db(), ORG)
        self.assertEqual(brief["totals"]["findings"], 2)
        self.assertEqual(brief["totals"]["dollars_at_stake"], 0.0)

    def test_clean_scan_reports_not_partial(self):
        def fine(db, org_id):
            return [{"ladder": "happened", "severity": "info", "title": "t",
                     "estimated_value": 5.0}]

        leak_engine.detectors_for = lambda v: [Detector(name="fine", vertical="hvac",
                                                         requires=[], run=fine)]
        brief = leak_engine.run_leak_scan(_engine_db(), ORG)
        self.assertFalse(brief["data_status"].get("partial", False))


# ---------------------------------------------------------------------------
# Benchmark recording
# ---------------------------------------------------------------------------

class BenchmarkResilienceTest(unittest.TestCase):
    def test_transient_db_failure_retries(self):
        db = FlakyInsertDB(
            {"organizations": [{"id": ORG, "benchmark_consent": True}]}, failures=2)
        result = bm.record_org_metrics(db, ORG, "hvac", {"quote_close_rate": 24.0})
        self.assertEqual(result["recorded"], 1)

    def test_persistent_db_failure_degrades_without_raising(self):
        db = DeadDB()
        # consent lookup itself fails transiently -> treated as no_consent path
        # must not raise; use a consenting org via a flaky consent read.
        class ConsentDeadDB(DeadDB):
            def table(self, name):
                if name == "organizations":
                    return FakeQuery([{"id": ORG, "benchmark_consent": True}])
                return super().table(name)

        result = bm.record_org_metrics(ConsentDeadDB(), ORG, "hvac", {"quote_close_rate": 24.0})
        self.assertEqual(result["recorded"], 0)
        self.assertEqual(result["reason"], "db_error")


if __name__ == "__main__":
    unittest.main()

# Prepared by Twistor Holdings LLC
"""TW-840 speed standard: RED-first tests for the columnar data standard.

Contract under test (ticket TW-840):
  1. Partition layout: Parquet + ZSTD, hive partitions device=/date=.
  2. DuckDB embedded OLAP with a HARD memory cap and out-of-core spill.
  3. Incremental builds: silver/gold rebuilt per new partition only.
  4. Gold device-day aggregates materialized at build time.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.data_layer.speedstd import corpus, engine, layout, transform

DEVICES = ["Ames, IA", "Greeley, CO"]


def _write_bronze(root: Path, days: int = 3, readings: int = 4) -> Path:
    bronze = root / "bronze"
    start = date(2026, 1, 1)
    for device in DEVICES:
        for d in range(days):
            day = start + timedelta(days=d)
            rows = corpus.make_rows(device, day, readings, seed=840)
            corpus.write_bronze_partition(bronze, "climateshift-1", device, day, rows)
    return bronze


# ---------------------------------------------------------------- layout


def test_partition_path_roundtrip():
    p = layout.partition_dir(
        Path("/data"), "climateshift-1", "Ames, IA", date(2026, 1, 5)
    )
    assert p.as_posix().endswith("climateshift-1/device=Ames, IA/date=2026-01-05")
    parsed = layout.parse_partition_dir(p)
    assert parsed == ("climateshift-1", "Ames, IA", date(2026, 1, 5))


def test_partition_file_is_parquet_zstd_named():
    f = layout.partition_file(Path("/data"), "ds", "dev1", date(2026, 2, 1))
    assert f.name == "data.parquet"


# ---------------------------------------------------------------- engine


def test_engine_applies_hard_memory_cap():
    con = engine.connect(memory_limit_mb=64)
    try:
        (value,) = con.execute("SELECT current_setting('memory_limit')").fetchone()
    finally:
        con.close()
    # DuckDB reports the cap back in bytes-ish units; the configured cap
    # must be the one in force, not the machine default.
    assert value.lower().startswith("61") or "64" in value or value.endswith("MiB")


def test_engine_queries_partitioned_parquet_with_pruning(tmp_path):
    bronze = _write_bronze(tmp_path)
    silver = tmp_path / "silver"
    transform.build(bronze, silver, tmp_path / "gold", tmp_path / "state.json")
    con = engine.connect(memory_limit_mb=64)
    try:
        rows = engine.aggregate_history(con, silver, "climateshift-1")
    finally:
        con.close()
    # 2 devices x 1 year bucket (2026) in the tiny corpus
    assert {(r[0], r[1]) for r in rows} == {("Ames, IA", 2026), ("Greeley, CO", 2026)}
    assert all(r[2] == 12 for r in rows)  # 3 days x 4 readings


def test_engine_spill_is_workspace_bounded_never_tmp():
    """Coordination fix (Marcus, TW-837 smoke): DuckDB spill must live in
    a bounded directory under the workspace, never unbounded /tmp, which
    is a small shared tmpfs other lanes depend on."""
    con = engine.connect()
    try:
        (temp_dir,) = con.execute("SELECT current_setting('temp_directory')").fetchone()
        (max_spill,) = con.execute(
            "SELECT current_setting('max_temp_directory_size')"
        ).fetchone()
    finally:
        con.close()
    assert temp_dir.startswith(str(engine.DEFAULT_SPILL_DIR))
    assert not temp_dir.startswith("/tmp")
    assert max_spill not in ("", "-1", "0 bytes")


# ---------------------------------------------------------------- corpus


def test_make_rows_is_deterministic_and_schema_shaped():
    a = corpus.make_rows("Ames, IA", date(2026, 1, 1), 4, seed=1)
    b = corpus.make_rows("Ames, IA", date(2026, 1, 1), 4, seed=1)
    assert a == b
    row = a[0]
    for key in (
        "device",
        "date",
        "ts",
        "genotype",
        "crop",
        "yield_t_ha",
        "temp_anomaly_c",
        "precip_mm",
        "gdd",
        "heat_days",
    ):
        assert key in row


# ---------------------------------------------------------------- build


def test_build_writes_silver_partitions_and_gold_device_day(tmp_path):
    bronze = _write_bronze(tmp_path)
    silver, gold = tmp_path / "silver", tmp_path / "gold"
    report = transform.build(bronze, silver, gold, tmp_path / "state.json")
    assert report.partitions_rebuilt == 6  # 2 devices x 3 days
    assert report.partitions_skipped == 0
    assert report.rows == 24
    parts = layout.list_partitions(silver, "climateshift-1")
    assert len(parts) == 6
    assert all((p / "data.parquet").exists() for p in parts)
    # gold: one aggregate row per device-day
    con = engine.connect(memory_limit_mb=64)
    try:
        (n,) = con.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=true)",
            [str(gold / "climateshift-1" / "**" / "*.parquet")],
        ).fetchone()
    finally:
        con.close()
    assert n == 6


def test_incremental_build_rebuilds_only_new_partitions(tmp_path):
    bronze = _write_bronze(tmp_path)
    silver, gold, state = (
        tmp_path / "silver",
        tmp_path / "gold",
        tmp_path / "state.json",
    )
    transform.build(bronze, silver, gold, state)
    before = {
        p: (p / "data.parquet").stat().st_mtime_ns
        for p in layout.list_partitions(silver, "climateshift-1")
    }
    # one new day arrives for one device (the poller append pattern)
    day = date(2026, 1, 4)
    rows = corpus.make_rows("Ames, IA", day, 4, seed=99)
    corpus.write_bronze_partition(bronze, "climateshift-1", "Ames, IA", day, rows)
    report = transform.build(bronze, silver, gold, state)
    assert report.partitions_rebuilt == 1
    assert report.partitions_skipped == 6
    for p, mtime in before.items():
        assert (p / "data.parquet").stat().st_mtime_ns == mtime
    manifest = json.loads(state.read_text())
    assert len(manifest["partitions"]) == 7


def test_gold_history_matches_silver_history(tmp_path):
    """The gold serving path must return the same full-history aggregate
    as the silver scan (weighted recomposition over device-day rollups)."""
    bronze = _write_bronze(tmp_path)
    silver, gold = tmp_path / "silver", tmp_path / "gold"
    transform.build(bronze, silver, gold, tmp_path / "state.json")
    con = engine.connect(memory_limit_mb=64)
    try:
        silver_rows = engine.aggregate_history(con, silver, "climateshift-1")
        gold_rows = engine.aggregate_gold_history(con, gold, "climateshift-1")
    finally:
        con.close()
    assert len(silver_rows) == len(gold_rows)
    for s, g in zip(silver_rows, gold_rows):
        assert s[0] == g[0] and s[1] == g[1] and s[2] == g[2]
        assert s[3] == pytest.approx(g[3], abs=1e-9)
        assert s[4] == pytest.approx(g[4], abs=1e-9)


def test_build_isolates_a_failing_partition_and_resumes(tmp_path, monkeypatch):
    """TW-208 self-healing: one bad partition never crushes the batch.

    A write failure on one partition is recorded, the rest of the build
    completes, the manifest holds the successes, and the next build
    retries exactly the failed partition.
    """
    bronze = _write_bronze(tmp_path)
    silver, gold, state = (
        tmp_path / "silver",
        tmp_path / "gold",
        tmp_path / "state.json",
    )
    real_write = transform._atomic_write_parquet
    calls = {"n": 0}

    def flaky(df, target):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("simulated transient IO error")
        return real_write(df, target)

    monkeypatch.setattr(transform, "_atomic_write_parquet", flaky)
    report = transform.build(bronze, silver, gold, state)
    assert report.partitions_failed == 1
    assert report.partitions_rebuilt == 5
    assert len(json.loads(state.read_text())["partitions"]) == 5

    monkeypatch.setattr(transform, "_atomic_write_parquet", real_write)
    report2 = transform.build(bronze, silver, gold, state)
    assert report2.partitions_failed == 0
    assert report2.partitions_rebuilt == 1
    assert report2.partitions_skipped == 5


def test_gold_sync_heals_a_missing_gold_year(tmp_path):
    """Gold is a cache over silver and must self-heal: if a build died
    after the manifest landed but before a gold year file was merged,
    the next build restores gold from silver WITHOUT rebuilding silver.
    """
    bronze = _write_bronze(tmp_path)
    silver, gold, state = (
        tmp_path / "silver",
        tmp_path / "gold",
        tmp_path / "state.json",
    )
    transform.build(bronze, silver, gold, state)
    victim = gold / "climateshift-1" / "device=Ames, IA" / "year=2026" / "data.parquet"
    assert victim.exists()
    victim.unlink()
    report = transform.build(bronze, silver, gold, state)
    assert report.partitions_rebuilt == 0
    assert report.gold_years_synced == 1
    assert victim.exists()
    con = engine.connect(memory_limit_mb=64)
    try:
        rows = engine.aggregate_gold_history(con, gold, "climateshift-1")
    finally:
        con.close()
    assert {(r[0], r[2]) for r in rows} == {("Ames, IA", 12), ("Greeley, CO", 12)}


def test_rebuild_with_no_changes_is_a_noop(tmp_path):
    bronze = _write_bronze(tmp_path)
    args = (bronze, tmp_path / "silver", tmp_path / "gold", tmp_path / "state.json")
    transform.build(*args)
    report = transform.build(*args)
    assert report.partitions_rebuilt == 0
    assert report.partitions_skipped == 6


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

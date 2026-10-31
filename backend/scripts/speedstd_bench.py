#!/usr/bin/env python3
# Prepared by Twistor Holdings LLC
"""TW-840 benchmark harness CLI: before/after proof for the speed standard.

    speedstd_bench.py gen   --out DIR --days 1095 --readings 48
    speedstd_bench.py build --corpus DIR
    speedstd_bench.py bench --corpus DIR --reps 3 --out results.json
    speedstd_bench.py worker ...        (internal: one pattern, one query)

Patterns benchmarked:
  baseline       the current access pattern: json.load the monolithic
                 JSON array, aggregate in Python dicts. No cap exists.
  standard       Parquet + ZSTD partitions, DuckDB at the standard's
                 hard cap (256MB), partition pruning by construction.
  standard-tight same, at a 64MB cap: proof the cap holds out-of-core
                 (spill to disk) instead of lagging the machine.

Every worker is a fresh process so peak RSS is measured clean per cell.
The orchestrator verifies result parity between baseline and standard
before believing any timing.
"""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_layer.speedstd import corpus, engine, transform  # noqa: E402

DATASET = "climateshift-1"
QUERIES = ("q1_history", "q1b_gold_history", "q2_pruned", "q3_gold_serving")
PATTERNS = ("baseline", "standard", "standard-tight")


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------- workers
def _baseline_query(corpus_dir: Path, query: str) -> dict:
    # The clock starts before the load: parsing the whole monolith into
    # Python objects IS the current pattern's cost, every single query.
    t0 = time.perf_counter()
    rows = json.loads((corpus_dir / f"{DATASET}.json").read_text())
    if query in ("q1_history", "q1b_gold_history"):
        agg: dict = {}
        for r in rows:
            key = (r["device"], int(r["date"][:4]))
            n, sy, sa = agg.get(key, (0, 0.0, 0.0))
            agg[key] = (n + 1, sy + r["yield_t_ha"], sa + r["temp_anomaly_c"])
        total_n = sum(v[0] for v in agg.values())
        total_yield = sum(v[1] for v in agg.values())
        groups = len(agg)
    elif query == "q2_pruned":
        total_n, total_yield, groups = 0, 0.0, 0
        for r in rows:
            if r["device"] == "Ames, IA" and "2019-01-01" <= r["date"] <= "2019-12-31":
                total_n += 1
                total_yield += r["yield_t_ha"]
        groups = 1 if total_n else 0
    else:  # q3: trailing-30-day device-day means, computed from raw rows
        last = max(r["date"] for r in rows)
        first = (date.fromisoformat(last) - timedelta(days=30)).isoformat()
        per_day: dict = {}
        for r in rows:
            if r["device"] == "Ames, IA" and first <= r["date"] <= last:
                n, sy = per_day.get(r["date"], (0, 0.0))
                per_day[r["date"]] = (n + 1, sy + r["yield_t_ha"])
        total_n = sum(v[0] for v in per_day.values())
        total_yield = sum(v[1] for v in per_day.values())
        groups = len(per_day)
    return {
        "seconds": time.perf_counter() - t0,
        "peak_rss_mb": _rss_mb(),
        "groups": groups,
        "total_n": total_n,
        "total_yield": round(total_yield, 2),
    }


def _standard_query(corpus_dir: Path, query: str, cap_mb: int) -> dict:
    silver = corpus_dir / "silver"
    gold = corpus_dir / "gold"
    con = engine.connect(memory_limit_mb=cap_mb)
    try:
        t0 = time.perf_counter()
        if query == "q1_history":
            rows = engine.aggregate_history(con, silver, DATASET)
            total_n = sum(r[2] for r in rows)
            total_yield = round(sum(r[2] * r[3] for r in rows), 2)
            groups = len(rows)
        elif query == "q1b_gold_history":
            rows = engine.aggregate_gold_history(con, gold, DATASET)
            total_n = sum(r[2] for r in rows)
            total_yield = round(sum(r[2] * r[3] for r in rows), 2)
            groups = len(rows)
        elif query == "q2_pruned":
            rows = engine.aggregate_pruned(
                con, silver, DATASET, "Ames, IA", "2019-01-01", "2019-12-31"
            )
            total_n = rows[0][1] if rows else 0
            total_yield = round(rows[0][1] * rows[0][2], 2) if rows else 0.0
            groups = len(rows)
        else:
            glob = str(gold / DATASET / "device=Ames, IA" / "**" / "*.parquet")
            rows = con.execute(
                """
                SELECT date, n_readings, avg_yield_t_ha
                FROM read_parquet(?, hive_partitioning = true)
                WHERE CAST(date AS DATE) >=
                      (SELECT max(CAST(date AS DATE))
                       FROM read_parquet(?, hive_partitioning = true)) - INTERVAL 30 DAY
                """,
                [glob, glob],
            ).fetchall()
            total_n = sum(r[1] for r in rows)
            total_yield = round(sum(r[1] * r[2] for r in rows), 2)
            groups = len(rows)
        seconds = time.perf_counter() - t0
    finally:
        con.close()
    return {
        "seconds": seconds,
        "peak_rss_mb": _rss_mb(),
        "groups": groups,
        "total_n": total_n,
        "total_yield": total_yield,
    }


def cmd_worker(args: argparse.Namespace) -> int:
    corpus_dir = Path(args.corpus)
    if args.pattern == "baseline":
        result = _baseline_query(corpus_dir, args.query)
    else:
        cap = 256 if args.pattern == "standard" else 64
        result = _standard_query(corpus_dir, args.query, cap)
    result["pattern"] = args.pattern
    result["query"] = args.query
    print(json.dumps(result))
    return 0


# ---------------------------------------------------------------- commands
def cmd_gen(args: argparse.Namespace) -> int:
    stats = corpus.generate_scale_corpus(
        Path(args.out),
        days=args.days,
        readings_per_day=args.readings,
    )
    print(json.dumps(stats, indent=1))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    corpus_dir = Path(args.corpus)
    report = transform.build(
        corpus_dir / "bronze",
        corpus_dir / "silver",
        corpus_dir / "gold",
        corpus_dir / "build-state.json",
    )
    print(
        json.dumps(
            {
                "partitions_rebuilt": report.partitions_rebuilt,
                "partitions_skipped": report.partitions_skipped,
                "partitions_failed": report.partitions_failed,
                "gold_years_synced": report.gold_years_synced,
                "errors": report.errors[:5],
                "rows": report.rows,
                "silver_bytes": sum(
                    p.stat().st_size for p in (corpus_dir / "silver").rglob("*.parquet")
                ),
                "gold_bytes": sum(
                    p.stat().st_size for p in (corpus_dir / "gold").rglob("*.parquet")
                ),
            },
            indent=1,
        )
    )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    corpus_dir = Path(args.corpus)
    cells = []
    for pattern in PATTERNS:
        for query in QUERIES:
            runs = []
            for _ in range(args.reps):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "worker",
                        "--corpus",
                        str(corpus_dir),
                        "--pattern",
                        pattern,
                        "--query",
                        query,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                runs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
            cells.append(
                {
                    "pattern": pattern,
                    "query": query,
                    "seconds_median": round(
                        statistics.median(r["seconds"] for r in runs), 4
                    ),
                    "peak_rss_mb_median": round(
                        statistics.median(r["peak_rss_mb"] for r in runs), 1
                    ),
                    "groups": runs[0]["groups"],
                    "total_n": runs[0]["total_n"],
                    "total_yield": runs[0]["total_yield"],
                }
            )
            print(
                f"{pattern:15s} {query:16s} "
                f"{cells[-1]['seconds_median']:>9.3f}s  rss {cells[-1]['peak_rss_mb_median']:>7.1f}MB"
            )
    # parity: baseline vs standard must agree on totals per query
    parity = {}
    for query in QUERIES:
        base = next(
            c for c in cells if c["pattern"] == "baseline" and c["query"] == query
        )
        std = next(
            c for c in cells if c["pattern"] == "standard" and c["query"] == query
        )
        parity[query] = base["total_n"] == std["total_n"] and abs(
            base["total_yield"] - std["total_yield"]
        ) < 0.05 * max(1, base["total_n"])
    payload = {"corpus": str(corpus_dir), "cells": cells, "parity": parity}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=1))
    print(json.dumps({"parity": parity}))
    return 0 if all(parity.values()) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gen")
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--readings", type=int, default=16)
    p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("build")
    p.add_argument("--corpus", required=True)
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("bench")
    p.add_argument("--corpus", required=True)
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("worker")
    p.add_argument("--corpus", required=True)
    p.add_argument("--pattern", choices=PATTERNS, required=True)
    p.add_argument("--query", choices=QUERIES, required=True)
    p.set_defaults(fn=cmd_worker)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

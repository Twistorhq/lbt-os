#!/usr/bin/env python3
# Prepared by Twistor Holdings LLC
"""TW-840 query CLI: ad-hoc SQL over a speed-standard corpus.

    speedstd_query.py --root DIR --dataset climateshift-1 \
        --sql "SELECT device, count(*) FROM measurements GROUP BY 1" \
        --memory-mb 256

The dataset is exposed as the `measurements` view over its hive
partitions (device=/date=). The DuckDB connection always carries the
standard's hard memory cap, so a runaway query spills out-of-core
instead of lagging the machine. Output: one JSON object per row.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_layer.speedstd import engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="silver (or gold) dataset root")
    ap.add_argument("--dataset", default="climateshift-1")
    ap.add_argument("--sql", required=True)
    ap.add_argument("--memory-mb", type=int, default=engine.DEFAULT_MEMORY_LIMIT_MB)
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()

    con = engine.connect(memory_limit_mb=args.memory_mb, threads=args.threads)
    try:
        engine.register_dataset(con, Path(args.root), args.dataset, "measurements")
        cur = con.execute(args.sql)
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print(
                json.dumps(
                    {
                        c: (str(v) if not isinstance(v, (int, float, str)) else v)
                        for c, v in zip(cols, row)
                    }
                )
            )
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

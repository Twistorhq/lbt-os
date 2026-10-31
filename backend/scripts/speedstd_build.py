#!/usr/bin/env python3
# Prepared by Twistor Holdings LLC
"""TW-840 build CLI: incremental bronze -> silver -> gold.

    speedstd_build.py --bronze DIR --silver DIR --gold DIR --state FILE

The shared build step every poller (TW-838 quantum, TW-839 lanes)
calls after appending new bronze partitions. Only partitions whose
bronze content hash changed are rebuilt; gold device-day aggregates
are materialized for exactly those partitions. Batch only, no daemon.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_layer.speedstd import transform  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bronze", required=True)
    ap.add_argument("--silver", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--state", required=True, help="manifest JSON path")
    args = ap.parse_args()

    report = transform.build(
        Path(args.bronze), Path(args.silver), Path(args.gold), Path(args.state)
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
            }
        )
    )
    return 1 if report.partitions_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

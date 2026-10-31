# Prepared by Twistor Holdings LLC
"""TW-840 speed standard: the DuckDB query engine.

The no-lag guarantee is a configuration, not a hope: every connection
this factory returns carries a HARD memory cap and a temp directory, so
DuckDB spills out-of-core instead of swapping the machine to death.
Analytic scans never touch Postgres (transactional metadata only) and
never load a corpus into Python: vectorized execution reads Parquet
columns, pruned by hive partitions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from . import layout

DEFAULT_MEMORY_LIMIT_MB = 256

# Spill lives under the workspace, bounded, NEVER in /tmp. /tmp on the
# shared sandbox is a small tmpfs other lanes depend on; an unbounded
# spill there filled it during a TW-837 smoke and killed a clip job
# with Errno 28 while the workspace disk had 97G free. The cap is a
# stated number, enforced by DuckDB itself (max_temp_directory_size):
# past it, the query errors instead of eating a shared filesystem.
DEFAULT_SPILL_DIR = Path.home() / "workspace" / ".speedstd-spill"
DEFAULT_MAX_SPILL = "1GB"


def connect(
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    threads: int = 2,
    temp_dir: str | None = None,
    max_spill: str = DEFAULT_MAX_SPILL,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection under the standard's caps.

    memory_limit_mb is a hard cap: past it, DuckDB spills to temp_dir
    (out-of-core) rather than growing RSS. threads stays low on purpose:
    batch jobs run single-tenant, the laptop stays responsive. Spill is
    bounded twice: a workspace path (never /tmp) and max_spill as a
    hard size cap on the spill directory.
    """
    spill = Path(temp_dir) if temp_dir else DEFAULT_SPILL_DIR
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit = '{memory_limit_mb}MB'")
    con.execute(f"SET threads = {int(threads)}")
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{max_spill}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def register_dataset(
    con: duckdb.DuckDBPyConnection, root: Path, dataset: str, view: str
) -> None:
    """Expose a partitioned dataset as a view with hive partition columns."""
    glob = layout.dataset_glob(root, dataset)
    con.execute(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM read_parquet('{glob}', hive_partitioning = true)"
    )


def aggregate_history(
    con: duckdb.DuckDBPyConnection, root: Path, dataset: str
) -> list[tuple[Any, ...]]:
    """The acceptance query: full-history aggregate per device and year.

    Over the standard this reads only the columns it aggregates, pruned
    by partition; the JSON baseline must parse every byte of every row.
    """
    register_dataset(con, root, dataset, "measurements")
    return con.execute(
        """
        SELECT device,
               CAST(strftime(CAST(date AS DATE), '%Y') AS INTEGER) AS year,
               count(*) AS n,
               avg(yield_t_ha) AS avg_yield,
               avg(temp_anomaly_c) AS avg_anomaly
        FROM measurements
        GROUP BY device, year
        ORDER BY device, year
        """
    ).fetchall()


def aggregate_pruned(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    dataset: str,
    device: str,
    first: str,
    last: str,
) -> list[tuple[Any, ...]]:
    """Partition-pruned aggregate: one device, one date window.

    The scan glob is narrowed to the device's partition subtree, so
    other devices' files are never listed or opened; the date filter
    then prunes inside that subtree.
    """
    glob = layout.device_glob(root, dataset, device)
    return con.execute(
        """
        SELECT device, count(*) AS n, avg(yield_t_ha) AS avg_yield
        FROM read_parquet(?, hive_partitioning = true)
        WHERE CAST(date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        GROUP BY device
        """,
        [glob, first, last],
    ).fetchall()


def aggregate_gold_history(
    con: duckdb.DuckDBPyConnection, gold_root: Path, dataset: str
) -> list[tuple[Any, ...]]:
    """Full-history aggregate served from gold device-day rollups.

    This is the standard's millisecond path: gold rows are materialized
    at build time (one per device-day), so the full-history answer is a
    weighted recomposition over thousands of summary rows, never a
    scan over millions of raw rows. Weighted by n_readings, the result
    is the same aggregate aggregate_history returns over silver.
    """
    register_dataset(con, gold_root, dataset, "gold")
    return con.execute(
        """
        WITH g AS (
            SELECT device,
                   CAST(strftime(CAST(date AS DATE), '%Y') AS INTEGER) AS yr,
                   n_readings, avg_yield_t_ha, avg_temp_anomaly_c
            FROM gold
        )
        SELECT device, yr AS year,
               sum(n_readings) AS n,
               sum(n_readings * avg_yield_t_ha) / sum(n_readings) AS avg_yield,
               sum(n_readings * avg_temp_anomaly_c) / sum(n_readings) AS avg_anomaly
        FROM g
        GROUP BY device, yr
        ORDER BY device, yr
        """
    ).fetchall()

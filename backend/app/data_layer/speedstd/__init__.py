# Prepared by Twistor Holdings LLC
"""TW-840 data platform speed standard.

Storage: Parquet + ZSTD, hive-partitioned by device/date.
Compute: DuckDB (hard memory cap, out-of-core) for analytics,
Polars lazy/streaming for transforms. Postgres stays transactional.
Builds: incremental per partition, gold device-day aggregates
materialized at build time. CLIs live in backend/scripts/speedstd_*.py.
"""

from . import corpus, engine, layout, transform

__all__ = ["corpus", "engine", "layout", "transform"]

# Prepared by Twistor Holdings LLC
"""TW-840 speed standard: the partition layout convention.

One layout for every queried corpus, so pollers (TW-838/839), the query
CLI, and the portal all speak the same paths:

    <root>/<dataset>/device=<device>/date=<YYYY-MM-DD>/data.parquet

Parquet + ZSTD inside, hive-style partition directories outside.
Partition pruning is structural: a query for one device and one day
opens exactly one file. Bronze JSON stays verbatim on disk elsewhere;
nothing in this layout is ever the only copy of raw data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

PARTITION_FILE = "data.parquet"


def partition_dir(root: Path, dataset: str, device: str, day: date) -> Path:
    return Path(root) / dataset / f"device={device}" / f"date={day.isoformat()}"


def partition_file(root: Path, dataset: str, device: str, day: date) -> Path:
    return partition_dir(root, dataset, device, day) / PARTITION_FILE


def parse_partition_dir(path: Path) -> tuple[str, str, date]:
    """Inverse of partition_dir: (dataset, device, day)."""
    date_part = path.name
    device_part = path.parent.name
    dataset = path.parent.parent.name
    if not date_part.startswith("date=") or not device_part.startswith("device="):
        raise ValueError(f"not a partition dir: {path}")
    return (
        dataset,
        device_part[len("device=") :],
        date.fromisoformat(date_part[len("date=") :]),
    )


def list_partitions(root: Path, dataset: str) -> list[Path]:
    """All partition dirs under a dataset root, sorted for determinism."""
    base = Path(root) / dataset
    if not base.exists():
        return []
    return sorted(p.parent for p in base.glob("device=*/date=*/" + PARTITION_FILE))


def dataset_glob(root: Path, dataset: str) -> str:
    """Recursive parquet glob for DuckDB read_parquet(hive_partitioning)."""
    return str(Path(root) / dataset / "**" / "*.parquet")


def device_glob(root: Path, dataset: str, device: str) -> str:
    """Glob narrowed to one device partition subtree.

    Pruning starts before the scan: a single-device query never lists
    or opens another device's files.
    """
    return str(Path(root) / dataset / f"device={device}" / "**" / "*.parquet")

# Prepared by Twistor Holdings LLC
"""TW-838 bronze store: verbatim vendor payloads, immutable, content-hashed.

The file form of the platform's bronze pattern (sci_bronze_artifacts):
payloads land as gzip-compressed JSON files on disk, partitioned by
vendor / date / device (the TW-840 partition discipline), with a JSONL
index whose rows mirror the sci_bronze_artifacts columns (lane,
storage_path, content_hash, collected_at, source_metadata). The
production sink swaps the index for the Supabase table behind this same
interface; the partition layout does not change.

Capture discipline (brief, Section 5):
  - Daily snapshot per device: an unchanged payload is stored once per
    UTC day, because the daily series is the asset.
  - Change-triggered recapture: a changed payload is stored immediately,
    whenever it is seen.
  - Same payload, same day: skipped. Nothing is ever overwritten;
    corrections are new records.

Raw values in bronze are INTERNAL ONLY at every price point (Angela
ruling, TW-838): bronze is never a served surface.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

LANE = "quantum"


@dataclass
class CaptureResult:
    artifact_key: str  # stable local id: sha256-derived, maps to the DB row
    content_hash: str
    path: str | None  # None when skipped
    stored: bool
    vendor: str
    device: str
    kind: str


def _canonical(payload: bytes) -> bytes:
    """Canonical bytes for hashing/storage: parsed JSON re-serialized with
    sorted keys when the payload is JSON, raw bytes otherwise. Two fetches
    of the same device state must hash identically."""
    try:
        return json.dumps(
            json.loads(payload), sort_keys=True, separators=(",", ":")
        ).encode()
    except (ValueError, UnicodeDecodeError):
        return payload


class FileBronzeStore:
    """Append-only bronze on local disk (production: the bronze bucket)."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.lane_dir = os.path.join(root, LANE)
        self.index_path = os.path.join(self.lane_dir, "_index.jsonl")
        os.makedirs(self.lane_dir, exist_ok=True)

    # -- index ---------------------------------------------------------------
    def iter_index(self) -> Iterator[dict[str, Any]]:
        if not os.path.exists(self.index_path):
            return
        with open(self.index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _append_index(self, row: dict[str, Any]) -> None:
        with open(self.index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def _latest(self, vendor: str, device: str, kind: str) -> dict | None:
        latest = None
        for row in self.iter_index():
            meta = row.get("source_metadata", {})
            if (
                meta.get("vendor") == vendor
                and meta.get("device") == device
                and meta.get("kind") == kind
            ):
                latest = row
        return latest

    # -- capture ---------------------------------------------------------------
    def capture(
        self,
        *,
        vendor: str,
        device: str,
        kind: str,
        payload: bytes,
        retrieved_at: datetime,
        source: str,
        endpoint: str,
        account_basis: str,
    ) -> CaptureResult:
        canonical = _canonical(payload)
        content_hash = hashlib.sha256(canonical).hexdigest()
        artifact_key = f"q-{content_hash[:24]}"
        day = retrieved_at.astimezone(timezone.utc).strftime("%Y-%m-%d")

        latest = self._latest(vendor, device, kind)
        if latest is not None:
            same_bytes = latest["content_hash"] == content_hash
            same_day = latest["collected_at"][:10] == day
            if same_bytes and same_day:
                return CaptureResult(
                    artifact_key=latest["artifact_key"],
                    content_hash=content_hash,
                    path=None,
                    stored=False,
                    vendor=vendor,
                    device=device,
                    kind=kind,
                )

        rel = os.path.join(
            LANE,
            f"vendor={vendor}",
            f"date={day}",
            f"device={device}",
            f"{kind}-{content_hash[:12]}.json.gz",
        )
        final_path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        with gzip.open(final_path, "wb") as f:
            f.write(canonical)

        self._append_index(
            {
                "artifact_key": artifact_key,
                "lane": LANE,
                "storage_path": rel,
                "content_hash": content_hash,
                "collected_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                "schema_version": 1,
                "source_metadata": {
                    "vendor": vendor,
                    "device": device,
                    "kind": kind,
                    "source": source,
                    "endpoint": endpoint,
                    "account_basis": account_basis,
                },
            }
        )
        return CaptureResult(
            artifact_key=artifact_key,
            content_hash=content_hash,
            path=final_path,
            stored=True,
            vendor=vendor,
            device=device,
            kind=kind,
        )

    # -- reads (normalizer) ----------------------------------------------------
    def read_capture(self, row: dict[str, Any]) -> bytes:
        with gzip.open(os.path.join(self.root, row["storage_path"]), "rb") as f:
            return f.read()

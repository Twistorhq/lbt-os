# Prepared by Twistor Holdings LLC
"""TW-612 bronze writer: raw artifacts in, content-hashed, license-tagged, immutable.

Every ingestor funnels through write_bronze_artifact():
  1. fetch bytes (streaming, with resume-safe temp file)
  2. sha256 the payload
  3. idempotency: skip when a bronze row with the same (lane, content_hash)
     already exists -- re-runs are free and safe
  4. insert sci_bronze_artifacts with license_tag + license_verdict

TW-208 self-healing: per-item fault isolation. One bad file never crushes
the batch; failures are recorded in the run manifest and retried later.
TW-552 PII: source contact strings are hashed, never stored raw.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .licenses import LicenseTag, assert_gold_eligible

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class IngestItem:
    """One file to pull into bronze."""

    source_key: str  # registry key in licenses.KNOWN_LICENSES, or "custom"
    remote_url: str  # where the bytes live
    filename: str  # storage name under the bronze prefix
    license: LicenseTag  # declared up front, before a byte is fetched
    metadata: dict[str, Any] = field(default_factory=dict)  # source-specific facts


@dataclass
class IngestResult:
    item: IngestItem
    artifact_id: str | None  # None when skipped (already present) or failed
    content_hash: str | None
    skipped_duplicate: bool = False
    error: str | None = None


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# TW-552 PII doctrine: contact-shaped metadata values are hashed at ingestion,
# never stored raw. Keys matching these patterns get sha256 treatment.
_CONTACT_KEY_HINTS = ("email", "phone", "contact", "address", "name")


def hash_pii_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of metadata with contact-shaped values hashed.

    Keys containing email/phone/contact/address/name hints have their values
    replaced with 'sha256:<digest>'. Everything else passes through verbatim.
    """
    out = {}
    for key, value in metadata.items():
        if any(hint in key.lower() for hint in _CONTACT_KEY_HINTS) and isinstance(
            value, str
        ):
            out[key] = "sha256:" + hashlib.sha256(value.encode()).hexdigest()
        else:
            out[key] = value
    return out


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class BronzeWriter:
    """Writes raw artifacts to bronze storage + the sci_bronze_artifacts table.

    storage_root: local prefix (production: the bronze bucket mount).
    db:           supabase-style client (table().select/insert().execute()).
    lab_id:       the registered lab row for this ingestion program.
    fetcher:      injectable download function for tests; defaults to urllib.
    """

    def __init__(
        self,
        storage_root: str,
        db: Any,
        lab_id: str,
        lane: str,
        fetcher: Callable[[str, str], None] | None = None,
    ) -> None:
        self.storage_root = storage_root
        self.db = db
        self.lab_id = lab_id
        self.lane = lane
        self.fetcher = fetcher or self._default_fetch

    # -- download -----------------------------------------------------------
    @staticmethod
    def _default_fetch(url: str, dest: str) -> None:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Twistor-Ingest/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                out.write(block)

    # -- license validation -------------------------------------------------
    @staticmethod
    def _validate_license(item: IngestItem) -> None:
        """Refuse items whose declared license doesn't match the registry.

        Registered source_key: the tag must be exactly the registered one.
        "custom": GREEN is refused without a written justification in metadata
        (prevents silent self-declared GREEN for RED sources).
        """
        from .licenses import KNOWN_LICENSES, VERDICT_GREEN

        if item.source_key == "custom":
            if item.license.verdict == VERDICT_GREEN and not item.metadata.get(
                "license_justification"
            ):
                raise ValueError(
                    "custom source claiming GREEN needs metadata['license_justification']"
                )
            return
        expected = KNOWN_LICENSES.get(item.source_key)
        if expected is None:
            raise ValueError(
                f"unknown source_key {item.source_key!r}: not in license registry"
            )
        if item.license != expected:
            raise ValueError(
                f"license mismatch for {item.source_key!r}: declared "
                f"{item.license.tag}/{item.license.verdict}, registry says "
                f"{expected.tag}/{expected.verdict}"
            )

    # -- idempotency ----------------------------------------------------------
    def _existing_by_hash(self, content_hash: str) -> str | None:
        resp = (
            self.db.table("sci_bronze_artifacts")
            .select("id")
            .eq("lane", self.lane)
            .eq("content_hash", content_hash)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return rows[0]["id"] if rows else None

    # -- write ----------------------------------------------------------------
    def write(self, item: IngestItem) -> IngestResult:
        """Fetch one item into bronze. Never raises for item-level failures.

        License hardening (Rosa TW-612): the declared license is validated
        against the registry before a byte is stored. A planner cannot
        self-declare GREEN for a RED source.
        """
        try:
            self._validate_license(item)
        except ValueError as exc:
            log.warning(
                "bronze license rejected lane=%s url=%s: %s",
                self.lane,
                item.remote_url,
                exc,
            )
            return IngestResult(item, None, None, error=str(exc))
        lane_dir = os.path.join(self.storage_root, self.lane)
        try:
            os.makedirs(lane_dir, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="ingest-", dir=lane_dir)
            os.close(tmp_fd)
            self.fetcher(item.remote_url, tmp_path)
            content_hash = sha256_file(tmp_path)

            dup = self._existing_by_hash(content_hash)
            if dup:
                os.unlink(tmp_path)
                log.info(
                    "bronze duplicate skipped lane=%s hash=%s",
                    self.lane,
                    content_hash[:12],
                )
                return IngestResult(item, dup, content_hash, skipped_duplicate=True)

            storage_path = os.path.join(self.lane, item.filename)
            final_path = os.path.join(self.storage_root, storage_path)
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            os.rename(tmp_path, final_path)

            row = {
                "lane": self.lane,
                "lab_id": self.lab_id,
                "storage_path": storage_path,
                "content_hash": content_hash,
                "collected_at": _utcnow(),
                "schema_version": SCHEMA_VERSION,
                "license_tag": item.license.tag,
                "license_verdict": item.license.verdict,
                "license_source_url": item.license.source_url,
                "source_metadata": hash_pii_metadata(item.metadata),
            }
            resp = self.db.table("sci_bronze_artifacts").insert(row).execute()
            data = getattr(resp, "data", None) or []
            artifact_id = data[0]["id"] if data else None
            log.info(
                "bronze wrote lane=%s file=%s hash=%s license=%s",
                self.lane,
                item.filename,
                content_hash[:12],
                item.license.tag,
            )
            return IngestResult(item, artifact_id, content_hash)
        except Exception as exc:  # TW-208: per-item isolation, never crush the batch
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            log.warning(
                "bronze ingest failed lane=%s url=%s: %s",
                self.lane,
                item.remote_url,
                exc,
            )
            return IngestResult(item, None, None, error=str(exc))

    def write_all(self, items: Iterable[IngestItem]) -> list[IngestResult]:
        return [self.write(it) for it in items]

    # -- gold gate --------------------------------------------------------------
    def gold_gate(self, artifact_id: str) -> LicenseTag:
        """Re-resolve the license tag for an artifact and assert gold eligibility.

        Call this at promotion time, not ingest time: the verdict that matters
        is the one on the stored row, in case the registry changed since ingest.
        """
        resp = (
            self.db.table("sci_bronze_artifacts")
            .select("license_tag,license_verdict,license_source_url")
            .eq("id", artifact_id)
            .single()
            .execute()
        )
        row = getattr(resp, "data", None)
        if row is None:
            raise LookupError(f"no bronze artifact {artifact_id}")
        tag = LicenseTag(
            tag=row["license_tag"],
            verdict=row["license_verdict"],
            source_url=row.get("license_source_url") or "",
        )
        assert_gold_eligible(tag, context=f"bronze artifact {artifact_id}")
        return tag


def write_run_manifest(path: str, lane: str, results: list[IngestResult]) -> None:
    """Append a JSONL run manifest: what was fetched, skipped, or failed."""
    ok = sum(1 for r in results if r.artifact_id and not r.skipped_duplicate)
    dup = sum(1 for r in results if r.skipped_duplicate)
    failed = [r for r in results if r.error]
    manifest = {
        "lane": lane,
        "ran_at": _utcnow(),
        "fetched": ok,
        "skipped_duplicates": dup,
        "failed": len(failed),
        "failures": [{"url": r.item.remote_url, "error": r.error} for r in failed],
        "artifacts": [
            {
                "id": r.artifact_id,
                "hash": r.content_hash,
                "file": r.item.filename,
                "license": r.item.license.tag,
                "verdict": r.item.license.verdict,
            }
            for r in results
            if r.artifact_id
        ],
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(manifest) + "\n")

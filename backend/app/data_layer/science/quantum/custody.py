# Prepared by Twistor Holdings LLC
"""TW-1329 custody collector: the machine that makes provider
deactivation a billing inconvenience instead of a data loss.

Implements the TW-1328 custody mechanics, in order:

  1. INTENT FIRST. record_intent() commits the job row (circuit,
     backend, shots, sha256) to OUR store before any provider call.
     submit() then records the provider job ID in the same breath as
     the submission returns. An in-flight job is never orphaned: worst
     case it is named, and its circuit is ours, so it can be re-run on
     the fallback provider.
  2. PULL ON COMPLETION. The moment a job completes, its result
     payload, its job metadata, and a fresh backend properties
     snapshot land in bronze verbatim, and the job row flips to
     'custodied' in the same run. Nothing is staged provider-side;
     Braket's S3 result bucket is read through as staging only.
  3. RECONCILIATION SWEEP (scheduler: every 15 minutes). Every job in
     a pre-custodied state is re-polled and pulled if complete.
     Idempotent on (provider, provider_job_id).
  4. DAILY DIFF (tripwire). The provider-side job list is diffed
     against our store; anything provider-side that we do not hold is
     pulled and custodied as a recovered row, and the gap is alerted.
  5. DAILY TELEMETRY SNAPSHOTS. Backend properties/configuration are
     captured on cadence whether or not jobs ran, because telemetry
     is the un-backfillable time series (the IBM lesson).
  6. AUTH FAILURE PAGES SAME-DAY. A ProviderAuthError from any
     transport writes an alert row AND fires the alert sink (named
     channel: the Twistor family board, watched by the founder; alert
     owner: Marcus; technical responder: Dre Coleman; see the
     quantum custody runbook). It never degrades into a silent pause
     the way TW-838 did. The sweep exits nonzero on auth failure.

Store of record: Supabase Postgres (founder decision, TW-1329), via
SQLAlchemy; the same code runs against the SQLite working store for
tests and the local pre-apply lane. Raw payloads live in bronze
(FileBronzeStore locally, sci_bronze_artifacts in production) and are
linked from the job row by the deterministic artifact UUID the lane
already uses (cli._artifact_uuid convention).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import custody_models as cm
from .bronze_store import FileBronzeStore
from .job_transports import JobTransport, ProviderAuthError
from .poller import QuantumPoller
from .transport import QuantumTransport

log = logging.getLogger(__name__)

_UUID_NAMESPACE = uuid.UUID("83838383-3838-4383-8838-383838383838")

# The named alert channel (Rosa nit on TW-1328, built in TW-1329): the
# Twistor family board. The CLI wires the sink to post there; the
# collector itself only requires a callable so tests can capture it.
ALERT_CHANNEL = "twistor-family-board"
ALERT_OWNER = "Marcus"
ALERT_TECHNICAL_RESPONDER = "Dre Coleman"


def _artifact_uuid(content_hash: str) -> uuid.UUID:
    return uuid.uuid5(_UUID_NAMESPACE, content_hash)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustodyAuthAlert(RuntimeError):
    """Raised after an auth failure has been recorded and paged.

    Carries the alert row id so the scheduler wrapper can exit nonzero
    with the alert named, not buried.
    """

    def __init__(self, provider: str, alert_id: str, detail: str) -> None:
        super().__init__(
            f"provider auth failure paged: provider={provider} "
            f"alert={alert_id}: {detail}"
        )
        self.provider = provider
        self.alert_id = alert_id


# An alert sink receives one dict per alert and delivers it to the
# named channel. Signature kept tiny on purpose.
AlertSink = Callable[[dict[str, Any]], None]


class CustodyCollector:
    def __init__(
        self,
        session: Session,
        bronze: FileBronzeStore,
        provider: str,
        job_transport: JobTransport | None = None,
        telemetry_transport: QuantumTransport | None = None,
        telemetry_source: str | None = None,
        account_basis: str = "payg",
        alert_sink: AlertSink | None = None,
    ) -> None:
        self.session = session
        self.bronze = bronze
        self.provider = provider
        self.job_transport = job_transport
        self.telemetry_transport = telemetry_transport
        self.telemetry_source = telemetry_source
        self.account_basis = account_basis
        self.alert_sink = alert_sink

    # -- alerts ---------------------------------------------------------------
    def _raise_alert(self, kind: str, detail: dict[str, Any]) -> cm.SciQuantumAlert:
        alert = cm.SciQuantumAlert(
            id=uuid.uuid4(),
            provider=self.provider,
            kind=kind,
            detail=detail,
        )
        self.session.add(alert)
        self.session.commit()
        if self.alert_sink is not None:
            self.alert_sink(
                {
                    "channel": ALERT_CHANNEL,
                    "owner": ALERT_OWNER,
                    "technical_responder": ALERT_TECHNICAL_RESPONDER,
                    "alert_id": str(alert.id),
                    "provider": self.provider,
                    "kind": kind,
                    "detail": detail,
                    "raised_at": _now().isoformat(),
                }
            )
        return alert

    def _auth_failure(self, exc: ProviderAuthError, during: str) -> CustodyAuthAlert:
        alert = self._raise_alert("auth_failure", {"during": during, "error": str(exc)})
        return CustodyAuthAlert(self.provider, str(alert.id), str(exc))

    # -- mechanic 1: intent first ---------------------------------------------
    def record_intent(
        self,
        backend: str,
        circuit_qasm: str,
        shots: int,
        provenance: dict[str, Any] | None = None,
    ) -> cm.SciQuantumJob:
        job = cm.SciQuantumJob(
            id=uuid.uuid4(),
            provider=self.provider,
            backend=backend,
            circuit_qasm=circuit_qasm,
            circuit_sha256=hashlib.sha256(circuit_qasm.encode()).hexdigest(),
            shots=shots,
            status="intent",
            provenance=provenance or {},
        )
        self.session.add(job)
        self.session.commit()
        return job

    def submit(self, job: cm.SciQuantumJob) -> cm.SciQuantumJob:
        """Submit an intent row's circuit; record the provider ID at once."""
        if self.job_transport is None:
            raise RuntimeError("no job transport configured for this collector")
        if job.status != "intent":
            raise ValueError(f"job {job.id} is {job.status}, not intent")
        try:
            provider_job_id = self.job_transport.submit(
                job.backend, job.circuit_qasm, job.shots
            )
        except ProviderAuthError as exc:
            raise self._auth_failure(exc, during="submit") from exc
        job.provider_job_id = provider_job_id
        job.status = "submitted"
        job.submitted_at = _now()
        job.updated_at = _now()
        self.session.commit()
        return job

    def submit_new(
        self,
        backend: str,
        circuit_qasm: str,
        shots: int,
        provenance: dict[str, Any] | None = None,
    ) -> cm.SciQuantumJob:
        return self.submit(self.record_intent(backend, circuit_qasm, shots, provenance))

    # -- mechanic 2: pull on completion -----------------------------------------
    def _capture_bronze(
        self, *, device: str, kind: str, payload: dict[str, Any], source: str
    ) -> uuid.UUID:
        res = self.bronze.capture(
            vendor=self.provider,
            device=device,
            kind=kind,
            payload=json.dumps(payload).encode(),
            retrieved_at=_now(),
            source=source,
            endpoint=f"custody/{kind}",
            account_basis=self.account_basis,
        )
        return _artifact_uuid(res.content_hash)

    def _snapshot_backend(self, backend: str) -> uuid.UUID | None:
        """Fresh backend properties capture at job completion (mechanic 2).

        Best-effort by design: a snapshot failure must never orphan a
        completed result, which is the record. The daily snapshot run
        (mechanic 5) covers any gap this leaves.
        """
        if self.telemetry_transport is None or backend == "unknown":
            return None
        try:
            payload = self.telemetry_transport.fetch_backend(backend)
        except Exception as exc:  # snapshot is support, result is record
            log.warning("custody snapshot failed backend=%s: %s", backend, exc)
            return None
        if not payload.properties:
            return None
        return self._capture_bronze(
            device=backend,
            kind="properties",
            payload=payload.properties,
            source=self.telemetry_source or self.provider,
        )

    def pull_job(self, job: cm.SciQuantumJob) -> cm.SciQuantumJob:
        """Poll one pre-custodied job; custody it if the provider is done."""
        if self.job_transport is None:
            raise RuntimeError("no job transport configured for this collector")
        if job.status in ("custodied", "failed") or job.provider_job_id is None:
            return job
        try:
            status = self.job_transport.status(job.provider_job_id)
        except ProviderAuthError as exc:
            raise self._auth_failure(exc, during="status") from exc
        if status == "running" or status == "queued":
            job.status = "running"
            job.updated_at = _now()
            self.session.commit()
            return job
        if status == "failed":
            job.status = "failed"
            job.completed_at = _now()
            job.updated_at = _now()
            self.session.commit()
            return job
        # completed: pull result + metadata + fresh snapshot, one run.
        try:
            result = self.job_transport.fetch_result(job.provider_job_id)
        except ProviderAuthError as exc:
            raise self._auth_failure(exc, during="fetch_result") from exc
        result_artifact = self._capture_bronze(
            device=job.backend,
            kind="job_result",
            payload=result,
            source=self.provider,
        )
        snapshot_artifact = self._snapshot_backend(job.backend)
        job.result_artifact_id = result_artifact
        job.snapshot_artifact_id = snapshot_artifact
        job.job_metadata = {
            **(job.job_metadata or {}),
            "result_keys": sorted(result.keys()),
            "custodied_via": "pull_on_completion",
        }
        job.status = "custodied"
        job.completed_at = job.completed_at or _now()
        job.custodied_at = _now()
        job.updated_at = _now()
        self.session.commit()
        return job

    # -- mechanic 3: reconciliation sweep ---------------------------------------
    def reconcile(self) -> dict[str, int]:
        counts = {"checked": 0, "custodied": 0, "still_running": 0, "failed": 0}
        open_jobs = (
            self.session.query(cm.SciQuantumJob)
            .filter(
                cm.SciQuantumJob.provider == self.provider,
                cm.SciQuantumJob.status.in_(("submitted", "running", "completed")),
            )
            .all()
        )
        for job in open_jobs:
            counts["checked"] += 1
            before = job.status
            self.pull_job(job)
            if job.status == "custodied":
                counts["custodied"] += 1
            elif job.status == "failed":
                counts["failed"] += 1
            elif before != job.status or job.status == "running":
                counts["still_running"] += 1
        return counts

    # -- mechanic 4: daily provider-side diff -------------------------------------
    def daily_diff(self) -> dict[str, Any]:
        """Diff provider-side jobs against our store; recover any gap."""
        if self.job_transport is None:
            raise RuntimeError("no job transport configured for this collector")
        try:
            provider_ids = set(self.job_transport.list_job_ids())
        except ProviderAuthError as exc:
            raise self._auth_failure(exc, during="daily_diff") from exc
        held = {
            row[0]
            for row in self.session.query(cm.SciQuantumJob.provider_job_id)
            .filter(
                cm.SciQuantumJob.provider == self.provider,
                cm.SciQuantumJob.provider_job_id.isnot(None),
            )
            .all()
        }
        missing = sorted(provider_ids - held)
        recovered = 0
        for provider_job_id in missing:
            job = cm.SciQuantumJob(
                id=uuid.uuid4(),
                provider=self.provider,
                backend="unknown",
                circuit_qasm="",
                circuit_sha256=hashlib.sha256(b"").hexdigest(),
                shots=0,
                status="submitted",
                provider_job_id=provider_job_id,
                provenance={"recovered_by": "daily_diff"},
            )
            # Recovered rows name a job whose circuit we never held:
            # circuit fields stay empty, shots stays 0 (unknown, never
            # invented), and provenance says how the row got here.
            self.session.add(job)
            self.session.commit()
            self.pull_job(job)
            recovered += 1
        if missing:
            self._raise_alert(
                "diff_gap",
                {"missing_provider_job_ids": missing, "recovered": recovered},
            )
        return {
            "provider_jobs": len(provider_ids),
            "held": len(held),
            "missing": missing,
            "recovered": recovered,
        }

    # -- mechanic 5: daily telemetry snapshots -------------------------------------
    def snapshot_backends(
        self, device_filter: list[str] | None = None
    ) -> dict[str, Any]:
        """Capture daily backend telemetry whether or not jobs ran."""
        if self.telemetry_transport is None:
            raise RuntimeError("no telemetry transport configured for this collector")
        poller = QuantumPoller(
            self.telemetry_transport,
            self.bronze,
            account_basis=self.account_basis,
            vendor=self.provider,
            source=self.telemetry_source,
            endpoint_prefix="custody/snapshot",
        )
        result = poller.run(device_filter)
        today = _now().date()
        ledger_rows = 0
        for cap in result.captured + result.skipped:
            row = cm.SciQuantumBackendSnapshot(
                id=uuid.uuid4(),
                provider=self.provider,
                backend=cap.device,
                snapshot_date=today,
                kind=cap.kind,
                artifact_id=_artifact_uuid(cap.content_hash),
                content_hash=cap.content_hash,
                captured_at=_now(),
                source=self.telemetry_source or self.provider,
                provenance={"purpose": result.purpose},
            )
            self.session.add(row)
            ledger_rows += 1
        self.session.commit()
        return {
            "backends_polled": result.backends_polled,
            "captured": len(result.captured),
            "skipped_duplicates": len(result.skipped),
            "failures": result.failures,
            "ledger_rows": ledger_rows,
        }

    # -- counts (drill + stats read-back) --------------------------------------------
    def counts(self) -> dict[str, Any]:
        jobs = (
            self.session.query(cm.SciQuantumJob)
            .filter(cm.SciQuantumJob.provider == self.provider)
            .all()
        )
        by_status: dict[str, int] = {}
        for job in jobs:
            by_status[job.status] = by_status.get(job.status, 0) + 1
        snapshots = (
            self.session.query(cm.SciQuantumBackendSnapshot)
            .filter(cm.SciQuantumBackendSnapshot.provider == self.provider)
            .count()
        )
        alerts = (
            self.session.query(cm.SciQuantumAlert)
            .filter(cm.SciQuantumAlert.provider == self.provider)
            .count()
        )
        return {
            "provider": self.provider,
            "jobs_total": len(jobs),
            "jobs_by_status": by_status,
            "custodied": by_status.get("custodied", 0),
            "snapshots": snapshots,
            "alerts": alerts,
        }

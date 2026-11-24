# Prepared by Twistor Holdings LLC
"""TW-1329 job transports: how the custody collector submits and pulls jobs.

One interface (JobTransport) per provider, behind the custody collector:

  submit(backend, circuit_qasm, shots) -> provider job ID
  status(provider_job_id) -> normalized status string
  fetch_result(provider_job_id) -> verbatim provider result payload (dict)
  list_job_ids() -> every provider-side job ID the provider will show us

Normalized statuses: "queued", "running", "completed", "failed".
Anything else a provider says is mapped by the transport or raised.

Auth discipline (TW-1328 mechanic 6): a provider answering 401/403 (or
the SDK equivalent) raises ProviderAuthError. The collector turns that
into an alert row AND a same-day page. It is never a silent pause: the
TW-838 lane died quietly that way when IBM deactivated the account.

Provider roster (founder decisions, TW-1329):
  - Braket is the first real provider adapter (TW-1328 primary). It is
    UN-FIRED until the founder creates the AWS account; without AWS
    credentials in the environment it raises AdapterUnavailable naming
    that unlock. Braket writes results to a customer-specified S3
    bucket: that bucket is a STAGING area only. fetch_result reads the
    staged result so the collector can land it in our Postgres store
    of record in the same run.
  - IBM is RETIRED by founder decision (2026-11-24, TW-1329): the
    account lapses and the apps delete Nov 25. No IBM job transport is
    built; the stub below raises AdapterUnavailable so the interface
    stays complete and the retirement is explicit in code, not absence.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from .adapters import AdapterUnavailable

__all__ = [
    "AdapterUnavailable",
    "ProviderAuthError",
    "JobTransport",
    "FixtureJobTransport",
    "BraketJobTransport",
    "IBMJobTransport",
    "get_job_transport",
]


class ProviderAuthError(RuntimeError):
    """The provider rejected our credentials (deactivation shape).

    Distinct from AdapterUnavailable (prerequisites never in place):
    auth worked before or should work, and now the provider says no.
    The collector pages on this the same day it happens.
    """


class JobTransport(Protocol):
    """Vendor job submission/retrieval transport."""

    provider: str

    def submit(self, backend: str, circuit_qasm: str, shots: int) -> str: ...

    def status(self, provider_job_id: str) -> str: ...

    def fetch_result(self, provider_job_id: str) -> dict[str, Any]: ...

    def list_job_ids(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# Fixture transport: tests, the deactivation drill, offline pipeline checks.
# ---------------------------------------------------------------------------
class FixtureJobTransport:
    """In-memory job transport with a revocable switch.

    Jobs complete on the first status poll after submission unless
    polls_to_complete says otherwise. revoke() simulates the provider
    deactivating us mid-life: every subsequent call raises
    ProviderAuthError, exactly the shape IBM produced on 2026-11-04.
    """

    provider = "fixture"

    def __init__(self, polls_to_complete: int = 1) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._polls_to_complete = polls_to_complete
        self._revoked = False
        # Optional hook run inside submit(), before the job ID returns.
        # Tests use it to prove the intent row already exists in our DB
        # at the moment of submission (custody mechanic 1).
        self.on_submit: Any = None

    def revoke(self) -> None:
        self._revoked = True

    def _guard(self) -> None:
        if self._revoked:
            raise ProviderAuthError(
                "fixture provider: credentials rejected (simulated deactivation)"
            )

    def submit(self, backend: str, circuit_qasm: str, shots: int) -> str:
        self._guard()
        if self.on_submit is not None:
            self.on_submit(backend, circuit_qasm, shots)
        self._counter += 1
        job_id = f"fixture-job-{self._counter:04d}"
        self._jobs[job_id] = {
            "backend": backend,
            "circuit_qasm": circuit_qasm,
            "shots": shots,
            "polls": 0,
            "result": {
                "job_id": job_id,
                "backend": backend,
                "shots": shots,
                "counts": {"00": shots // 2, "11": shots - shots // 2},
                "fixture": True,
            },
        }
        return job_id

    def status(self, provider_job_id: str) -> str:
        self._guard()
        job = self._jobs[provider_job_id]
        job["polls"] += 1
        return "completed" if job["polls"] >= self._polls_to_complete else "running"

    def fetch_result(self, provider_job_id: str) -> dict[str, Any]:
        self._guard()
        return dict(self._jobs[provider_job_id]["result"])

    def list_job_ids(self) -> list[str]:
        self._guard()
        return sorted(self._jobs)


# ---------------------------------------------------------------------------
# Amazon Braket: first real provider adapter (TW-1328 primary, 86/100).
# Un-fired until the founder creates the AWS account (TW-1329 unlock).
# ---------------------------------------------------------------------------
BRAKET_S3_BUCKET_ENV = "BRAKET_S3_BUCKET"
BRAKET_S3_PREFIX_ENV = "BRAKET_S3_PREFIX"
BRAKET_REGION_ENV = "AWS_REGION"

_BRAKET_STATUS_MAP = {
    "QUEUED": "queued",
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "CANCELLED": "failed",
}


class BraketJobTransport:
    """Braket job transport over boto3 (lazy import, fail closed).

    Credentials come from the standard AWS environment chain at runtime
    only; nothing is stored in code. Results are staged by Braket in
    the customer S3 bucket named by BRAKET_S3_BUCKET; fetch_result reads
    the staged result.json so the collector lands it in our store of
    record in the same run. The bucket is staging, never the record.
    """

    provider = "braket"

    def __init__(self, braket_client: Any = None, s3_client: Any = None) -> None:
        self._braket = braket_client
        self._s3 = s3_client

    # -- client construction -------------------------------------------------
    def _clients(self) -> tuple[Any, Any]:
        if self._braket is not None and self._s3 is not None:
            return self._braket, self._s3
        bucket = os.environ.get(BRAKET_S3_BUCKET_ENV)
        if not bucket:
            raise AdapterUnavailable(
                f"{BRAKET_S3_BUCKET_ENV} is not set: Braket results stage in a "
                "customer S3 bucket, and the bucket is created after the "
                "founder creates the AWS account (TW-1329 founder unlock). "
                "The adapter is built and un-fired until then."
            )
        try:
            import boto3
            from botocore.exceptions import NoCredentialsError
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise AdapterUnavailable(f"boto3 is not installed: {exc}") from exc
        region = os.environ.get(BRAKET_REGION_ENV, "us-east-1")
        try:
            session = boto3.Session(region_name=region)
            if session.get_credentials() is None:
                raise NoCredentialsError()
        except NoCredentialsError as exc:
            raise AdapterUnavailable(
                "No AWS credentials in the environment: Braket fires only "
                "after the founder creates the AWS account and the collector "
                "credential is issued (TW-1329 founder unlock)."
            ) from exc
        self._braket = session.client("braket")
        self._s3 = session.client("s3")
        return self._braket, self._s3

    @staticmethod
    def _guard_auth(exc: Exception) -> None:
        """Translate SDK auth-shaped failures into ProviderAuthError.

        Duck-typed on the botocore ClientError shape (a ``response``
        dict) so the mapping works with injected clients and never
        requires importing botocore on a host without it.
        """
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status in (401, 403) or "AccessDenied" in code or "Auth" in code:
            raise ProviderAuthError(f"braket auth rejected: {code}") from exc

    # -- JobTransport ----------------------------------------------------------
    def submit(self, backend: str, circuit_qasm: str, shots: int) -> str:
        braket, _ = self._clients()
        bucket = os.environ[BRAKET_S3_BUCKET_ENV]
        prefix = os.environ.get(BRAKET_S3_PREFIX_ENV, "quantum-custody")
        try:
            resp = braket.create_quantum_task(
                action=circuit_qasm,
                deviceArn=backend,
                outputS3Bucket=bucket,
                outputS3KeyPrefix=prefix,
                shots=shots,
            )
        except Exception as exc:
            self._guard_auth(exc)
            raise
        return str(resp["quantumTaskArn"])

    def status(self, provider_job_id: str) -> str:
        braket, _ = self._clients()
        try:
            resp = braket.get_quantum_task(quantumTaskArn=provider_job_id)
        except Exception as exc:
            self._guard_auth(exc)
            raise
        raw = str(resp.get("status", ""))
        try:
            return _BRAKET_STATUS_MAP[raw]
        except KeyError as exc:
            raise RuntimeError(f"braket returned unmapped status {raw!r}") from exc

    def fetch_result(self, provider_job_id: str) -> dict[str, Any]:
        braket, s3 = self._clients()
        try:
            task = braket.get_quantum_task(quantumTaskArn=provider_job_id)
            output = task.get("outputS3Directory") or {}
            bucket = output.get("bucket") or os.environ[BRAKET_S3_BUCKET_ENV]
            prefix = output.get("keyPrefix", "")
            key = f"{prefix}/results.json".lstrip("/")
            obj = s3.get_object(Bucket=bucket, Key=key)
            payload = json.loads(obj["Body"].read())
        except Exception as exc:
            self._guard_auth(exc)
            raise
        if not isinstance(payload, dict):
            return {"result": payload, "provider_job_id": provider_job_id}
        return payload

    def list_job_ids(self) -> list[str]:
        braket, _ = self._clients()
        ids: list[str] = []
        token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {"filters": [], "maxResults": 100}
                if token:
                    kwargs["nextToken"] = token
                resp = braket.search_quantum_tasks(**kwargs)
                ids.extend(t["quantumTaskArn"] for t in resp.get("quantumTasks", []))
                token = resp.get("nextToken")
                if not token:
                    return sorted(ids)
        except Exception as exc:
            self._guard_auth(exc)
            raise


# ---------------------------------------------------------------------------
# IBM: RETIRED by founder decision (TW-1329, 2026-11-24). Interface stub
# only. The account lapses; the apps delete Nov 25. No salvage pull, no
# armed adapter. The TW-838 telemetry wiring stays as historical code;
# nothing new is built on this provider.
# ---------------------------------------------------------------------------
class IBMJobTransport:
    provider = "ibm"

    def _retired(self) -> None:
        raise AdapterUnavailable(
            "IBM Quantum is retired by founder decision (TW-1329, "
            "2026-11-24): the account lapses and no IBM job transport is "
            "built. Braket is the first real provider adapter."
        )

    def submit(self, backend: str, circuit_qasm: str, shots: int) -> str:
        self._retired()
        raise AssertionError("unreachable")

    def status(self, provider_job_id: str) -> str:
        self._retired()
        raise AssertionError("unreachable")

    def fetch_result(self, provider_job_id: str) -> dict[str, Any]:
        self._retired()
        raise AssertionError("unreachable")

    def list_job_ids(self) -> list[str]:
        self._retired()
        raise AssertionError("unreachable")


_JOB_TRANSPORTS = {"braket": BraketJobTransport, "ibm": IBMJobTransport}


def get_job_transport(provider: str) -> JobTransport:
    try:
        return _JOB_TRANSPORTS[provider]()
    except KeyError:
        raise AdapterUnavailable(f"unknown quantum job provider {provider!r}")

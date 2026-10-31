# Prepared by Twistor Holdings LLC
"""TW-838 poller: fleet capture runs over a QuantumTransport into bronze.

Account-basis discipline (Angela ruling, TW-838; EUA Section 2(d)):
  - basis "payg": production accumulation under the IBM Cloud
    Pay-As-You-Go account (CSA governs; commercial use contemplated).
    purpose = "production".
  - basis "open": the free Open Plan is licensed for research, education,
    testing and evaluation only. Runs are capped at OPEN_TEST_MAX_BACKENDS
    devices and stamped purpose = "code-test" in every capture's metadata
    and in the run manifest. An Open Plan account must never feed the
    production corpus.

TW-208 self-healing: per-device fault isolation. One backend failing
never crushes the run; failures land in the result and the run manifest.
Batch only: the poller runs when invoked (scheduler calls the CLI), it is
not a resident daemon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .bronze_store import CaptureResult, FileBronzeStore
from .transport import QuantumTransport

log = logging.getLogger(__name__)

OPEN_TEST_MAX_BACKENDS = 2
SOURCE_IBM = "ibm-quantum-platform"


@dataclass
class PollRunResult:
    purpose: str  # "production" (payg) | "code-test" (open)
    account_basis: str
    backends_polled: int = 0
    captured: list[CaptureResult] = field(default_factory=list)
    skipped: list[CaptureResult] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "lane": "quantum",
            "purpose": self.purpose,
            "account_basis": self.account_basis,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "backends_polled": self.backends_polled,
            "captured": len(self.captured),
            "skipped_duplicates": len(self.skipped),
            "failed": len(self.failures),
            "failures": self.failures,
            "artifacts": [
                {
                    "key": c.artifact_key,
                    "hash": c.content_hash,
                    "device": c.device,
                    "kind": c.kind,
                }
                for c in self.captured
            ],
        }


class QuantumPoller:
    def __init__(
        self,
        transport: QuantumTransport,
        store: FileBronzeStore,
        account_basis: str,
        vendor: str = "ibm",
    ) -> None:
        if account_basis not in ("payg", "open"):
            raise ValueError(
                f"account_basis must be 'payg' or 'open', got {account_basis!r}"
            )
        self.transport = transport
        self.store = store
        self.account_basis = account_basis
        self.vendor = vendor
        self.purpose = "production" if account_basis == "payg" else "code-test"

    def run(self, device_filter: list[str] | None = None) -> PollRunResult:
        names = self.transport.list_backends()
        if device_filter:
            wanted = set(device_filter)
            names = [n for n in names if n in wanted]
        if self.account_basis == "open":
            names = names[:OPEN_TEST_MAX_BACKENDS]
        result = PollRunResult(purpose=self.purpose, account_basis=self.account_basis)
        now = datetime.now(timezone.utc)
        for name in names:
            result.backends_polled += 1
            try:
                payload = self.transport.fetch_backend(name)
            except Exception as exc:  # TW-208: isolate, record, continue
                log.warning("poller fetch failed device=%s: %s", name, exc)
                result.failures.append({"device": name, "error": str(exc)})
                continue
            for kind, doc in (
                ("configuration", payload.configuration),
                ("properties", payload.properties),
            ):
                if not doc:
                    continue
                res = self.store.capture(
                    vendor=self.vendor,
                    device=name,
                    kind=kind,
                    payload=json.dumps(doc).encode(),
                    retrieved_at=now,
                    source=SOURCE_IBM if self.vendor == "ibm" else self.vendor,
                    endpoint=f"/backends/{name}",
                    account_basis=self.account_basis,
                )
                (result.captured if res.stored else result.skipped).append(res)
        return result

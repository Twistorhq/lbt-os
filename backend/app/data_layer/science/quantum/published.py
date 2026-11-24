# Prepared by Twistor Holdings LLC
"""TW-1329 credential-free collection start: published QPU snapshots.

With IBM retired (founder decision, TW-1329) and Braket un-fired until
the AWS account exists, collection starts TODAY from data that needs
no provider account at all: the backend calibration snapshots IBM
publishes inside the qiskit-ibm-runtime package's fake-provider module.

Honesty rules for this source, enforced in the provenance stamps:
  - These are REAL calibration snapshots of REAL IBM devices, published
    by IBM inside an Apache-2.0 package. They are NOT live telemetry
    from a Twistor account, and they are NOT current: each payload
    carries the device's own last_update_date, which the normalizer
    keeps as measured_at. Bronze captures stamp source =
    "qiskit-fake-provider" and purpose = "published-snapshot" so no
    reader can mistake them for account telemetry.
  - The snapshot date accrues value the same way account telemetry
    does: it is a dated observation of a real device that cannot be
    re-measured later. As the package republishes updated snapshots,
    the daily poll captures the new state and the series grows.

The transport reads the INSTALLED package only: no network, no
credentials, no account. If the package is absent it raises
AdapterUnavailable with the install instruction, fail closed.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from .adapters import AdapterUnavailable
from .transport import BackendPayload


def _jsonable(doc: dict[str, Any]) -> dict[str, Any]:
    """Deep-convert a fake-provider document to plain JSON values.

    The package's to_dict() leaves datetime objects inside calibration
    entries; IBM's platform API (the transport contract's reference
    shape) spells the same values as ISO strings. Converting here
    keeps bronze payloads byte-comparable with API captures of the
    same device state, and changes no values.
    """
    return json.loads(json.dumps(doc, default=str))


SOURCE_PUBLISHED = "qiskit-fake-provider"

# Curated starter fleet: one device per IBM generation family in the
# published set. The daily snapshot poll walks this list; extend it in
# code review, never from provider output.
PUBLISHED_BACKENDS = (
    "FakeBrisbane",  # Eagle r3, 127q
    "FakeKyiv",  # Eagle r3, 127q
    "FakeSherbrooke",  # Eagle r3, 127q
    "FakeKawasaki",  # Heron r1, 156q
    "FakeTorino",  # Heron r1, 133q
    "FakeMarrakesh",  # Heron r2, 156q
    "FakeManilaV2",  # Falcon r5.11L, 5q
    "FakeLimaV2",  # Falcon r5.11L, 5q
)


class PublishedSnapshotTransport:
    """QuantumTransport over IBM's published fake-provider snapshots."""

    def __init__(self, backends: tuple[str, ...] = PUBLISHED_BACKENDS) -> None:
        try:
            self._module = importlib.import_module("qiskit_ibm_runtime.fake_provider")
        except ImportError as exc:
            raise AdapterUnavailable(
                "qiskit-ibm-runtime is not installed: the credential-free "
                "published-snapshot source needs the package on disk "
                "(pip install qiskit-ibm-runtime). No account, no network, "
                "no credentials are involved; the install is the only gate."
            ) from exc
        self._backends = backends
        import qiskit_ibm_runtime

        self.package_version = qiskit_ibm_runtime.__version__

    def list_backends(self) -> list[str]:
        names = []
        for cls_name in self._backends:
            cls = getattr(self._module, cls_name, None)
            if cls is None:
                continue
            names.append(cls().name)
        return sorted(names)

    def fetch_backend(self, name: str) -> BackendPayload:
        for cls_name in self._backends:
            cls = getattr(self._module, cls_name, None)
            if cls is None:
                continue
            backend = cls()
            if backend.name != name:
                continue
            properties: dict[str, Any] = {}
            try:
                props = backend.properties()
                if props is not None:
                    properties = _jsonable(props.to_dict())
            except Exception:
                properties = {}
            configuration: dict[str, Any] = {}
            try:
                configuration = _jsonable(backend.configuration().to_dict())
            except Exception:
                configuration = {}
            status: dict[str, Any] = {}
            try:
                status = backend.status().to_dict()
            except Exception:
                status = {}
            return BackendPayload(
                name=name,
                configuration=configuration,
                properties=properties,
                status=status,
            )
        raise KeyError(f"no published snapshot backend named {name!r}")

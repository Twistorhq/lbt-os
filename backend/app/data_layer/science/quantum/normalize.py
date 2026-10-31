# Prepared by Twistor Holdings LLC
"""TW-838 silver normalizer: IBM properties payloads -> observation rows.

Input contract: the Qiskit BackendProperties.to_dict() shape
{backend_name, backend_version, last_update_date, qubits: [[NDUV...]],
gates: [{gate, name, qubits, parameters: [NDUV...]}], general: [...]}.
NDUV = {date, name, unit, value}.

Output: one dict per observation in the brief's silver schema (vendor,
device, device_generation, qubit_or_edge, metric, value, unit,
measured_at, source_record_ref). measured_at is the vendor's own
per-entry calibration timestamp where the payload carries one, else the
payload's last_update_date.

Per-item fault isolation (TW-208): a malformed entry (missing value,
unknown metric name) is skipped and counted, never raised. Unknown metric
names are refused rather than invented into the schema.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .models import METRICS as _MODEL_METRICS
from .transport import BackendPayload

log = logging.getLogger(__name__)

KNOWN_METRICS = frozenset(_MODEL_METRICS)

# IBM qubit NDUV names -> silver metric names.
_QUBIT_METRICS = {
    "T1": "t1",
    "T2": "t2",
    "frequency": "frequency",
    "readout_error": "readout_error",
    "readout_length": "readout_length",
    "prob_meas0_prep1": "prob_meas0_prep1",
    "prob_meas1_prep0": "prob_meas1_prep0",
}


def _measured_at(entry: dict[str, Any], fallback: str | None) -> str:
    raw = entry.get("date") or fallback
    if raw is None:
        return datetime.now(timezone.utc).isoformat()
    return str(raw).replace("Z", "+00:00")


def _device_generation(configuration: dict[str, Any]) -> str | None:
    ptype = configuration.get("processor_type") or {}
    if isinstance(ptype, dict) and ptype.get("family"):
        return str(ptype["family"])
    return None


def normalize_properties(
    payload: BackendPayload,
    artifact_ref: str,
    vendor: str = "ibm",
) -> list[dict[str, Any]]:
    """Explode one backend properties payload into silver observation rows."""
    props = payload.properties or {}
    configuration = payload.configuration or {}
    device = props.get("backend_name") or payload.name
    generation = _device_generation(configuration)
    fallback_date = props.get("last_update_date")
    rows: list[dict[str, Any]] = []
    skipped = 0

    def base(qubit_or_edge: str, metric: str, entry: dict[str, Any]) -> dict | None:
        nonlocal skipped
        value = entry.get("value")
        if value is None or metric not in KNOWN_METRICS:
            skipped += 1
            return None
        return {
            "vendor": vendor,
            "device": device,
            "device_generation": generation,
            "qubit_or_edge": qubit_or_edge,
            "metric": metric,
            "value": float(value),
            "unit": entry.get("unit") or None,
            "measured_at": _measured_at(entry, fallback_date),
            "source_record_ref": artifact_ref,
        }

    for qi, qubit_entries in enumerate(props.get("qubits") or []):
        for entry in qubit_entries or []:
            metric = _QUBIT_METRICS.get(entry.get("name", ""))
            if metric is None:
                skipped += 1
                continue
            row = base(f"q{qi}", metric, entry)
            if row is not None:
                rows.append(row)

    for gate in props.get("gates") or []:
        qubits = gate.get("qubits") or []
        if not qubits:
            continue
        edge = "-".join(f"q{q}" for q in qubits)
        for entry in gate.get("parameters") or []:
            name = entry.get("name")
            if name == "gate_error":
                metric = "gate_error_1q" if len(qubits) == 1 else "gate_error_2q"
            elif name == "gate_length":
                metric = "gate_length"
            else:
                skipped += 1
                continue
            row = base(edge, metric, entry)
            if row is not None:
                rows.append(row)

    if skipped:
        log.info(
            "normalize skipped %d malformed/unknown entries for %s",
            skipped,
            device,
        )
    return rows

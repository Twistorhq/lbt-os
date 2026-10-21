"""Gate-run wiring over the TW-649 harness (TW-658).

`run_gate` is what a real launch-gate run calls. It resolves the
dataset from the registry, defaults the coverage set from the
registry entry (Rosa's TW-649 observation: the runner pulls
expected_groups from the entry itself, so a run cannot forget to
declare its coverage), stamps the dataset id and the engine version
into the report, and delegates the math to runner.run_benchmark.

The consented-volunteer set is special: its demographic labels are
self-described, so its coverage strata cannot be fixed in the
registry. The collection protocol declares them here, once, before
the first gate run (registry entry twistor-consented-volunteers);
a gate run attempted before that declaration is a configuration
error, raised loudly, never an INSUFFICIENT_DATA shrug and never a
pass. Collecting the volunteers themselves is a human step under
the TW-654 consent package, not part of this wiring.
"""

from __future__ import annotations

from typing import Iterable, Optional

from app.verify.benchmark import registry
from app.verify.benchmark.metrics import GateCriteria, ScoredAttempt
from app.verify.benchmark.runner import run_benchmark
from app.verify.engine.config import ENGINE_VERSION

_VOLUNTEERS_ID = "twistor-consented-volunteers"
_volunteer_strata: tuple[str, ...] = ()


class GateConfigurationError(RuntimeError):
    """The gate run is misconfigured before any math happens."""


def declare_volunteer_strata(groups: Iterable[str]) -> tuple[str, ...]:
    """Record the collection protocol's coverage strata.

    Called by the collection protocol (TW-654 consent flow) once
    the self-described strata exist. Duplicates collapse; an empty
    declaration is rejected, because an empty coverage set would
    let the gate pass on whatever showed up.
    """
    global _volunteer_strata
    declared = tuple(dict.fromkeys(g.strip() for g in groups if g.strip()))
    if not declared:
        raise GateConfigurationError(
            "volunteer coverage strata must name at least one group"
        )
    _volunteer_strata = declared
    return _volunteer_strata


def volunteer_strata() -> tuple[str, ...]:
    return _volunteer_strata


def run_gate(
    dataset_id: str,
    attempts: Iterable[ScoredAttempt],
    threshold: float,
    criteria: GateCriteria = GateCriteria(),
    liveness: Optional[Iterable[tuple[str, bool]]] = None,
    expected_groups: Optional[Iterable[str]] = None,
) -> dict:
    """Run the launch gate for a registered dataset.

    Coverage defaults from the registry entry; the consented
    volunteer set takes its coverage from the declared collection
    strata and refuses to run before they exist.
    """
    entry = registry.get(dataset_id)  # KeyError on unregistered: honest
    if expected_groups is None:
        if dataset_id == _VOLUNTEERS_ID:
            if not _volunteer_strata:
                raise GateConfigurationError(
                    "consented-volunteer gate run attempted before the "
                    "collection protocol declared its coverage strata; "
                    "labels are self-described, so the strata come from "
                    "the protocol (declare_volunteer_strata), never "
                    "from the harness"
                )
            expected = _volunteer_strata
        else:
            expected = entry.expected_groups
    else:
        expected = tuple(expected_groups)

    report = run_benchmark(
        attempts,
        threshold,
        criteria=criteria,
        liveness=liveness,
        expected_groups=expected,
    )
    report["dataset"] = dataset_id
    report["engine_version"] = ENGINE_VERSION
    return report

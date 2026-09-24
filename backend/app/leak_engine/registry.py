"""Detector registry — the plugin contract.

A detector is a named leak pattern: what vertical it serves, which source
tables it reads, and a run() that returns findings. New vertical = new
detectors registered here, not a new engine.

run() contract (TW-204): return the findings list, or a
(findings, meta) tuple where meta may carry "skipped_rows" — the count of
malformed rows the detector skipped loudly instead of dying on.
"""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Detector:
    name: str
    vertical: str | None  # None = runs for every vertical
    requires: list[str] = field(default_factory=list)
    run: Callable[..., Any] | None = None

    def __call__(self, db, org_id: str) -> Any:
        assert self.run is not None, f"detector {self.name} has no run()"
        return self.run(db, org_id)


_REGISTRY: list[Detector] = []


def register(detector: Detector) -> Detector:
    _REGISTRY.append(detector)
    return detector


def all_detectors() -> list[Detector]:
    return list(_REGISTRY)


def detectors_for(vertical: str | None) -> list[Detector]:
    return [d for d in _REGISTRY if d.vertical is None or d.vertical == vertical]


# Import detector modules so they self-register.
from .detectors import hvac  # noqa: E402,F401

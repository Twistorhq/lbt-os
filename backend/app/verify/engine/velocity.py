"""Velocity limits: engine stage 5 fraud signal (TW-658, spec S5.2.5).

Attempts per document, per device, and per network inside a
rolling window. Exceeding a limit never fails a member outright:
it routes the session to human review with the exceeded kinds
named, because a shared household network is not a fraud ring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import EngineConfig

_KINDS = ("document", "device", "network")


@dataclass(frozen=True)
class AttemptRecord:
    kind: str  # one of _KINDS
    key: str  # the document hash / device ref / network ref
    at: datetime


def check_velocity(
    records: list[AttemptRecord],
    *,
    document_key: str | None,
    device_key: str | None,
    network_key: str | None,
    now: datetime,
    config: EngineConfig,
) -> list[str]:
    """Kind names whose in-window attempt count reached the limit.

    `records` includes the current attempt (the caller records it
    before checking), so reaching the configured maximum is the
    trip: the next attempt is the one a human should look at.
    """
    keys = {"document": document_key, "device": device_key, "network": network_key}
    limits = {
        "document": config.velocity_max_per_document,
        "device": config.velocity_max_per_device,
        "network": config.velocity_max_per_network,
    }
    window_start = now - timedelta(hours=config.velocity_window_hours)
    exceeded: list[str] = []
    for kind in _KINDS:
        key = keys[kind]
        if key is None:
            continue
        count = sum(
            1
            for record in records
            if record.kind == kind and record.key == key and record.at >= window_start
        )
        if count >= limits[kind]:
            exceeded.append(kind)
    return exceeded

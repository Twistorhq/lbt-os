"""TW-650 Twistor Social — scheduler tick (Phase 3 skeleton).

The tick (TW-625 Flows pattern) drives all time-based work in later
phases: series instance materialization on the rolling 4-week
horizon, check-in prompt scheduling, welfare-check prompts, trust
score recomputation from the ledger, dormancy step-down, collectible
derivation, and the integrity lifecycle sweeps (spec Sections 6.7,
7.6). Phase 3 ships the seam only: run_tick raises until Phase 5
wires the first real jobs, so a scheduler that silently does
nothing can never be mistaken for a working one.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run_tick(db) -> dict:
    raise NotImplementedError(
        "TW-650 skeleton: tick jobs land in Phase 5 (tasks.md items 19-23)"
    )

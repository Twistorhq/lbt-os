"""TW-622 Twistor Telemetry — sampling-degradation budget contract.

The TW-630 wedge: PostHog punishes success with per-event meter cliffs.
Telemetry's contract:

- Under 80% of the monthly tier: ingest everything.
- At 80%: warn once (email + usage headers via the TW-607/TW-608 path),
  keep ingesting.
- Over the tier: SAMPLE deterministically at a recorded rate — graceful
  degradation, never silence, never a surprise bill.

No 429 wall here (unlike rate limits): analytics must degrade, not die.
"""

from __future__ import annotations

import hashlib

WARN_RATIO = 0.8
MIN_SAMPLE_RATE = 0.01


def ingest_decision(used: int, cap: int | None) -> dict:
    """Pure decision: what to do with the next ingested event.

    Returns {"action": "ingest"|"sample", "rate": float, "warn": bool}.
    cap=None means the generous unlimited tier.
    """
    if cap is None:
        return {"action": "ingest", "rate": 1.0, "warn": False}
    if used >= cap:
        # Over the tier: sample. The deeper the overage, the thinner the
        # sample — but never below MIN_SAMPLE_RATE, and never zero.
        rate = max(MIN_SAMPLE_RATE, cap / used)
        return {"action": "sample", "rate": rate, "warn": True}
    if used >= WARN_RATIO * cap:
        return {"action": "ingest", "rate": 1.0, "warn": True}
    return {"action": "ingest", "rate": 1.0, "warn": False}


def should_keep(key: str, rate: float) -> bool:
    """Deterministic sampling: same key + rate -> same verdict, always.

    key should uniquely identify the event (org + hashed id + event +
    timestamp). sha256 -> uniform [0, 1); keep when under the rate.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"sample rate must be in [0, 1], got {rate}")
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    uniform = int.from_bytes(digest[:8], "big") / 2**64
    return uniform < rate

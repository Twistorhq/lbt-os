# Prepared by Twistor Holdings LLC
"""TW-838 quantum toll paths, re-scoped by the DERIVED-ONLY ruling.

Standing scientific price caps (TW-593/TW-606): $0.01 floor, $0.10 hard
cap per download/API call. The brief priced four quantum products:
$0.01 / $0.03 / $0.05 / $0.09. Angela's terms read (2026-10-30) rules
that raw calibration values may not be redistributed at any price, so
the SKUs are re-scoped to what is actually sellable:

  - $0.01 current snapshot -> current-day DERIVED aggregates (medians and
    spreads per device), never the raw snapshot. Raw is internal only.
  - $0.03 device history range -> DERIVED history (device-day aggregates
    and drift features over the range), not raw values.
  - $0.05 cross-vendor drift analytics -> unchanged (already derived).
  - $0.09 full-history bulk export -> HOLD. As priced in the brief this
    was a raw export; it stays disabled until IBM grants raw rights in
    writing. quote() and meter_quantum_read() raise TollHoldError.

Metering rides the M1 ledger exactly like the other science lanes:
toll_service.record_toll_event with an explicit amount_cents, and
failures are swallowed (TW-208: metering never breaks serving). These
are code paths only: no quantum product is publicly exposed by this
module, and the paid tier additionally waits on the portal surface.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

HARD_CAP_CENTS = 10  # $0.10 standing scientific hard cap

SKU_CURRENT_AGGREGATES = "quantum.current_day_aggregates"
SKU_DERIVED_HISTORY = "quantum.derived_history"
SKU_DRIFT_ANALYTICS = "quantum.drift_analytics"
SKU_RAW_BULK_EXPORT = "quantum.raw_bulk_export"

_PRICES_CENTS = {
    SKU_CURRENT_AGGREGATES: 1,
    SKU_DERIVED_HISTORY: 3,
    SKU_DRIFT_ANALYTICS: 5,
    SKU_RAW_BULK_EXPORT: 9,
}

_HELD_SKUS = frozenset({SKU_RAW_BULK_EXPORT})


class TollHoldError(RuntimeError):
    """The SKU is on legal hold (raw redistribution not granted)."""


def quote(sku: str) -> int:
    """Price in cents for a quantum read SKU. Raises for held/unknown SKUs."""
    if sku in _HELD_SKUS:
        raise TollHoldError(
            f"{sku} is on HOLD: raw calibration values may not be "
            "redistributed at any price (Angela ruling, TW-838, "
            "2026-10-30). It re-opens only on a written IBM grant."
        )
    try:
        cents = _PRICES_CENTS[sku]
    except KeyError:
        raise ValueError(f"unknown quantum toll SKU {sku!r}") from None
    assert cents < HARD_CAP_CENTS, f"{sku} breaches the $0.10 hard cap"
    return cents


def meter_quantum_read(
    db: Any,
    org_id: str,
    sku: str,
    request_id: str,
    quantity: float = 1.0,
) -> dict | None:
    """Emit an M1 toll event for a quantum read. Held SKUs raise before
    any metering; ledger failures are logged and swallowed (TW-208)."""
    cents = quote(sku)  # raises TollHoldError / ValueError before metering
    try:
        from app.services import toll_service

        return toll_service.record_toll_event(
            db,
            org_id=org_id,
            event_type=sku,
            quantity=quantity,
            amount_cents=int(round(cents * quantity)),
            idempotency_key=f"sci:quantum:{request_id}",
            source_type="sci_quantum",
            source_id=None,
        )
    except Exception as exc:  # TW-208
        log.warning("quantum toll emit failed (%s); continuing", exc)
        return None

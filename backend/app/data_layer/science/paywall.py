# Prepared by Twistor Holdings LLC
"""TW-599 Wave 1 paywall: subscription gate + dynamic derivative pricing + Excel delivery.

Delivery doctrine (Justynn 2026-10-18, LAW for scientific datasets):
  - large datasets  -> API only
  - medium datasets -> API + downloadable Excel
  - small datasets  -> downloadable Excel, API if needed
  - EVERYTHING behind the paywall: an active Twistor subscription is required
    for every read; API calls are toll-metered with dynamic derivative pricing
    (rates scale with platform volume); Excel downloads carry a one-time fee
    ON TOP of the subscription (subscription + per-download fee).

Excel pricing (his words): small ~$5-7 (30k-row benchmark), medium ~$7-10,
large ~$20-25. Implemented: $6.00 / $8.50 / $22.50.

Dynamic derivative pricing: the per-unit toll rate is multiplied by a volume
factor derived from the org's trailing-30-day science toll spend. Rates fall
as volume rises (the volume discount derivative):
  spend < $50        -> 1.00x
  $50  - $200        -> 0.90x
  $200 - $1000       -> 0.80x
  > $1000            -> 0.70x
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ tiers
TIER_SMALL = "small"
TIER_MEDIUM = "medium"
TIER_LARGE = "large"

SMALL_MAX_ROWS = 100_000
MEDIUM_MAX_ROWS = 1_000_000

# One-time Excel download fees, cents. Inside Justynn's bands.
EXCEL_FEE_CENTS = {
    TIER_SMALL: 600,    # $6.00  (band $5-7)
    TIER_MEDIUM: 850,   # $8.50  (band $7-10)
    TIER_LARGE: 2250,   # $22.50 (band $20-25)
}

# Delivery matrix: which surfaces each tier may use.
DELIVERY_MATRIX = {
    TIER_SMALL: {"api": True, "excel": True},
    TIER_MEDIUM: {"api": True, "excel": True},
    TIER_LARGE: {"api": True, "excel": False},  # large: API only
}

# Volume-discount derivative tiers: (spend_cents_floor, multiplier).
VOLUME_TIERS = (
    (100_000, 0.70),  # > $1,000 / 30d
    (20_000, 0.80),   # > $200
    (5_000, 0.90),    # > $50
    (0, 1.00),
)

DOWNLOAD_EVENT = "data.science.download"
SCIENCE_EVENT_PREFIX = "data.science."

# Orgs that can never pass the gate (no free tier: everything is paywalled).
_UNSCOPED_ORGS = {"", "public", "unscoped", "anonymous"}


def dataset_tier(row_count: int) -> str:
    """Size tier for a dataset version by row count."""
    if row_count < SMALL_MAX_ROWS:
        return TIER_SMALL
    if row_count < MEDIUM_MAX_ROWS:
        return TIER_MEDIUM
    return TIER_LARGE


def delivery_for_tier(tier: str) -> dict[str, bool]:
    """Which delivery surfaces a tier may use (Justynn's matrix)."""
    return dict(DELIVERY_MATRIX[tier])


def excel_fee_cents(tier: str) -> int:
    """One-time download fee for a tier, in cents."""
    return EXCEL_FEE_CENTS[tier]


# ------------------------------------------------------- subscription gate
def subscription_status(db: Any, org_id: str | None) -> str | None:
    """Return the org's subscription_status, or None if unknown.

    db is a supabase-like client (or a test double exposing .table()).
    """
    if not org_id or org_id in _UNSCOPED_ORGS:
        return None
    try:
        resp = (
            db.table("organizations")
            .select("subscription_status")
            .eq("id", org_id)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if not rows:
            return None
        return rows[0].get("subscription_status")
    except Exception as exc:  # TW-208: lookup failure -> closed gate, logged
        log.warning("subscription lookup failed for org %s: %s", org_id, exc)
        return None


def require_active_subscription(db: Any, org_id: str | None) -> str:
    """Paywall gate: every science read needs an active Twistor subscription.

    Raises 402 Payment Required otherwise. Returns the org_id on success.
    """
    status = subscription_status(db, org_id)
    if status != "active":
        raise HTTPException(
            status_code=402,
            detail=(
                "Twistor subscription required: this dataset is paywalled. "
                "Activate a subscription, then retry."
            ),
        )
    return org_id or ""


# ------------------------------------------------- dynamic derivative pricing
def trailing_science_spend_cents(db: Any, org_id: str, days: int = 30) -> int:
    """Trailing-N-day science toll spend for an org, in cents."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        resp = (
            db.table("toll_events")
            .select("amount_cents")
            .eq("org_id", org_id)
            .gte("occurred_at", since)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return sum(int(r.get("amount_cents") or 0) for r in rows)
    except Exception as exc:  # TW-208: pricing lookup never breaks serving
        log.warning("volume pricing lookup failed for org %s: %s", org_id, exc)
        return 0


def volume_rate_multiplier(db: Any, org_id: str) -> float:
    """Dynamic derivative: per-unit rate multiplier from trailing volume."""
    spend = trailing_science_spend_cents(db, org_id)
    for floor, mult in VOLUME_TIERS:
        if spend >= floor:
            return mult
    return 1.0


def metered_toll(
    db: Any,
    org_id: str,
    event_type: str,
    request_id: str,
    quantity: float = 1.0,
    source_type: str | None = None,
    source_id: str | None = None,
) -> dict | None:
    """Emit a toll event with dynamic derivative pricing applied.

    amount_cents = quantity * TOLL_RATES[event_type] * volume_multiplier.
    Unknown event types and toll failures are logged and swallowed (TW-208):
    metering never breaks serving. Returns the toll row or None.
    """
    from app.config import TOLL_RATES
    from app.services import toll_service

    base = TOLL_RATES.get(event_type)
    if base is None:
        log.warning("metered_toll: unknown event type %s; skipping", event_type)
        return None
    multiplier = volume_rate_multiplier(db, org_id)
    amount = int(round(quantity * base * multiplier))
    try:
        return toll_service.record_toll_event(
            db,
            org_id=org_id,
            event_type=event_type,
            quantity=quantity,
            amount_cents=amount,
            idempotency_key=f"sci:{request_id}",
            source_type=source_type,
            source_id=source_id,
        )
    except Exception as exc:  # TW-208
        log.warning("metered_toll emit failed (%s); continuing", exc)
        return None


def emit_download_toll(
    db: Any,
    org_id: str,
    request_id: str,
    tier: str,
    dataset_version_id: str,
) -> dict | None:
    """One-time Excel download fee, ON TOP of the subscription.

    The fee is passed as amount_cents (tier-priced); no TOLL_RATES entry is
    needed because the toll service accepts explicit amounts.
    """
    from app.services import toll_service

    fee = excel_fee_cents(tier)
    try:
        return toll_service.record_toll_event(
            db,
            org_id=org_id,
            event_type=DOWNLOAD_EVENT,
            quantity=1.0,
            amount_cents=fee,
            idempotency_key=f"sci:download:{request_id}",
            source_type="sci_dataset_versions",
            source_id=dataset_version_id,
        )
    except Exception as exc:  # TW-208
        log.warning("download toll emit failed (%s); continuing", exc)
        return None


# ------------------------------------------------------------------ Excel
def build_excel_bytes(
    rows: list[dict[str, Any]],
    columns: list[str],
    dataset_name: str,
    semver: str,
    tier: str,
    fee_cents: int,
    synthetic: bool,
) -> bytes:
    """Build an .xlsx workbook: DATA sheet + README provenance sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "DATA"
    header_font = Font(bold=True)
    for col, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.font = header_font
    for r, row in enumerate(rows, start=2):
        for col, name in enumerate(columns, start=1):
            value = row.get(name)
            if isinstance(value, (dict, list)):
                value = __import__("json").dumps(value, default=str)
            ws.cell(row=r, column=col, value=value)
    ws.freeze_panes = "A2"
    # README: provenance + paywall receipt
    readme = wb.create_sheet("README")
    lines = [
        ("Dataset", dataset_name),
        ("Version", semver),
        ("Size tier", tier),
        ("Rows", len(rows)),
        ("Downloaded (UTC)", datetime.now(timezone.utc).isoformat()),
        ("One-time download fee", f"${fee_cents / 100:.2f}"),
        ("Synthetic seed", "YES - all rows synthetic" if synthetic else "NO"),
        ("Provenance", "Every row traces to bronze artifact -> lab -> calibration. See API /query/{id}/provenance."),
        ("License", "Twistor subscription + per-download fee. Redistribution prohibited."),
    ]
    for r, (k, v) in enumerate(lines, start=1):
        readme.cell(row=r, column=1, value=k).font = header_font
        readme.cell(row=r, column=2, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def excel_response(
    content: bytes,
    dataset_name: str,
    semver: str,
) -> StreamingResponse:
    """Streaming .xlsx download with a content-disposition filename."""
    filename = f"{dataset_name}-{semver}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def check_excel_delivery(tier: str, row_count: int) -> None:
    """Enforce the delivery matrix: large datasets are API-only.

    Raises 403 if Excel is not a permitted surface for the tier.
    """
    if not delivery_for_tier(tier)["excel"]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This dataset is tier '{tier}' ({row_count:,} rows): "
                "large datasets are API-only. Query /query or use bulk export."
            ),
        )

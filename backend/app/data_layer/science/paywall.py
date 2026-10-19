# Prepared by Twistor Holdings LLC
"""TW-606 science paywall: subscription gate + dynamic volume pricing curve.

Delivery doctrine (Justynn 2026-10-18, LAW for scientific datasets):
  - large datasets  -> API only
  - medium datasets -> API + downloadable Excel
  - small datasets  -> downloadable Excel, API if needed
  - EVERYTHING behind the paywall: an active Twistor subscription is required
    for every read; Excel downloads carry a one-time fee ON TOP of the
    subscription (subscription + per-download fee).

Dynamic volume pricing (TW-606, REVISION 3 2026-10-18: pennies):
  - Excel download AND API-call price scales with the dataset's CUMULATIVE
    download count:
      price(d) = floor + (cap - floor) * (d / scale) ** exponent, hard-capped.
    The floor is per-dataset, set by its asset-value tier at launch
    (standard $0.01, premium $0.05, flagship $0.09). Defaults: $0.01 at
    0 downloads -> $0.10 HARD CAP at 100,000 downloads. Everything costs
    pennies -- no more than $0.10 per download or per API call, ever.
  - API micro-tolls are ECONOMICALLY NEUTRAL with the download price: the
    per-row toll is set so that pulling an entire dataset via API converges
    to approximately the current download price. The researcher's choice
    between API and download is neutral -- the toll is for access pattern,
    not a discount or penalty.
  - Rate-limit tiers are priced off the same curve: higher limits cost more.
  - Signup question ("researcher, student, or other") is kept for AUDIENCE
    TRACKING only -- per-transaction pricing is pennies for everyone.
    The subscription is still required.

Dynamic derivative pricing (org-level volume discount, unchanged):
  the per-unit toll rate is multiplied by a volume factor derived from the
  org's trailing-30-day science toll spend. Rates fall as volume rises:
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

# Delivery matrix: which surfaces each tier may use.
DELIVERY_MATRIX = {
    TIER_SMALL: {"api": True, "excel": True},
    TIER_MEDIUM: {"api": True, "excel": True},
    TIER_LARGE: {"api": True, "excel": False},  # large: API only
}

# ------------------------------------------- dynamic volume pricing (TW-606)
# Excel download price scales with the dataset's CUMULATIVE download count:
#   price(d) = floor + (cap - floor) * (d / scale) ** exponent, hard-capped.
# The floor is per-dataset (asset-value tier, set at launch); the curve runs
# from the dataset's floor up to the cap across 0..scale downloads.
# Defaults: $0.01 at 0 downloads -> $0.10 HARD CAP at 100,000 downloads.
# Everything costs pennies: no more than $0.10 per download or API call, ever.
DOWNLOAD_CURVE_SCALE_DOWNLOADS = 100_000
DOWNLOAD_CURVE_EXPONENT = 1.5
DOWNLOAD_CURVE_CAP_CENTS = 10  # hard cap $0.10 -- pennies, no exceptions

# Asset-value tiers: the launch-time value assessment that sets a dataset's
# price floor. All floors are pennies under the $0.10 cap. Configurable,
# not hardcoded.
ASSET_VALUE_TIERS = {
    "standard": 1,  # $0.01 -- default
    "premium": 5,  # $0.05
    "flagship": 9,  # $0.09
}
DEFAULT_ASSET_VALUE_TIER = "standard"


def floor_for_value_tier(tier: str) -> int:
    """Price floor in cents for a named asset-value tier."""
    return ASSET_VALUE_TIERS.get(tier, ASSET_VALUE_TIERS[DEFAULT_ASSET_VALUE_TIER])


def download_price_cents(
    download_count: int,
    floor_cents: int = 1,
    *,
    scale: int = DOWNLOAD_CURVE_SCALE_DOWNLOADS,
    exponent: float = DOWNLOAD_CURVE_EXPONENT,
    cap_cents: int = DOWNLOAD_CURVE_CAP_CENTS,
) -> int:
    """Dynamic volume price for a dataset download, in cents.

    price(d) = floor + (cap - floor) * (d / scale) ** exponent, capped.
    The exponent, scale, and cap are keyword-configurable, not hardcoded.
    The floor is CLAMPED to the cap: a misconfigured/stale floor above the
    cap can never resurrect a higher price (Justynn's rule: hard $0.10 cap,
    no exceptions). Raises ValueError on nonsense config (non-positive
    scale, negative exponent) -- fail fast on programmer error in a money
    function.
    """
    if scale <= 0:
        raise ValueError(f"download curve scale must be positive, got {scale}")
    if exponent < 0:
        raise ValueError(f"download curve exponent must be >= 0, got {exponent}")
    d = max(0, int(download_count))
    cap = max(1, int(cap_cents))
    floor = min(max(1, int(floor_cents)), cap)
    growth = (cap - floor) * (d / scale) ** float(exponent)
    return min(int(round(floor + growth)), cap)


# --------------------------------------------- audience tracking (TW-606)
# The signup/purchase question ("Are you a researcher, student, or other?")
# is kept for AUDIENCE TRACKING ONLY. Per-transaction pricing is pennies
# for everyone -- the old tiered entry fees ($5.99/$10.99) were scratched
# (TW-606 Revision 3: too inflated for a single data pull).
AUDIENCE_CATEGORIES = ("student", "researcher", "other")
DEFAULT_AUDIENCE_CATEGORY = "other"


def org_customer_tier(db: Any, org_id: str | None) -> str:
    """The org's audience category; defaults to 'other'.

    Set by the signup/purchase question ("researcher, student, or other").
    Audience tracking only -- it no longer affects pricing. Reads
    organizations.customer_tier when the column exists; graceful default
    otherwise.
    """
    if not org_id or org_id in _UNSCOPED_ORGS:
        return DEFAULT_AUDIENCE_CATEGORY
    try:
        resp = (
            db.table("organizations").select("customer_tier").eq("id", org_id).execute()
        )
        rows = getattr(resp, "data", None) or []
        if not rows:
            return DEFAULT_AUDIENCE_CATEGORY
        tier = rows[0].get("customer_tier") or DEFAULT_AUDIENCE_CATEGORY
        return tier if tier in AUDIENCE_CATEGORIES else DEFAULT_AUDIENCE_CATEGORY
    except Exception:  # TW-208: column missing or lookup down -> default
        return DEFAULT_AUDIENCE_CATEGORY


def download_fee_cents(db: Any, org_id: str | None, dataset_version_id: str) -> int:
    """One-time Excel download fee for this dataset, in cents.

    TW-606 Revision 3: the fee IS the dataset's live curve price -- pennies,
    hard-capped at $0.10. The tiered entry fees are gone; per-transaction
    pricing is the same for every audience category. org_id is kept for
    signature compatibility (audience tracking) but does not affect price.
    """
    return dataset_download_price_cents(db, dataset_version_id)


# Volume-discount derivative tiers: (spend_cents_floor, multiplier).
VOLUME_TIERS = (
    (100_000, 0.70),  # > $1,000 / 30d
    (20_000, 0.80),  # > $200
    (5_000, 0.90),  # > $50
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


# -------------------------------------------- download ledger (TW-606)
def cumulative_downloads(db: Any, dataset_version_id: str) -> int:
    """Cumulative Excel downloads for a dataset version, from the toll ledger.

    Every download emits a DOWNLOAD_EVENT toll with source_id = version id,
    so the ledger is the source of truth for the volume curve. Prefers a
    server-side count (no row fetch -- this runs on the query hot path);
    falls back to counting fetched rows on shims without count support.
    """
    try:
        resp = (
            db.table("toll_events")
            .select("id", count="exact", head=True)
            .eq("event_type", DOWNLOAD_EVENT)
            .eq("source_id", dataset_version_id)
            .execute()
        )
        count = getattr(resp, "count", None)
        if count is not None:
            return int(count)
        rows = getattr(resp, "data", None) or []
        return len(rows)
    except Exception as exc:  # TW-208: pricing lookup never breaks serving
        log.warning("download count lookup failed for %s: %s", dataset_version_id, exc)
        return 0


def version_price_floor_cents(version_row: dict | None) -> int:
    """Per-dataset price floor from the version row's asset-value tier."""
    if not version_row:
        return ASSET_VALUE_TIERS[DEFAULT_ASSET_VALUE_TIER]
    try:
        return max(
            1,
            int(version_row.get("price_floor_cents") or 0)
            or ASSET_VALUE_TIERS[DEFAULT_ASSET_VALUE_TIER],
        )
    except Exception:
        return ASSET_VALUE_TIERS[DEFAULT_ASSET_VALUE_TIER]


def dataset_download_price_cents(
    db: Any, dataset_version_id: str, version: dict | None = None
) -> int:
    """Current Excel download price for a dataset version, in cents.

    Evaluates the dynamic volume curve at the version's cumulative download
    count, starting from its per-dataset asset-value floor. Pass a resolved
    version row to skip the re-lookup on hot paths.
    """
    if version is None:
        version = latest_lane_version_by_id(db, dataset_version_id)
    floor = version_price_floor_cents(version)
    return download_price_cents(
        cumulative_downloads(db, dataset_version_id), floor_cents=floor
    )


def latest_lane_version(db: Any, lane: str) -> dict | None:
    """Latest released dataset version row for a lane, or None."""
    try:
        resp = (
            db.table("sci_dataset_versions")
            .select("*")
            .eq("lane", lane)
            .order("released_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return dict(rows[0]) if rows else None
    except Exception as exc:  # TW-208
        log.warning("lane version lookup failed for %s: %s", lane, exc)
        return None


def latest_lane_version_by_id(db: Any, dataset_version_id: str) -> dict | None:
    """Dataset version row by id, or None."""
    try:
        resp = (
            db.table("sci_dataset_versions")
            .select("*")
            .eq("id", dataset_version_id)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return dict(rows[0]) if rows else None
    except Exception as exc:  # TW-208
        log.warning("version lookup failed for %s: %s", dataset_version_id, exc)
        return None


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
    dataset_version_id: str,
) -> dict | None:
    """One-time Excel download fee, ON TOP of the subscription.

    TW-606 Revision 3: the fee is the dataset's live curve price -- pennies,
    hard-capped at $0.10 per download. No tiered entry fees. Passed as
    amount_cents; no TOLL_RATES entry needed because the toll service
    accepts explicit amounts.
    """
    fee = download_fee_cents(db, org_id, dataset_version_id)
    try:
        from app.services import toll_service

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


# -------------------------------------- neutral API tolls (TW-606)
# The per-row API toll is set so that pulling an entire dataset via API
# converges to approximately the dataset's current CURVE price
# (download_price_cents at its cumulative download count). The toll is for
# access pattern, not a discount or penalty.
# RESOLVED (TW-606 Revision 3, Justynn's call): the charged Excel download
# fee IS the curve price -- full API-vs-download economic neutrality holds.
# Both surfaces cost pennies, hard-capped at $0.10 per download or call.
API_ROWS_EVENT = "data.science.api_rows"


def neutral_per_row_rate_cents(
    db: Any,
    dataset_version_id: str,
    row_count_total: int,
    version: dict | None = None,
) -> float:
    """Neutral per-row API rate in cents: download_price / total_rows.

    A full-dataset pull (row_count_total rows served) converges to the
    current download price. Uses the curve price so the rate reflects the
    dataset's market value. Pass a resolved version row to skip re-lookup.
    """
    total = max(1, int(row_count_total))
    price = dataset_download_price_cents(db, dataset_version_id, version=version)
    return price / total


def emit_neutral_data_toll(
    db: Any,
    org_id: str,
    request_id: str,
    lane: str,
    rows_served: int,
    source_type: str | None = None,
    dataset_version_id: str | None = None,
) -> dict | None:
    """Neutral data-query toll for a lane: rows_served x per-row rate.

    Resolves the lane's latest dataset version for the current curve price
    and total row count (or uses dataset_version_id when the caller already
    resolved it). Falls back to the legacy flat query toll when no version
    row is resolvable (tests, unmigrated DB). TW-208: never breaks serving;
    failures are logged and swallowed.
    """
    try:
        version = None
        if dataset_version_id:
            version = latest_lane_version_by_id(db, dataset_version_id)
        if version is None:
            version = latest_lane_version(db, lane)
        if version is None or int(version.get("row_count") or 0) <= 0:
            return metered_toll(
                db, org_id, "data.science.query", request_id, source_type=source_type
            )
        served = max(0, int(rows_served))
        if served == 0:
            return None
        # version already resolved above: thread it through (no re-lookup).
        rate = neutral_per_row_rate_cents(
            db, version["id"], int(version["row_count"]), version=version
        )
        mult = volume_rate_multiplier(db, org_id)
        amount = int(round(served * rate * mult))
        from app.services import toll_service

        return toll_service.record_toll_event(
            db,
            org_id=org_id,
            event_type=API_ROWS_EVENT,
            quantity=float(served),
            amount_cents=amount,
            idempotency_key=f"sci:api_rows:{request_id}",
            source_type=source_type or "sci_dataset_versions",
            source_id=version["id"],
        )
    except Exception as exc:  # TW-208
        log.warning("neutral api toll emit failed (%s); continuing", exc)
        return None


# --------------------------------------- rate-limit tiers (TW-606)
# Higher rate limits cost more; tier pricing is tied to the same volume
# curve -- every tier moves with the dataset's cumulative downloads.
# Fees are monthly recurring, priced off the curve at the reference count
# (the dataset's cumulative download count at pricing time).
#
# STATUS: pricing schedule DEFINED here, but billing + enforcement are NOT
# wired yet -- no production callers of rate_limit_tier_fee_cents and no
# rate-limit enforcement reads org_rate_limit_tier. Do not present these
# as live until the TW-606 follow-up wires them.
RATE_LIMIT_TIERS: dict[str, dict] = {
    "standard": {"rpm": 60, "price_factor": 1.0, "label": "Standard: 60 req/min"},
    "pro": {"rpm": 600, "price_factor": 5.0, "label": "Pro: 600 req/min"},
    "scale": {"rpm": 6000, "price_factor": 20.0, "label": "Scale: 6,000 req/min"},
}
DEFAULT_RATE_LIMIT_TIER = "standard"


def rate_limit_tier_fee_cents(
    download_count: int, tier: str = DEFAULT_RATE_LIMIT_TIER
) -> int:
    """Monthly fee for a rate-limit tier, in cents.

    Evaluates the download volume curve at the reference count (the
    dataset's cumulative download count at pricing time), scaled by the
    tier's price factor. Higher limits cost more; all tiers move with
    the same curve.

    NOTE: pricing schedule only -- billing/enforcement not wired yet.
    """
    spec = RATE_LIMIT_TIERS.get(tier, RATE_LIMIT_TIERS[DEFAULT_RATE_LIMIT_TIER])
    return int(
        round(download_price_cents(download_count) * float(spec["price_factor"]))
    )


def org_rate_limit_tier(db: Any, org_id: str | None) -> str:
    """The org's rate-limit tier name; defaults to standard.

    Reads organizations.rate_limit_tier when the column exists; graceful
    default otherwise. Enforcement wiring is a follow-up (TW-606).
    """
    if not org_id or org_id in _UNSCOPED_ORGS:
        return DEFAULT_RATE_LIMIT_TIER
    try:
        resp = (
            db.table("organizations")
            .select("rate_limit_tier")
            .eq("id", org_id)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if not rows:
            return DEFAULT_RATE_LIMIT_TIER
        tier = rows[0].get("rate_limit_tier") or DEFAULT_RATE_LIMIT_TIER
        return tier if tier in RATE_LIMIT_TIERS else DEFAULT_RATE_LIMIT_TIER
    except Exception:  # TW-208: column missing or lookup down
        return DEFAULT_RATE_LIMIT_TIER


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
        (
            "Provenance",
            "Every row traces to bronze artifact -> lab -> calibration. See API /query/{id}/provenance.",
        ),
        (
            "License",
            "Twistor subscription + per-download fee. Redistribution prohibited.",
        ),
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

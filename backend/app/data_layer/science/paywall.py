# Prepared by Twistor Holdings LLC
"""TW-606 science paywall: subscription gate + dynamic volume pricing curve.

Delivery doctrine (Justynn 2026-10-18, LAW for scientific datasets):
  - large datasets  -> API only
  - medium datasets -> API + downloadable Excel
  - small datasets  -> downloadable Excel, API if needed
  - EVERYTHING behind the paywall: an active Twistor subscription is required
    for every read; Excel downloads carry a one-time fee ON TOP of the
    subscription (subscription + per-download fee).

Dynamic volume pricing (TW-606, supersedes the flat $6/$8.50/$22.50 tiers):
  - Excel download price scales with the dataset's CUMULATIVE download count:
      price(d) = floor + (cap - floor) * (d / scale) ** exponent, hard-capped.
    The floor is per-dataset, set by its asset-value tier at launch
    (standard $0.01, premium/flagship higher). Defaults: floor $0.01 at
    0 downloads -> $59.99 at 100,000 downloads. Cheap while unproven,
    meaningful at scale, never unaffordable.
  - API micro-tolls are ECONOMICALLY NEUTRAL with the download price: the
    per-row toll is set so that pulling an entire dataset via API converges
    to approximately the current download price. The researcher's choice
    between API and download is neutral -- the toll is for access pattern,
    not a discount or penalty.
  - Rate-limit tiers are priced off the same curve: higher limits cost more.
  - Customer tiers: the signup question ("researcher, student, or other")
    sets the download entry fee -- student $5.99, researcher $10.99,
    other $10.99. All tiers then pay the same per-request API tolling at
    the dataset's live curve price. The subscription is still required.

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
# Defaults: $0.01 at 0 downloads -> $59.99 at 100,000 downloads.
DOWNLOAD_CURVE_SPAN_CENTS = 5998  # +$59.98 of growth above the floor
DOWNLOAD_CURVE_SCALE_DOWNLOADS = 100_000
DOWNLOAD_CURVE_EXPONENT = 1.5
DOWNLOAD_CURVE_CAP_CENTS = 5999  # hard cap $59.99 -- never unaffordable

# Asset-value tiers: the launch-time value assessment that sets a dataset's
# price floor. Standard datasets floor at $0.01; more valuable datasets
# floor higher. Configurable, not hardcoded.
ASSET_VALUE_TIERS = {
    "standard": 1,  # $0.01 -- default
    "premium": 999,  # $9.99
    "flagship": 2999,  # $29.99
}
DEFAULT_ASSET_VALUE_TIER = "standard"


def floor_for_value_tier(tier: str) -> int:
    """Price floor in cents for a named asset-value tier."""
    return ASSET_VALUE_TIERS.get(tier, ASSET_VALUE_TIERS[DEFAULT_ASSET_VALUE_TIER])


def download_price_cents(
    download_count: int,
    floor_cents: int = 1,
    *,
    span_cents: int = DOWNLOAD_CURVE_SPAN_CENTS,
    scale: int = DOWNLOAD_CURVE_SCALE_DOWNLOADS,
    exponent: float = DOWNLOAD_CURVE_EXPONENT,
    cap_cents: int = DOWNLOAD_CURVE_CAP_CENTS,
) -> int:
    """Dynamic volume price for a dataset download, in cents.

    price(d) = floor + (cap - floor) * (d / scale) ** exponent, capped.
    The exponent and cap are keyword-configurable, not hardcoded magic.
    """
    d = max(0, int(download_count))
    floor = max(1, int(floor_cents))
    cap = max(floor, int(cap_cents))
    growth = (cap - floor) * (d / scale) ** float(exponent)
    return min(int(round(floor + growth)), cap)


# --------------------------------------------- customer tiers (TW-606)
# Entry fees are gated behind a signup/purchase question:
#   "Are you a researcher, student, or other?"
# The tier sets the download entry fee; ALL tiers then pay the same
# per-request API tolling at the dataset's live curve price.
# Configurable tier definitions, not hardcoded.
CUSTOMER_TIERS: dict[str, dict] = {
    "student": {"entry_fee_cents": 599, "label": "Student: $5.99 entry"},
    "researcher": {
        "entry_fee_cents": 1099,
        "label": "Researcher: $10.99 entry (incl. company researchers)",
    },
    "other": {"entry_fee_cents": 1099, "label": "Other: $10.99 entry"},
}
DEFAULT_CUSTOMER_TIER = "other"


def org_customer_tier(db: Any, org_id: str | None) -> str:
    """The org's customer tier; defaults to 'other'.

    Set by the signup/purchase question ("researcher, student, or other").
    Reads organizations.customer_tier when the column exists; graceful
    default otherwise. The subscription gate is unchanged -- the tier only
    prices the entry fee.
    """
    if not org_id or org_id in _UNSCOPED_ORGS:
        return DEFAULT_CUSTOMER_TIER
    try:
        resp = (
            db.table("organizations").select("customer_tier").eq("id", org_id).execute()
        )
        rows = getattr(resp, "data", None) or []
        if not rows:
            return DEFAULT_CUSTOMER_TIER
        tier = rows[0].get("customer_tier") or DEFAULT_CUSTOMER_TIER
        return tier if tier in CUSTOMER_TIERS else DEFAULT_CUSTOMER_TIER
    except Exception:  # TW-208: column missing or lookup down -> default tier
        return DEFAULT_CUSTOMER_TIER


def customer_entry_fee_cents(db: Any, org_id: str | None) -> int:
    """Entry fee for the org's customer tier, in cents."""
    tier = org_customer_tier(db, org_id)
    return int(CUSTOMER_TIERS[tier]["entry_fee_cents"])


def download_fee_cents(db: Any, org_id: str | None, dataset_version_id: str) -> int:
    """One-time Excel download fee for this org + dataset, in cents.

    The fee is the org's tiered entry fee ($5.99 student / $10.99
    researcher+other), set by the signup question. Per-request API tolling
    for all tiers uses the dataset's live curve price (neutral rate).
    """
    return customer_entry_fee_cents(db, org_id)


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
    so the ledger is the source of truth for the volume curve.
    """
    try:
        resp = (
            db.table("toll_events")
            .select("id")
            .eq("event_type", DOWNLOAD_EVENT)
            .eq("source_id", dataset_version_id)
            .execute()
        )
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


def dataset_download_price_cents(db: Any, dataset_version_id: str) -> int:
    """Current Excel download price for a dataset version, in cents.

    Evaluates the dynamic volume curve at the version's cumulative download
    count, starting from its per-dataset asset-value floor.
    """
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

    The fee is the org-aware download price: university-tier orgs pay the
    flat academic fee, everyone else pays the dataset's dynamic curve price
    at its current cumulative download count. Passed as amount_cents; no
    TOLL_RATES entry needed because the toll service accepts explicit amounts.
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
# Economic neutrality: the per-row API toll is set so that pulling an entire
# dataset via API converges to approximately the dataset's current download
# price. The researcher's choice between API and download is neutral -- the
# toll is for access pattern, not a discount or penalty.
API_ROWS_EVENT = "data.science.api_rows"


def neutral_per_row_rate_cents(
    db: Any, dataset_version_id: str, row_count_total: int
) -> float:
    """Neutral per-row API rate in cents: download_price / total_rows.

    A full-dataset pull (row_count_total rows served) converges to the
    current download price. Uses the standard curve price (not the
    university flat fee) so the rate reflects the dataset's market value.
    """
    total = max(1, int(row_count_total))
    price = dataset_download_price_cents(db, dataset_version_id)
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
        rate = neutral_per_row_rate_cents(db, version["id"], int(version["row_count"]))
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
# Fees are monthly recurring, priced off the curve at the reference count.
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

    Evaluates the download volume curve at the reference count, scaled by
    the tier's price factor. Higher limits cost more; all tiers move with
    the same curve.
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


# ------------------------------------------- usage metering (TW-607)
# Claude Code end-of-message style: every science API response carries a
# usage block so the researcher sees exactly what each transaction consumed
# and cost. ONE SOURCE OF TRUTH: everything is read from the toll ledger
# (toll_events) -- the toll for this call is looked up by idempotency key,
# the running total is the ledger's trailing-30d science spend. Nothing is
# recomputed from pricing formulas, so the readout can never drift from
# what was actually charged.
#
# Shape (JSON):
#   usage: {
#     "records": 100,                  # rows/records returned by this call
#     "toll_cents": 42,                # toll charged for this call
#     "toll": "$0.42",                 # human-readable
#     "period": "trailing_30d",        # the running-total window
#     "period_total_cents": 1250,      # caller's running total, ledger-read
#     "period_total": "$12.50",
#     "rate_limit_tier": "standard",   # the caller's tier + allowance
#     "rate_limit_rpm": 60,
#   }
# Headers (for programmatic clients):
#   X-Twistor-Toll: $0.42
#   X-Twistor-Usage: {"records":100,"toll_cents":42,"period_total_cents":1250}

# Idempotency-key patterns every science toll is emitted under. The lookup
# covers flat query tolls (sci:{rid}), neutral row tolls (sci:api_rows:{rid}),
# and download tolls (sci:download:{rid} / sci:{rid}:download).
USAGE_TOLL_KEY_PATTERNS = (
    "sci:api_rows:{rid}",
    "sci:{rid}",
    "sci:download:{rid}",
    "sci:{rid}:download",
)


def _dollars(cents: int) -> str:
    """Human-readable dollars, e.g. $0.42."""
    return f"${cents / 100:,.2f}"


def lookup_request_tolls(db: Any, request_id: str) -> list[dict]:
    """Toll rows for one request, read from the toll ledger by idempotency key.

    This is the one-source-of-truth read: the amounts here are what was
    actually charged, never a recomputation. Fail-soft (TW-208): a lookup
    failure returns [] and the usage block still renders with toll_cents 0.
    """
    rows: list[dict] = []
    if not request_id:
        return rows
    try:
        for pattern in USAGE_TOLL_KEY_PATTERNS:
            key = pattern.format(rid=request_id)
            resp = (
                db.table("toll_events")
                .select("amount_cents,idempotency_key,event_type")
                .eq("idempotency_key", key)
                .execute()
            )
            rows.extend(getattr(resp, "data", None) or [])
    except Exception as exc:  # TW-208: metering never breaks serving
        log.warning("usage toll lookup failed for %s: %s", request_id, exc)
    return rows


def build_usage_block(
    db: Any,
    org_id: str | None,
    *,
    request_id: str,
    records: int = 0,
) -> dict[str, Any]:
    """Build the EOM usage block for one science API call.

    toll_cents is the sum of this request's toll rows from the ledger;
    period_total_cents is the org's trailing-30d science spend (also ledger);
    the rate-limit tier is the org's configured tier. All fail-soft.
    """
    toll_rows = lookup_request_tolls(db, request_id)
    toll_cents = sum(int(r.get("amount_cents") or 0) for r in toll_rows)
    period_total_cents = (
        trailing_science_spend_cents(db, org_id) if org_id else 0
    )
    tier = org_rate_limit_tier(db, org_id)
    rpm = int(RATE_LIMIT_TIERS.get(tier, {}).get("rpm") or 60)
    return {
        "records": max(0, int(records)),
        "toll_cents": toll_cents,
        "toll": _dollars(toll_cents),
        "period": "trailing_30d",
        "period_total_cents": period_total_cents,
        "period_total": _dollars(period_total_cents),
        "rate_limit_tier": tier,
        "rate_limit_rpm": rpm,
    }


def _count_records(result: Any) -> int:
    """Rows/records served by a lane response, across response shapes."""
    try:
        data = getattr(result, "data", None)
        if data is None and isinstance(result, dict):
            data = result.get("data")
        if data is None and isinstance(result, (list, tuple)):
            return len(result)
        if isinstance(data, (list, tuple)):
            return len(data)
        if isinstance(data, dict):
            return 1
        return 0
    except Exception:
        return 0


def set_usage_headers(response: Any, usage: dict[str, Any]) -> None:
    """Stamp X-Twistor-Toll / X-Twistor-Usage on a FastAPI response.

    X-Twistor-Toll is the human-readable charge for this call; X-Twistor-Usage
    is a compact JSON summary for programmatic clients. No-op when usage is
    empty or the response object has no headers mapping.
    """
    if not usage:
        return
    try:
        import json as _json

        headers = getattr(response, "headers", None)
        if headers is None:
            return
        headers["X-Twistor-Toll"] = str(usage.get("toll", "$0.00"))
        headers["X-Twistor-Usage"] = _json.dumps(
            {
                "records": usage.get("records", 0),
                "toll_cents": usage.get("toll_cents", 0),
                "period_total_cents": usage.get("period_total_cents", 0),
            },
            separators=(",", ":"),
        )
    except Exception as exc:  # TW-208: header stamping never breaks serving
        log.warning("usage header stamp failed: %s", exc)


def finalize(
    result: Any,
    db: Any,
    org_id: str | None,
    request_id: str,
    response: Any = None,
) -> Any:
    """Attach the EOM usage block to a science lane response (TW-607).

    Reads the toll ledger (one source of truth), builds the usage block,
    attaches it to the response body (ScienceEnvelope.usage or result["usage"];
    JSON arrays get headers only), and stamps X-Twistor-* headers when a
    response object is provided. Returns the result unchanged otherwise.
    """
    try:
        usage = build_usage_block(
            db, org_id, request_id=request_id, records=_count_records(result)
        )
    except Exception as exc:  # TW-208
        log.warning("usage block build failed: %s", exc)
        return result
    try:
        if hasattr(result, "usage"):
            result.usage = usage  # envelope lanes (field exists, serializes)
        elif isinstance(result, dict):
            result["usage"] = usage
        elif hasattr(result, "model_dump"):
            # Other pydantic lane responses: convert to dict so the usage
            # block serializes. (Typed response_model endpoints strip it --
            # those lanes get headers only; see evidence doc.)
            result = result.model_dump(mode="json")
            result["usage"] = usage
        # lists/tuples: body untouched (can't key a JSON array); headers carry it
    except Exception as exc:
        log.warning("usage body attach failed: %s", exc)
    if response is not None:
        set_usage_headers(response, usage)
    return result

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

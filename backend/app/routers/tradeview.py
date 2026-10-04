"""Trade View real-backend API (TW-301).

Pins, dossiers, diagnostics, and layers served from real pipeline output
(customers, leads, leak-engine brief) instead of fictional sample data.

Auth + consent posture:
- Every endpoint requires a valid auth token (any plan) and serves ONLY
  the caller's own org_id. A cross-org id degrades to 404 — never leaks.
- This serves an org's OWN connected data back to its OWN signed-in
  members (the product's normal function). Cohort/benchmark recording
  stays consent-gated exactly as before; nothing here opts anyone in.
- Signed-out visitors keep the clearly-labeled sample experience (TW-295):
  the frontend only calls these endpoints when signed in.

Self-healing (TW-208): one bad row or one failed geocode never kills the
endpoint — failures degrade to honest unlocated counts / empty states.
"""

import math
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..leak_engine import run_leak_scan
from ..limiter import enforce_user_limit
from ..services.geocode import geocode_cached

router = APIRouter(prefix="/tradeview", tags=["tradeview"])

# NIT 2 (Rosa, TW-301): single enforcement via the spoof-proof per-user
# limiter (TW-078) — the IP-keyed slowapi layer was redundant on these
# auth-required endpoints. 120/hour fits interactive map use (pins load
# once per view; dossier/diagnostics fire per click).
_TRADEVIEW_RATE_LIMIT = "120/hour"


class _DBUnavailable(Exception):
    """The org's data tables could not be read at all."""


def _rows(db, table: str, org_id: str) -> list[dict[str, Any]]:
    """Fetch an org's rows. MAJOR 1 (Rosa, TW-301): a TOTAL table failure
    must raise — never return [] — so the endpoint answers non-200 and the
    frontend .catch() engages the TW-295 sample fallback instead of
    rendering an empty map with a Live badge. Per-row isolation lives in
    the pins loop below, not here."""
    try:
        return db.table(table).select("*").eq("org_id", org_id).execute().data or []
    except Exception as e:
        raise _DBUnavailable(table) from e


def _one(db, table: str, org_id: str, entity_id: str) -> dict[str, Any] | None:
    for row in _rows(db, table, org_id):
        if str(row.get("id")) == str(entity_id):
            return row
    return None


class _GeocodeBudget:
    """Mutable per-request cap on live geocode attempts."""

    def __init__(self, attempts: int):
        self.attempts = attempts


# MAJOR 2: worst case per request ≈ 10 attempts × 2 providers × 2s timeout,
# and the typical case is far faster (cache hits + sub-second Photon).
# The remainder degrade to unlocated this round; the cache warms across
# requests.
_MAX_GEOCODE_ATTEMPTS_PER_REQUEST = 10
_PINS_GEOCODE_TIMEOUT_S = 2.0


def _pin_for(
    kind: str,
    row: dict[str, Any],
    budget: _GeocodeBudget,
    *,
    geocode_timeout: float = _PINS_GEOCODE_TIMEOUT_S,
) -> dict[str, Any]:
    """Build a map pin; geocoding failure degrades to located=False.

    MAJOR 2 (Rosa, TW-301): live geocode attempts are capped per request
    via `budget` — the remainder count as unlocated this round and the
    process cache warms across requests, so a big book can never hang the
    request past proxy timeouts."""
    address = (row.get("address") or "").strip() or None
    geo = None
    if address and budget.attempts > 0:
        budget.attempts -= 1
        geo = geocode_cached(address, timeout=geocode_timeout)
    pin: dict[str, Any] = {
        "id": row.get("id"),
        "kind": kind,
        "name": row.get("name"),
        "trade": row.get("trade") or row.get("service_interest"),
        "address": address,
        "located": geo is not None,
        "lat": geo.lat if geo else None,
        "lng": geo.lon if geo else None,
        "geocode_source": geo.source if geo else None,
    }
    return pin


# MINOR 2 (Rosa, TW-301): a full-org run_leak_scan per pin click is O(org)
# per click. Short-TTL memo per (org, db-instance): get_db() is a process
# singleton in production, so this is effectively per-org; test doubles get
# fresh entries via id(db). Failures are never cached.
_BRIEF_TTL_S = 60.0
_BRIEF_CACHE_MAX_ORGS = 64
_brief_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def _brief_for(db, org_id: str) -> dict[str, Any]:
    key = (org_id, id(db))
    now = time.monotonic()
    hit = _brief_cache.get(key)
    if hit is not None and now < hit[0]:
        return hit[1]
    brief = run_leak_scan(db, org_id)
    if len(_brief_cache) >= _BRIEF_CACHE_MAX_ORGS:
        oldest = min(_brief_cache, key=lambda k: _brief_cache[k][0])
        del _brief_cache[oldest]
    _brief_cache[key] = (now + _BRIEF_TTL_S, brief)
    return brief


def _findings_for_entity(db, org_id: str, entity_id: str) -> list[dict[str, Any]]:
    """Leak findings whose entities reference this id. Never raises."""
    try:
        brief = _brief_for(db, org_id)
    except Exception:
        return []
    matched = []
    for rung in ("what_happened", "what_will_happen", "what_should_we_do"):
        for f in brief.get(rung, []):
            for e in f.get("entities", []) or []:
                if str(e.get("id")) == str(entity_id):
                    matched.append(
                        {
                            "detector": f.get("detector"),
                            "title": f.get("title"),
                            "detail": f.get("detail"),
                            "severity": f.get("severity"),
                            "estimated_value": f.get("estimated_value"),
                            "recommended_action": f.get("recommended_action"),
                        }
                    )
                    break
    return matched


@router.get("/pins")
def tradeview_pins(
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit(_TRADEVIEW_RATE_LIMIT))],
):
    """Map pins from the org's real customers + leads.

    MAJOR 1: a total table failure answers 502 (never 200-empty) so the
    frontend falls back to the sample map. Per-row isolation is preserved
    below — one bad row or one failed geocode never kills the endpoint.
    """
    db = get_db()
    try:
        customer_rows = _rows(db, "customers", auth.org_id)
        lead_rows = _rows(db, "leads", auth.org_id)
    except _DBUnavailable:
        raise HTTPException(
            status_code=502,
            detail="Trade View data source unavailable — try again shortly.",
        )
    budget = _GeocodeBudget(_MAX_GEOCODE_ATTEMPTS_PER_REQUEST)
    pins: list[dict[str, Any]] = []
    unlocated = 0
    for row in customer_rows:
        try:
            pin = _pin_for("customer", row, budget)
        except Exception:
            unlocated += 1
            continue
        if pin["located"]:
            pins.append(pin)
        else:
            unlocated += 1
    for row in lead_rows:
        try:
            pin = _pin_for("lead", row, budget)
        except Exception:
            unlocated += 1
            continue
        if pin["located"]:
            pins.append(pin)
        else:
            unlocated += 1
    return {
        "source": "live",
        "org_id": auth.org_id,
        "pins": pins,
        "unlocated_count": unlocated,
        "records_total": len(customer_rows) + len(lead_rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/dossiers/{kind}/{entity_id}")
def tradeview_dossier(
    kind: str,
    entity_id: str,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit(_TRADEVIEW_RATE_LIMIT))],
):
    """Dossier for one customer or lead: facts + related leak findings."""
    if kind not in ("customer", "lead"):
        raise HTTPException(status_code=404, detail="unknown dossier kind")
    table = "customers" if kind == "customer" else "leads"
    try:
        row = _one(get_db(), table, auth.org_id, entity_id)
    except _DBUnavailable:
        raise HTTPException(
            status_code=502,
            detail="Trade View data source unavailable — try again shortly.",
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    facts = {
        "name": row.get("name"),
        "address": row.get("address"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "trade": row.get("trade") or row.get("service_interest"),
        "status": row.get("status"),
        "estimated_value": row.get("estimated_value"),
    }
    facts = {k: v for k, v in facts.items() if v is not None}
    return {
        "source": "live",
        "kind": kind,
        "id": row.get("id"),
        "name": row.get("name"),
        "facts": facts,
        "leak_findings": _findings_for_entity(get_db(), auth.org_id, entity_id),
        "provenance": "Live from your connected data — no sample records.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/diagnostics/{kind}/{entity_id}")
def tradeview_diagnostics(
    kind: str,
    entity_id: str,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit(_TRADEVIEW_RATE_LIMIT))],
):
    """Server-generated diagnostics report from real data."""
    if kind not in ("customer", "lead"):
        raise HTTPException(status_code=404, detail="unknown dossier kind")
    table = "customers" if kind == "customer" else "leads"
    db = get_db()
    try:
        row = _one(db, table, auth.org_id, entity_id)
    except _DBUnavailable:
        raise HTTPException(
            status_code=502,
            detail="Trade View data source unavailable — try again shortly.",
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    findings = _findings_for_entity(db, auth.org_id, entity_id)
    trade = row.get("trade") or row.get("service_interest") or "trade services"
    top = findings[0] if findings else None
    return {
        "source": "live",
        "kind": kind,
        "entity_id": row.get("id"),
        "entity_name": row.get("name"),
        "snapshot": f"{trade} · {kind} · {row.get('address') or 'address on file'}",
        "leak_findings": findings,
        "recommended_next_step": (
            top["recommended_action"]
            if top and top.get("recommended_action")
            else "Run a leak scan across the book, then work the highest-dollar findings first."
        ),
        "provenance": "Generated from your live data — not a sample assessment.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/layers")
def tradeview_layers(
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit(_TRADEVIEW_RATE_LIMIT))],
):
    """Available map layers and their live/sample status."""
    return {
        "layers": [
            {
                "id": "pins",
                "label": "Customers & leads",
                "live": True,
                "requires_auth": True,
                "note": "Your connected customers and leads, geocoded live.",
            },
            {
                "id": "storm",
                "label": "NWS storm alerts",
                "live": True,
                "requires_auth": False,
                "note": "Live from api.weather.gov — keyless.",
            },
            {
                "id": "leak_map",
                "label": "Leak Map — money on the table",
                "live": True,
                "requires_auth": True,
                "note": "Every leak finding pinned with its dollar figure.",
            },
            {
                "id": "actions",
                "label": "What Should We Do",
                "live": False,
                "requires_auth": True,
                "note": "Ships with TW-306.",
            },
        ]
    }


# ---------------------------------------------------------------------------
# TW-303: Leak Map — money walking out the door, pinned with dollar figures
# ---------------------------------------------------------------------------


def _dollars(value: Any) -> float | None:
    """Coerce a detector's dollar figure. Garbage becomes None — a missing
    figure is honest; a fabricated one is not."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v) or v < 0:
        return None
    return round(v, 2)


def _address_index(db, org_id: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Entity id -> {address, kind, name} across customers, leads, quotes,
    plus a customer name -> row fallback for name-matched entities."""
    index: dict[str, dict] = {}
    for table, kind in (
        ("customers", "customer"),
        ("leads", "lead"),
        ("quotes", "quote"),
    ):
        for row in _rows(db, table, org_id):
            rid = row.get("id")
            if rid and str(rid) not in index:
                index[str(rid)] = {
                    "address": (row.get("address") or "").strip() or None,
                    "kind": kind,
                    "name": row.get("customer_name") or row.get("name"),
                }
    names: dict[str, dict] = {}
    for row in _rows(db, "customers", org_id):
        n = (row.get("name") or "").strip().lower()
        if n and n not in names:
            names[n] = {
                "address": (row.get("address") or "").strip() or None,
                "kind": "customer",
                "name": row.get("name"),
            }
    return index, names


def _leak_pin(
    entity: dict[str, Any],
    finding: dict[str, Any],
    index: dict[str, dict],
    names: dict[str, dict],
    budget: _GeocodeBudget,
    *,
    geocode_timeout: float = _PINS_GEOCODE_TIMEOUT_S,
) -> dict[str, Any] | None:
    """One glowing leak pin for one entity. Returns None when the entity
    cannot be located — counted, never pinned on a guess.

    Shares the per-request geocode budget (MAJOR 2, TW-301): beyond the cap
    the entity counts as unlocated this round and the cache warms across
    requests."""
    eid = entity.get("id")
    entry = index.get(str(eid)) if eid is not None else None
    if entry is None:
        entry = names.get((entity.get("name") or "").strip().lower())
    if entry is None or not entry.get("address"):
        return None
    geo = None
    if budget.attempts > 0:
        budget.attempts -= 1
        geo = geocode_cached(entry["address"], timeout=geocode_timeout)
    if geo is None:
        return None
    dollars = _dollars(entity.get("total"))
    if dollars is None:
        dollars = _dollars(entity.get("estimated_value"))
    return {
        "id": f"{finding.get('detector')}:{eid}",
        "entity_id": str(eid) if eid is not None else None,
        "entity_name": entity.get("name") or entry.get("name"),
        "entity_kind": entry.get("kind"),
        "detector": finding.get("detector"),
        "title": finding.get("title"),
        "detail": finding.get("detail"),
        "severity": finding.get("severity"),
        "ladder": finding.get("ladder"),
        "dollars": dollars,
        "days_idle": entity.get("days_idle"),
        "recommended_action": finding.get("recommended_action"),
        "address": entry["address"],
        "lat": geo.lat,
        "lng": geo.lon,
        "located": True,
    }


@router.get("/leaks")
def tradeview_leaks(
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit(_TRADEVIEW_RATE_LIMIT))],
):
    """Leak Map: every leak finding pinned with its dollar figure.

    Runs the real leak-engine scan, resolves each finding's entities to
    addresses (customers, leads, quotes), and geocodes them. Entities that
    cannot be located are counted in totals.unlocated — never pinned on a
    guess, never dropped silently. Dollar figures come only from the
    detectors; a pin without a figure shows none rather than an estimate.
    """
    db = get_db()
    try:
        brief = run_leak_scan(db, auth.org_id)
    except Exception:
        brief = {
            "what_happened": [],
            "what_will_happen": [],
            "what_should_we_do": [],
            "totals": {"findings": 0, "dollars_at_stake": 0.0},
            "data_status": {"errors": ["scan"]},
        }
    index, names = _address_index(db, auth.org_id)
    pins: dict[str, dict[str, Any]] = {}
    unlocated = 0
    budget = _GeocodeBudget(_MAX_GEOCODE_ATTEMPTS_PER_REQUEST)
    for rung in ("what_happened", "what_will_happen", "what_should_we_do"):
        for finding in brief.get(rung, []) or []:
            for entity in finding.get("entities", []) or []:
                try:
                    pin = _leak_pin(entity, finding, index, names, budget)
                except Exception:
                    unlocated += 1
                    continue
                if pin is None:
                    unlocated += 1
                    continue
                key = pin["entity_id"] or pin["id"]
                prev = pins.get(key)
                # Dedupe: one pin per entity, keeping the highest-dollar one.
                if prev is None or (pin["dollars"] or 0) > (prev["dollars"] or 0):
                    pins[key] = pin
    leak_list = sorted(pins.values(), key=lambda p: p["dollars"] or 0, reverse=True)
    dollars = brief.get("totals", {}).get("dollars_at_stake", 0) or 0
    try:
        dollars = float(dollars)
    except (TypeError, ValueError):
        dollars = 0.0
    headline = (
        f"${dollars:,.0f} left on the table"
        if dollars > 0
        else "No leaks detected — your follow-up game is tight."
    )
    return {
        "source": "live",
        "org_id": auth.org_id,
        "headline": headline,
        "totals": {
            "findings": brief.get("totals", {}).get("findings", 0),
            "dollars_at_stake": round(dollars, 2),
            "located": len(leak_list),
            "unlocated": unlocated,
            "partial": bool(brief.get("data_status", {}).get("partial")),
        },
        "leaks": leak_list,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

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

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..leak_engine import run_leak_scan
from ..limiter import enforce_user_limit, limiter
from ..services.geocode import geocode_cached

router = APIRouter(prefix="/tradeview", tags=["tradeview"])


def _rows(db, table: str, org_id: str) -> list[dict[str, Any]]:
    try:
        return db.table(table).select("*").eq("org_id", org_id).execute().data or []
    except Exception:
        return []


def _one(db, table: str, org_id: str, entity_id: str) -> dict[str, Any] | None:
    for row in _rows(db, table, org_id):
        if str(row.get("id")) == str(entity_id):
            return row
    return None


def _pin_for(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Build a map pin; geocoding failure degrades to located=False."""
    address = (row.get("address") or "").strip() or None
    geo = geocode_cached(address) if address else None
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


def _findings_for_entity(db, org_id: str, entity_id: str) -> list[dict[str, Any]]:
    """Leak findings whose entities reference this id. Never raises."""
    try:
        brief = run_leak_scan(db, org_id)
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
@limiter.limit("30/hour")
def tradeview_pins(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("30/hour"))],
):
    """Map pins from the org's real customers + leads."""
    pins: list[dict[str, Any]] = []
    unlocated = 0
    for row in _rows(get_db(), "customers", auth.org_id):
        try:
            pin = _pin_for("customer", row)
        except Exception:
            unlocated += 1
            continue
        if pin["located"]:
            pins.append(pin)
        else:
            unlocated += 1
    for row in _rows(get_db(), "leads", auth.org_id):
        try:
            pin = _pin_for("lead", row)
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/dossiers/{kind}/{entity_id}")
@limiter.limit("30/hour")
def tradeview_dossier(
    kind: str,
    entity_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("30/hour"))],
):
    """Dossier for one customer or lead: facts + related leak findings."""
    if kind not in ("customer", "lead"):
        raise HTTPException(status_code=404, detail="unknown dossier kind")
    table = "customers" if kind == "customer" else "leads"
    row = _one(get_db(), table, auth.org_id, entity_id)
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
@limiter.limit("30/hour")
def tradeview_diagnostics(
    kind: str,
    entity_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("30/hour"))],
):
    """Server-generated diagnostics report from real data."""
    if kind not in ("customer", "lead"):
        raise HTTPException(status_code=404, detail="unknown dossier kind")
    table = "customers" if kind == "customer" else "leads"
    db = get_db()
    row = _one(db, table, auth.org_id, entity_id)
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
@limiter.limit("30/hour")
def tradeview_layers(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("30/hour"))],
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
                "live": False,
                "requires_auth": True,
                "note": "Ships with TW-303.",
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

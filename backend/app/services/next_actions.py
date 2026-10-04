"""Next-best-action engine (TW-306; implements the TW-212 brief).

Consumes leak-engine findings, ranks every open leak by expected
dollars-recovered, and prescribes the single next move per leak.
Nearest-qualified-tech assignment uses JEV-style auditable decision
records (clean-room, stdlib only). Route optimization is a documented
nearest-neighbor heuristic.

Recoverability weights are DOCUMENTED HEURISTIC PRIORS — labeled as
such on every action, tunable per org — never presented as measured
conversion rates. expected_recovery = dollars x weight, shown rounded.
A leak with no dollar figure gets no expected recovery (None), never a
fabricated one.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

# Detector -> recoverability prior. Basis (documented, tunable):
# - quote-resurrection 0.35: stalled quotes are the warmest money; a
#   two-touch follow-up measurably lifts close rates.
# - plan-churn-risk 0.50: saving an existing plan beats winning a new
#   customer on cost.
# - equipment-age-graveyard 0.20: replacement cycles are long; the
#   action is booking the inspection, not closing today.
RECOVERABILITY: dict[str, float] = {
    "quote-resurrection": 0.35,
    "plan-churn-risk": 0.50,
    "equipment-age-graveyard": 0.20,
}
DEFAULT_RECOVERABILITY = 0.25
_WEIGHT_BASIS = "heuristic prior — tune per org; not a measured rate"


def _dollars(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v) or v < 0:
        return None
    return round(v, 2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Stdlib only."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_action_queue(
    findings: list[dict[str, Any]],
    located: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Rank every (finding, entity) by expected dollars-recovered.

    `located`: entity_id -> {"lat":, "lng":} for actions that can be
    mapped and routed. Actions without dollars rank last with
    expected_recovery None — they still need doing.
    """
    located = located or {}
    actions: list[dict[str, Any]] = []
    for finding in findings:
        detector = finding.get("detector") or "unknown"
        weight = RECOVERABILITY.get(detector, DEFAULT_RECOVERABILITY)
        for entity in finding.get("entities", []) or []:
            eid = entity.get("id")
            dollars = _dollars(entity.get("total"))
            if dollars is None:
                dollars = _dollars(entity.get("estimated_value"))
            expected = round(dollars * weight, 2) if dollars is not None else None
            geo = located.get(str(eid)) if eid is not None else None
            actions.append(
                {
                    "id": f"action:{detector}:{eid}",
                    "rank": 0,  # assigned after sorting
                    "detector": detector,
                    "title": finding.get("title"),
                    "entity_id": str(eid) if eid is not None else None,
                    "entity_name": entity.get("name"),
                    "severity": finding.get("severity"),
                    "ladder": finding.get("ladder"),
                    "dollars": dollars,
                    "recoverability": weight,
                    "weight_basis": _WEIGHT_BASIS,
                    "expected_recovery": expected,
                    "next_move": finding.get("recommended_action")
                    or "Follow up today — this one is going cold.",
                    "days_idle": entity.get("days_idle"),
                    "trade": entity.get("trade") or finding.get("vertical"),
                    "lat": geo["lat"] if geo else None,
                    "lng": geo["lng"] if geo else None,
                    "located": geo is not None,
                }
            )
    actions.sort(
        key=lambda a: (a["expected_recovery"] is None, -(a["expected_recovery"] or 0))
    )
    for i, a in enumerate(actions, 1):
        a["rank"] = i
    return actions


def assign_tech(
    action: dict[str, Any],
    roster: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Assign the nearest qualified tech to an action.

    `roster`: [{"id", "name", "skills": [...], "lat", "lng"}].
    Returns {"tech": {...}|None, "reason": str, "decision": {...}|None}.
    The decision record is JEV-style auditable: every candidate scored,
    the winner named, the reason stated.
    """
    if not action.get("located") or action.get("lat") is None:
        return {
            "tech": None,
            "reason": "action has no location — cannot assign",
            "decision": None,
        }
    if not roster:
        return {
            "tech": None,
            "reason": "no technician roster connected — connect your roster to enable assignment",
            "decision": None,
        }
    trade = (action.get("trade") or "").strip().lower()
    scored = []
    for t in roster:
        try:
            skills = {(s or "").strip().lower() for s in (t.get("skills") or [])}
            if trade and skills and trade not in skills and "all" not in skills:
                continue
            km = haversine_km(
                action["lat"], action["lng"], float(t["lat"]), float(t["lng"])
            )
            scored.append(
                {"id": t["id"], "name": t.get("name"), "distance_km": round(km, 2)}
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not scored:
        return {
            "tech": None,
            "reason": f"no qualified tech on the roster for trade '{trade or 'unknown'}'",
            "decision": None,
        }
    scored.sort(key=lambda s: s["distance_km"])
    winner = scored[0]
    distribution = {s["id"]: round(1.0 / (1.0 + s["distance_km"]), 4) for s in scored}
    return {
        "tech": winner,
        "reason": f"nearest qualified tech ({winner['distance_km']} km)",
        "decision": {
            "option_set_name": "tech_assignment",
            "chosen_option": winner["id"],
            "distribution": distribution,
            "model_id": "nearest-qualified-heuristic",
            "timestamp": datetime.now(UTC).isoformat(),
            "context": f"action {action.get('id')} trade={trade or 'unknown'}",
        },
    }


def optimize_route(
    stops: list[dict[str, Any]],
    start: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Nearest-neighbor route over stops. Documented heuristic, not optimal.

    `stops`: [{"id", "name", "lat", "lng"}]. Returns visit order, legs,
    and total km.
    """
    remaining = [
        s for s in stops if s.get("lat") is not None and s.get("lng") is not None
    ]
    if not remaining:
        return {
            "order": [],
            "legs": [],
            "total_km": 0.0,
            "method": "nearest-neighbor heuristic — no stops given",
            "note": "No located stops to route.",
        }
    cur = (
        {"lat": start["lat"], "lng": start["lng"]}
        if start
        else {"lat": remaining[0]["lat"], "lng": remaining[0]["lng"]}
    )
    order: list[str] = []
    legs: list[dict[str, Any]] = []
    total = 0.0
    while remaining:
        nxt = min(
            remaining,
            key=lambda s: haversine_km(cur["lat"], cur["lng"], s["lat"], s["lng"]),
        )
        km = haversine_km(cur["lat"], cur["lng"], nxt["lat"], nxt["lng"])
        total += km
        legs.append(
            {
                "from": order[-1] if order else "start",
                "to": nxt["id"],
                "km": round(km, 2),
            }
        )
        order.append(nxt["id"])
        cur = {"lat": nxt["lat"], "lng": nxt["lng"]}
        remaining = [s for s in remaining if s["id"] != nxt["id"]]
    return {
        "order": order,
        "legs": legs,
        "total_km": round(total, 2),
        "method": "nearest-neighbor heuristic — fast and good, not optimal",
    }

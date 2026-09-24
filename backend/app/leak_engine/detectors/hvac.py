"""HVAC leak detectors — live for the Oct 2 launch (TW-201).

Sourced from the TW-199 moat brief: the equipment graveyard, plan churn
(including involuntary card-failure churn), and quote resurrection.
"""
from datetime import datetime, timezone
from typing import Any

from ..registry import Detector, register

# Low end of each lifespan band (TW-199): the action threshold. A unit is
# "past life" at the band floor and "entering the window" 12 months before it.
_TYPE_LIFE_FLOOR = {
    "furnace": 15,
    "ac": 10,
    "heat_pump": 10,
    "boiler": 20,
    "water_heater": 8,
    "air_handler": 12,
}
_DEFAULT_LIFE = 15
_WINDOW_DAYS = 365


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _age_days(installed: str | None) -> float | None:
    dt = _parse_dt(installed)
    if not dt:
        return None
    return (_now() - dt).total_seconds() / 86400


def _fetch(db, table: str, org_id: str) -> list[dict[str, Any]]:
    return (
        db.table(table).select("*").eq("org_id", org_id).execute().data or []
    )


def _entity(row: dict[str, Any], **extra) -> dict[str, Any]:
    e = {
        "id": row.get("id"),
        "name": row.get("customer_name") or row.get("name"),
    }
    e.update(extra)
    return e


# ---------------------------------------------------------------------------
# equipment-age-graveyard
# ---------------------------------------------------------------------------

def _equipment_graveyard_run(db, org_id: str) -> list[dict[str, Any]]:
    assets = _fetch(db, "service_assets", org_id)
    past, entering = [], []
    for a in assets:
        age = _age_days(a.get("install_date"))
        if age is None:
            continue
        atype = (a.get("asset_type") or "").lower()
        floor = _TYPE_LIFE_FLOOR.get(atype, _DEFAULT_LIFE)
        stated = a.get("expected_life_years") or _DEFAULT_LIFE
        effective_days = min(floor, stated) * 365
        info = _entity(
            a,
            asset_type=atype or None,
            brand=a.get("brand"),
            age_years=round(age / 365.25, 1),
        )
        if age >= effective_days:
            past.append((age, info))
        elif effective_days - age <= _WINDOW_DAYS:
            entering.append((age, info))

    findings = []
    if past:
        past.sort(key=lambda t: t[0], reverse=True)
        findings.append({
            "ladder": "happened",
            "severity": "watch",
            "title": f"{len(past)} installed systems past expected life",
            "detail": (
                "These customers are running equipment past its expected "
                "lifespan. When it fails — usually the hottest or coldest "
                "week of the year — the replacement goes to whoever answers "
                "first. It should be you."
            ),
            "count": len(past),
            "estimated_value": None,
            "entities": [info for _, info in past[:10]],
            "recommended_action": "Call the oldest units first with a replacement quote before failure season.",
        })
    if entering:
        entering.sort(key=lambda t: t[0], reverse=True)
        findings.append({
            "ladder": "will",
            "severity": "info",
            "title": f"{len(entering)} systems entering end-of-life within 12 months",
            "detail": (
                "These units are inside a year of their expected lifespan. "
                "A proactive inspection now turns an emergency replacement "
                "later into a planned sale."
            ),
            "count": len(entering),
            "estimated_value": None,
            "entities": [info for _, info in entering[:10]],
            "recommended_action": "Book end-of-life inspections this month; quote replacements on the visit.",
        })
    if past or entering:
        ordered = sorted(past + entering, key=lambda t: t[0], reverse=True)
        findings.append({
            "ladder": "should",
            "severity": "urgent" if past else "watch",
            "title": "Monday replacement call list",
            "detail": "Prioritized by equipment age — oldest first.",
            "count": len(ordered),
            "estimated_value": None,
            "entities": [info for _, info in ordered[:10]],
            "recommended_action": "Work this list top to bottom before the next extreme-weather week.",
        })
    return findings


# ---------------------------------------------------------------------------
# plan-churn-risk
# ---------------------------------------------------------------------------

_BILLING_RISK = {"past_due", "card_failed", "payment_failed"}


def _plan_churn_run(db, org_id: str) -> list[dict[str, Any]]:
    plans = _fetch(db, "service_plans", org_id)
    now = _now()
    missing_visits, billing_risk = [], []
    for p in plans:
        if (p.get("status") or "").lower() != "active":
            continue
        started = _parse_dt(p.get("started_at")) or now
        days_active = max(0.0, (now - started).total_seconds() / 86400)
        expected = (p.get("visits_per_year") or 0) * min(1.0, days_active / 365)
        completed = p.get("visits_completed") or 0
        info = _entity(
            p,
            plan_name=p.get("plan_name"),
            visits_completed=completed,
            visits_expected=round(expected, 1),
            billing_status=p.get("billing_status"),
        )
        if completed < expected - 0.5:
            missing_visits.append(info)
        if (p.get("billing_status") or "").lower() in _BILLING_RISK:
            billing_risk.append(info)

    findings = []
    if missing_visits:
        findings.append({
            "ladder": "happened",
            "severity": "watch",
            "title": f"{len(missing_visits)} plan members missing seasonal visits",
            "detail": (
                "Members who skip their visits are members about to cancel — "
                "and nobody flags them until the renewal fails."
            ),
            "count": len(missing_visits),
            "estimated_value": None,
            "entities": missing_visits[:10],
            "recommended_action": "Call each member, book the missed visit, remind them what the plan already paid for.",
        })
    if billing_risk:
        findings.append({
            "ladder": "should",
            "severity": "urgent",
            "title": f"{len(billing_risk)} plans at involuntary-churn risk",
            "detail": (
                "Failed cards and past-due billing — churn with no hard "
                "feelings and no second chance, purely from billing neglect."
            ),
            "count": len(billing_risk),
            "estimated_value": None,
            "entities": billing_risk[:10],
            "recommended_action": "Fix the payment method today — a 2-minute call saves the whole plan.",
        })
    return findings


# ---------------------------------------------------------------------------
# quote-resurrection
# ---------------------------------------------------------------------------

_STALLED_QUOTE_DAYS = 14
_QUIET_FOLLOWUP_DAYS = 7
_OPEN_QUOTE = {"sent", "proposal"}


def _quote_resurrection_run(db, org_id: str) -> list[dict[str, Any]]:
    quotes = _fetch(db, "quotes", org_id)
    now = _now()
    stalled = []
    for q in quotes:
        if (q.get("status") or "").lower() not in _OPEN_QUOTE:
            continue
        sent = _parse_dt(q.get("sent_at"))
        last_fu = _parse_dt(q.get("last_follow_up_at"))
        fu_count = q.get("follow_up_count") or 0
        ref = last_fu or sent
        if ref is None:
            continue
        days_idle = (now - ref).total_seconds() / 86400
        never_followed = fu_count == 0 and (now - sent).total_seconds() / 86400 >= _STALLED_QUOTE_DAYS
        gone_quiet = fu_count > 0 and days_idle >= _QUIET_FOLLOWUP_DAYS
        if never_followed or gone_quiet:
            stalled.append((
                float(q.get("total") or 0),
                _entity(q, total=float(q.get("total") or 0),
                        days_idle=int(days_idle),
                        follow_up_count=fu_count),
            ))

    # Fallback: no quote records yet (pilot importing via leads) — read
    # stalled proposal/qualified leads instead.
    if not stalled:
        leads = _fetch(db, "leads", org_id)
        for lead in leads:
            if (lead.get("status") or "").lower() not in ("proposal", "qualified"):
                continue
            ref = _parse_dt(lead.get("stage_changed_at") or lead.get("created_at"))
            if ref is None:
                continue
            days_idle = (now - ref).total_seconds() / 86400
            if days_idle >= _STALLED_QUOTE_DAYS:
                stalled.append((
                    float(lead.get("estimated_value") or 0),
                    _entity(lead,
                            total=float(lead.get("estimated_value") or 0),
                            days_idle=int(days_idle),
                            source="lead"),
                ))

    if not stalled:
        return []
    stalled.sort(key=lambda t: t[0], reverse=True)
    total_value = round(sum(v for v, _ in stalled), 2)
    return [{
        "ladder": "should",
        "severity": "watch",
        "title": f"{len(stalled)} stalled quotes still winnable",
        "detail": (
            "Quotes that died without follow-up. Industry data puts "
            "conversion near 24% without follow-up versus 33-38% with a "
            "simple two-touch sequence — the gap is almost entirely process."
        ),
        "count": len(stalled),
        "estimated_value": total_value,
        "entities": [info for _, info in stalled[:10]],
        "recommended_action": "Two-touch follow-up this week, highest value first. Call, don't email.",
    }]


register(Detector(
    name="equipment-age-graveyard",
    vertical="hvac",
    requires=["service_assets"],
    run=_equipment_graveyard_run,
))
register(Detector(
    name="plan-churn-risk",
    vertical="hvac",
    requires=["service_plans"],
    run=_plan_churn_run,
))
register(Detector(
    name="quote-resurrection",
    vertical="hvac",
    requires=["quotes", "leads"],
    run=_quote_resurrection_run,
))

"""HVAC leak detectors — live for the Oct 2 launch (TW-201).

Sourced from the TW-199 moat brief: the equipment graveyard, plan churn
(including involuntary card-failure churn), and quote resurrection.

TW-204: per-row resilience. Pilot CSV imports arrive as hostile strings
("N/A", "NaN", "abc") where the migrations declare numeric columns. A bad
cell skips its row — counted loudly in the brief — instead of killing the
whole detector.
"""
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..registry import Detector, register

# Low end of each lifespan band (TW-199): the action threshold. A unit is
# "past life" at the band floor and "entering the window" 12 months before it.
_TYPE_LIFE_FLOOR = {
    "furnace": 15,
    "ac": 10,
    "air_conditioner": 10,  # canonical import value (docs/service-data-model.md)
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
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    # B1 (Rosa): production DATE columns arrive as naive "YYYY-MM-DD".
    # Treat naive as UTC so aware-minus-naive never raises TypeError.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


def _num(value: Any, default: float | None = None) -> float | None:
    """Coerce a CSV-ish cell to float.

    Returns `default` for missing, non-numeric, NaN, or infinite values.
    Pilot imports arrive as strings ("N/A", "NaN", "abc") and must never
    raise inside a detector (TW-204).
    """
    if value is None or value == "":
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


# ---------------------------------------------------------------------------
# equipment-age-graveyard
# ---------------------------------------------------------------------------

_ML_SCORER = None
_ML_SCORER_TRIED = False


def _log_ml(msg, *args):
    logging.getLogger(__name__).warning("[graveyard-ml] " + msg, *args)


def _ml_scorer():
    """Lazily load the ML survival scorer (TW-213 wire-in, TW-470 surge).

    Returns None when the artifact is missing or corrupt — the detector
    then runs static-floor-only and says so loudly. The load is attempted
    once per process; a loud log line marks the fallback.
    """
    global _ML_SCORER, _ML_SCORER_TRIED
    if _ML_SCORER_TRIED:
        return _ML_SCORER
    _ML_SCORER_TRIED = True
    try:
        from ...services.equipment_survival import SurvivalScorer

        artifact = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "survival_artifact.v1.json"
        )
        _ML_SCORER = SurvivalScorer.from_path(artifact)
        _log_ml("ML survival artifact loaded: %s (%s calibration)",
                artifact.name, _ML_SCORER.provenance)
    except Exception as exc:
        _log_ml("ML survival scorer unavailable (%s) — graveyard runs static-floor-only", exc)
        _ML_SCORER = None
    return _ML_SCORER


def _ml_estimate_for(asset: dict, age_days: float):
    """Score one asset with the ML survival model. None => static fallback.

    Never raises: every undecidable case returns None and the caller labels
    the row 'static-floor'.
    """
    try:
        scorer = _ml_scorer()
        if scorer is None:
            return None
        from ...services.equipment_survival import brand_tier_for

        atype = (asset.get("asset_type") or "").lower()
        tier, assumed = brand_tier_for(asset.get("brand"))
        return scorer.score_asset(
            age_years=age_days / 365.25,
            equipment_type=atype,
            brand_tier=tier,
            metro_key="",  # orgs carry no metro; global conformal + honest label
            brand_assumed=assumed,
        )
    except Exception:
        return None


def _equipment_graveyard_run(db, org_id: str):
    assets = _fetch(db, "service_assets", org_id)
    past, entering = [], []  # (sort_key, info, is_ml)
    skipped = 0
    ml_rows = 0
    for a in assets:
        try:
            age = _age_days(a.get("install_date"))
            if age is None:
                continue
            atype = (a.get("asset_type") or "").lower()
            info = _entity(
                a,
                asset_type=atype or None,
                brand=a.get("brand"),
                age_years=round(age / 365.25, 1),
            )
            # TW-213 wire-in: ML survival dates first, static floor as the
            # labeled fallback. Provenance on every row — never a bare number.
            est = _ml_estimate_for(a, age)
            if est is not None:
                ml_rows += 1
                info["replacement_basis"] = "ml"
                info["remaining_years"] = est.median_remaining_years
                info["remaining_window"] = (
                    f"{est.remaining_lo_years}\u2013{est.remaining_hi_years} yrs "
                    f"(80% interval, {est.provenance})"
                )
                info["p_fail_2y"] = est.p_fail_within_2y
                info["cohort_median_life_years"] = est.cohort_median_life_years
                if est.brand_assumed:
                    info["brand_assumed_mid"] = True
                # TW-213 rule: "past expected life" = the unit outlived its
                # cohort's median replacement age (age >= L50); "entering" =
                # inside one window-year of L50. Replaces the static band
                # floors with model dates — every row labeled by basis.
                age_years = age / 365.25
                l50 = est.cohort_median_life_years
                if age_years >= l50:
                    past.append((est.p_fail_within_2y, info, True))
                elif l50 - age_years <= _WINDOW_DAYS / 365.25:
                    entering.append((est.p_fail_within_2y, info, True))
                continue
            # Static-floor fallback (unchanged legacy path, now labeled).
            floor = _TYPE_LIFE_FLOOR.get(atype, _DEFAULT_LIFE)
            # TW-204: "N/A" and friends fall back to the documented default,
            # exactly like a missing value — the row is otherwise valid.
            stated = _num(a.get("expected_life_years"), _DEFAULT_LIFE)
            effective_days = min(floor, stated) * 365
            info["replacement_basis"] = "static-floor"
            if age >= effective_days:
                past.append((age, info, False))
            elif effective_days - age <= _WINDOW_DAYS:
                entering.append((age, info, False))
        except Exception:
            # TW-204: backstop — one bad row skips loudly, never kills the
            # detector.
            skipped += 1

    findings = []
    ml_basis_note = (
        f" {ml_rows} of these used the ML survival model (80% intervals, "
        "synthetic calibration); the rest used static life floors."
        if ml_rows
        else ""
    )
    if past:
        # ML rows sort by failure probability, static rows by age — most
        # urgent first either way.
        past.sort(key=lambda t: t[0], reverse=True)
        findings.append({
            "ladder": "happened",
            "severity": "watch",
            "title": f"{len(past)} installed systems past expected life",
            "detail": (
                "These customers are running equipment past its expected "
                "lifespan. When it fails — usually the hottest or coldest "
                "week of the year — the replacement goes to whoever answers "
                "first. It should be you." + ml_basis_note
            ),
            "count": len(past),
            "estimated_value": None,
            "entities": [info for _, info, _ in past[:10]],
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
                "later into a planned sale." + ml_basis_note
            ),
            "count": len(entering),
            "estimated_value": None,
            "entities": [info for _, info, _ in entering[:10]],
            "recommended_action": "Book end-of-life inspections this month; quote replacements on the visit.",
        })
    if past or entering:
        # Sort matches the customer-facing copy below: ML rows by failure
        # probability (sharpest signal first), then static-floor rows by age.
        # (Rosa nit: raw t[0] mixed p_fail<=1 with age-in-days, which always
        # ranked static rows first regardless of urgency.)
        ml_rows_first = sorted(
            [t for t in past + entering if t[2]], key=lambda t: t[0], reverse=True
        )
        static_rows = sorted(
            [t for t in past + entering if not t[2]], key=lambda t: t[0], reverse=True
        )
        ordered = ml_rows_first + static_rows
        findings.append({
            "ladder": "should",
            "severity": "urgent" if past else "watch",
            "title": "Your replacement call list",
            "detail": (
                "Prioritized by failure probability (ML rows) then equipment "
                "age (static-floor rows) — most urgent first." + ml_basis_note
            ),
            "count": len(ordered),
            "estimated_value": None,
            "entities": [info for _, info, _ in ordered[:10]],
            "recommended_action": "Work this list top to bottom before the next extreme-weather week.",
        })
    return findings, {"skipped_rows": skipped}


# ---------------------------------------------------------------------------
# plan-churn-risk
# ---------------------------------------------------------------------------

_BILLING_RISK = {"past_due", "card_failed", "payment_failed"}


def _plan_churn_run(db, org_id: str):
    plans = _fetch(db, "service_plans", org_id)
    now = _now()
    missing_visits, billing_risk = [], []
    skipped = 0
    for p in plans:
        try:
            if (p.get("status") or "").lower() != "active":
                continue
            started = _parse_dt(p.get("started_at")) or now
            days_active = max(0.0, (now - started).total_seconds() / 86400)
            # TW-204: verdict-critical numerics. Uncoercible means we cannot
            # judge this row, so skip it loudly instead of guessing.
            vpy = _num(p.get("visits_per_year"))
            completed = _num(p.get("visits_completed"))
            if vpy is None or completed is None:
                skipped += 1
                continue
            expected = vpy * min(1.0, days_active / 365)
            info = _entity(
                p,
                plan_name=p.get("plan_name"),
                visits_completed=int(completed),
                visits_expected=round(expected, 1),
                billing_status=p.get("billing_status"),
            )
            if completed < expected - 0.5:
                missing_visits.append(info)
            if (p.get("billing_status") or "").lower() in _BILLING_RISK:
                billing_risk.append(info)
        except Exception:
            skipped += 1

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
                "feelings and no second chance. Purely a billing failure, "
                "not a service failure."
            ),
            "count": len(billing_risk),
            "estimated_value": None,
            "entities": billing_risk[:10],
            "recommended_action": "Fix the payment method today — a 2-minute call saves the whole plan.",
        })
    return findings, {"skipped_rows": skipped}


# ---------------------------------------------------------------------------
# quote-resurrection
# ---------------------------------------------------------------------------

# Valid quote statuses per migration_service_datamodel.sql
# (draft|sent|follow_up|won|lost|expired). "sent" and "follow_up" are open.
_STALLED_QUOTE_DAYS = 14
_QUIET_FOLLOWUP_DAYS = 7
_OPEN_QUOTE = {"sent", "follow_up"}


def _quote_resurrection_run(db, org_id: str):
    quotes = _fetch(db, "quotes", org_id)
    now = _now()
    stalled = []
    skipped = 0
    for q in quotes:
        try:
            if (q.get("status") or "").lower() not in _OPEN_QUOTE:
                continue
            sent = _parse_dt(q.get("sent_at"))
            last_fu = _parse_dt(q.get("last_follow_up_at"))
            # TW-204: follow-up count drives the verdict — uncoercible
            # means skip loudly. Total only drives display/priority, so a
            # garbage cell coerces to 0 and the row survives.
            fu_count = _num(q.get("follow_up_count"))
            if fu_count is None:
                skipped += 1
                continue
            fu_count = int(fu_count)
            total = _num(q.get("total"), 0.0)
            ref = last_fu or sent
            if ref is None:
                continue
            days_idle = (now - ref).total_seconds() / 86400
            never_followed = fu_count == 0 and sent is not None and (now - sent).total_seconds() / 86400 >= _STALLED_QUOTE_DAYS
            gone_quiet = fu_count > 0 and days_idle >= _QUIET_FOLLOWUP_DAYS
            if never_followed or gone_quiet:
                stalled.append((
                    total,
                    _entity(q, total=total,
                            days_idle=int(days_idle),
                            follow_up_count=fu_count),
                ))
        except Exception:
            # TW-204: backstop — one bad row skips loudly, never kills the
            # detector.
            skipped += 1

    # Fallback: no quote records yet (pilot importing via leads) — read
    # stalled proposal/qualified leads instead.
    if not stalled:
        leads = _fetch(db, "leads", org_id)
        for lead in leads:
            try:
                if (lead.get("status") or "").lower() not in ("proposal", "qualified"):
                    continue
                ref = _parse_dt(lead.get("stage_changed_at") or lead.get("created_at"))
                if ref is None:
                    continue
                days_idle = (now - ref).total_seconds() / 86400
                if days_idle >= _STALLED_QUOTE_DAYS:
                    total = _num(lead.get("estimated_value"), 0.0)
                    stalled.append((
                        total,
                        _entity(lead,
                                total=total,
                                days_idle=int(days_idle),
                                source="lead"),
                    ))
            except Exception:
                skipped += 1

    if not stalled:
        return [], {"skipped_rows": skipped}
    stalled.sort(key=lambda t: t[0], reverse=True)
    total_value = round(sum(v for v, _ in stalled), 2)
    return [{
        "ladder": "should",
        "severity": "watch",
        "title": f"{len(stalled)} stalled quotes still winnable",
        "detail": (
            "Quotes that died without follow-up. Per industry research, "
            "conversion runs near 24% without follow-up versus 33-38% with a "
            "simple two-touch sequence — the gap is almost entirely process."
        ),
        "count": len(stalled),
        "estimated_value": total_value,
        "entities": [info for _, info in stalled[:10]],
        "recommended_action": "Two-touch follow-up this week, highest value first. Call, don't email.",
    }], {"skipped_rows": skipped}


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

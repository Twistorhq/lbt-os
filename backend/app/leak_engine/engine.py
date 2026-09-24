"""Leak scan engine — runs the registered detectors for an org's vertical and
groups findings into the morning brief (the question ladder).

Leak findings are never fabricated: a detector whose source tables are
missing or empty degrades to an honest `insufficient_data` status.

TW-208 self-healing: one error never crushes the full process. Each detector
runs in isolation with transient retries; a detector that still fails lands
in data_status["errors"] and the scan completes partial, flagged honestly.
"""
import math
from datetime import datetime, timezone
from typing import Any

from .. import self_healing as sh
from .registry import detectors_for

# Detector invocation retries: transient DB blips heal themselves; permanent
# detector bugs raise immediately into the per-detector error bucket.
_DETECTOR_RETRY_ATTEMPTS = 3
_DETECTOR_RETRY_BASE_DELAY = 0.2

# Industry (organizations.industry) -> leak-engine vertical.
_INDUSTRY_VERTICAL = {
    "hvac": "hvac",
    "plumbing": "plumbing",
    "electrician": "electrical",
    "electrical": "electrical",
    "landscaping": "landscaping",
    "cleaning_service": "cleaning",
    "salon_spa": "salons",
    "restaurant": "restaurants",
    "gym": "fitness",
    "real_estate": "real_estate",
    "dental": "dental",
    "roofing": "roofing",
    "auto_repair": "auto",
    "veterinary": "veterinary",
    "pest_control": "pest",
    "appliance_repair": "appliance",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def vertical_for_industry(industry: str | None) -> str:
    """Map organizations.industry to a leak-engine vertical.

    Defaults to hvac for the pilot: an org with no industry set still gets
    the beachhead detectors rather than an empty brief.
    """
    return _INDUSTRY_VERTICAL.get((industry or "").strip().lower(), "hvac")


def vertical_for_org(db, org_id: str) -> str:
    """Strict org → vertical lookup. Raises on DB failure.

    Product paths use this directly so a DB blip surfaces as a 500 instead
    of silently serving the wrong cohort (TW-209 fix round, Rosa). Forgiving
    wrappers build on top and document their fallback explicitly:
      * engine._vertical_for → "hvac" (the brief: pilot orgs with no industry
        still get the beachhead detectors rather than an empty brief)
      * routers.leak_engine._analytics_vertical → "unknown" (analytics
        tagging only — never product data)
    """
    rows = (
        db.table("organizations")
        .select("industry")
        .eq("id", org_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    industry = rows[0].get("industry") if rows else None
    return vertical_for_industry(industry)


def _vertical_for(db, org_id: str) -> str:
    try:
        return vertical_for_org(db, org_id)
    except Exception:
        return "hvac"


def _tables_available(db, tables: list[str]) -> list[str]:
    """Return the subset of tables that exist and are readable."""
    report = sh.health_check(db, tables)
    return [t for t, status in report.items() if status != "ok"]


def _safe_float(value: Any) -> float:
    """Coerce a detector's estimated_value; hostile values become 0.0.

    TW-208: a detector returning estimated_value="N/A" must never 500 the
    brief totals aggregation.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return v


def run_leak_scan(db, org_id: str) -> dict[str, Any]:
    vertical = _vertical_for(db, org_id)
    detectors = detectors_for(vertical)

    brief: dict[str, Any] = {
        "org_id": org_id,
        "vertical": vertical,
        "generated_at": _now().isoformat(),
        "what_happened": [],
        "what_will_happen": [],
        "what_should_we_do": [],
        "totals": {"findings": 0, "dollars_at_stake": 0.0},
        "data_status": {"insufficient": []},
    }

    for detector in detectors:
        missing = _tables_available(db, detector.requires)
        if missing:
            for t in missing:
                if t not in brief["data_status"]["insufficient"]:
                    brief["data_status"]["insufficient"].append(t)
            continue
        try:
            # TW-208: transient DB blips retry with backoff; a permanent
            # detector bug raises immediately into the error bucket below.
            # Either way a detector must never take down the scan.
            result = sh.retry_with_backoff(
                lambda: detector(db, org_id),
                attempts=_DETECTOR_RETRY_ATTEMPTS,
                base_delay=_DETECTOR_RETRY_BASE_DELAY,
            ) or []
            # TW-204: detectors may return (findings, meta) with a skipped_rows
            # count — surfaced loudly so a bad CSV cell never silently removes
            # a leak category from the morning brief. Unpacked inside the try:
            # a malformed contract from a future detector degrades to an
            # honest error entry. A detector must never take down the scan.
            if isinstance(result, tuple):
                findings, meta = result
            else:
                findings, meta = result, {}
            if not isinstance(meta, dict):
                raise TypeError(
                    f"detector {detector.name} returned non-dict meta"
                )
            try:
                skipped = int(meta.get("skipped_rows") or 0)
            except (TypeError, ValueError):
                skipped = 0
            if skipped:
                brief["data_status"].setdefault("skipped_rows", {})[detector.name] = skipped
            for f in findings:
                rung = {
                    "happened": "what_happened",
                    "will": "what_will_happen",
                    "should": "what_should_we_do",
                }.get(f.get("ladder"), "what_happened")
                f.setdefault("detector", detector.name)
                f.setdefault("vertical", vertical)
                f.setdefault("is_demo", False)
                brief[rung].append(f)
        except Exception:
            # A detector must never take down the whole scan.
            brief["data_status"].setdefault("errors", []).append(detector.name)
            continue

    # TW-208: honest partial flag — the brief says when it isn't whole.
    data_status = brief["data_status"]
    if data_status.get("errors") or data_status.get("skipped_rows") or data_status.get("insufficient"):
        data_status["partial"] = True

    findings_all = (
        brief["what_happened"] + brief["what_will_happen"] + brief["what_should_we_do"]
    )
    brief["totals"]["findings"] = len(findings_all)
    brief["totals"]["dollars_at_stake"] = round(
        sum(_safe_float(f.get("estimated_value")) for f in findings_all), 2
    )
    # Most urgent first inside each rung.
    severity_rank = {"urgent": 0, "watch": 1, "info": 2}
    for rung in ("what_happened", "what_will_happen", "what_should_we_do"):
        brief[rung].sort(key=lambda f: severity_rank.get(f.get("severity"), 2))
    return brief

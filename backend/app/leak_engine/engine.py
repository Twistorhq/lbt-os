"""Leak scan engine — runs the registered detectors for an org's vertical and
groups findings into the morning brief (the question ladder).

Leak findings are never fabricated: a detector whose source tables are
missing or empty degrades to an honest `insufficient_data` status.
"""
from datetime import datetime, timezone
from typing import Any

from .registry import detectors_for

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


def _vertical_for(db, org_id: str) -> str:
    try:
        rows = (
            db.table("organizations")
            .select("industry")
            .eq("id", org_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return "hvac"
    industry = rows[0].get("industry") if rows else None
    return vertical_for_industry(industry)


def _tables_available(db, tables: list[str]) -> list[str]:
    """Return the subset of tables that exist and are readable."""
    missing = []
    for t in tables:
        try:
            db.table(t).select("id").limit(1).execute()
        except Exception:
            missing.append(t)
    return missing


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
            result = detector(db, org_id) or []
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

    findings_all = (
        brief["what_happened"] + brief["what_will_happen"] + brief["what_should_we_do"]
    )
    brief["totals"]["findings"] = len(findings_all)
    brief["totals"]["dollars_at_stake"] = round(
        sum(float(f.get("estimated_value") or 0) for f in findings_all), 2
    )
    # Most urgent first inside each rung.
    severity_rank = {"urgent": 0, "watch": 1, "info": 2}
    for rung in ("what_happened", "what_will_happen", "what_should_we_do"):
        brief[rung].sort(key=lambda f: severity_rank.get(f.get("severity"), 2))
    return brief

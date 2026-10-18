# Prepared by Twistor Holdings LLC.
"""TW-212 wire-in — prioritized action queue in the Morning Brief (TW-470 surge).

The next-best-action engine itself lives in services/next_actions.py
(TW-306, merged, Rosa-approved). This module is the missing output leg:
it takes the ranked queue and renders it for the Monday brief channels
(email HTML + SMS). Self-healing: any failure degrades to an empty
section — the brief always sends.
"""

from __future__ import annotations

import html
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TOP_N = 3


def top_brief_actions(db: Any, org_id: str, top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Top-N money moves for the brief. Never raises.

    Runs the leak scan, ranks via next_actions.build_action_queue, dedupes
    to one action per entity (highest rank wins). On ANY failure returns []
    so the brief degrades gracefully.
    """
    try:
        from ..leak_engine import run_leak_scan
        from . import next_actions as na

        brief = run_leak_scan(db, org_id)
        findings = brief.get("what_should_we_do") or []
        if not findings:
            return []
        queue = na.build_action_queue(findings)
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for a in queue:
            key = a.get("entity_id") or a.get("id")
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "rank": len(out) + 1,
                    "title": a.get("title") or "Open leak",
                    "entity_name": a.get("entity_name"),
                    "next_move": a.get("next_move") or "Follow up today.",
                    "expected_recovery": a.get("expected_recovery"),
                    "dollars": a.get("dollars"),
                    "detector": a.get("detector"),
                }
            )
            if len(out) >= top_n:
                break
        return out
    except Exception as exc:  # TW-208: one bad section never kills the brief
        log.warning("[brief_actions] action queue failed for org %s: %s", org_id, exc)
        return []


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "$?"


def render_email_section(actions: list[dict[str, Any]]) -> str:
    """HTML section for the weekly email brief. Empty string when no actions."""
    if not actions:
        return ""
    rows = []
    for a in actions:
        label = a["entity_name"] or a["title"]
        rec = _money(a["expected_recovery"]) if a["expected_recovery"] is not None else "unpriced"
        rows.append(
            "<div style='padding:10px 0;border-bottom:1px solid #f1f5f9;'>"
            f"<div style='font-size:13px;font-weight:700;color:#0f172a;'>"
            f"#{a['rank']} {html.escape(str(label))} "
            f"<span style='color:#15803d;'>{html.escape(rec)} expected</span></div>"
            f"<div style='font-size:12px;color:#475569;margin-top:2px;'>"
            f"{html.escape(str(a['next_move']))}</div>"
            "</div>"
        )
    return (
        "<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;"
        "padding:16px;margin-bottom:12px;'>"
        "<div style='font-size:11px;font-weight:700;text-transform:uppercase;"
        "letter-spacing:1.4px;color:#15803d;margin-bottom:8px;'>"
        "Your top money moves</div>"
        + "".join(rows)
        + "<div style='font-size:10px;color:#94a3b8;margin-top:8px;'>"
        "Recovery figures use heuristic priors, not measured rates.</div>"
        "</div>"
    )


def render_sms_line(actions: list[dict[str, Any]]) -> str:
    """One compressed line for the SMS brief. Empty string when no actions."""
    if not actions:
        return ""
    a = actions[0]
    label = (a["entity_name"] or a["title"] or "")[:40]
    rec = _money(a["expected_recovery"]) if a["expected_recovery"] is not None else ""
    return f"Top move: {label} ({rec} expected)".strip()

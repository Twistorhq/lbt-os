"""White-label BI service (TW-570 M3, Databox wedge).

Dashboards, org theming, token-gated public embeds (aggregate-only), and
connector syncing into metric snapshots. Every metered action emits to the
M1 toll_events ledger, best-effort (a toll failure never breaks the read).

Security invariants (Rosa TW-570 design review):
- Embed tokens: only the SHA-256 hash is stored; the raw token is returned
  once at issue and never logged.
- Public endpoint (no JWT): tenant isolation comes from the server-side
  token -> embed -> dashboard -> org_id chain. org_id NEVER comes from the
  request; every snapshot read is filtered by the dashboard's org_id.
- Aggregate-only is a mechanism: widget metric keys must be in the
  WIDGET_METRICS allowlist AND every value must validate against the
  metric's declared output shape. Anything else fails closed.
- The public path reads snapshots ONLY. It never triggers compute or
  writes, so unauthenticated requests cannot drive load.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from .bi_connectors import BI_CONNECTOR_REGISTRY, widget_metrics
from .toll_service import record_toll_event

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------
THEME_ALLOWED_KEYS = frozenset({
    "logo_url", "primary_color", "secondary_color", "font_family",
    "background_color",
})
_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_FONT_RE = re.compile(r"^[a-zA-Z0-9 ,\-']{1,60}$")


def validate_theme(theme: Any) -> dict[str, Any]:
    """Validate an org theme dict. Raises HTTPException(400) on violation."""
    if not isinstance(theme, dict):
        raise HTTPException(status_code=400, detail="theme must be an object")
    for key in theme:
        if key not in THEME_ALLOWED_KEYS:
            raise HTTPException(status_code=400,
                                detail=f"unknown theme key: {key}")
    for color_key in ("primary_color", "secondary_color", "background_color"):
        if color_key in theme and not _COLOR_RE.match(str(theme[color_key])):
            raise HTTPException(
                status_code=400,
                detail=f"{color_key} must be a #rgb or #rrggbb hex color")
    if "logo_url" in theme:
        url = str(theme["logo_url"])
        if not url.startswith("https://"):
            raise HTTPException(status_code=400,
                                detail="logo_url must be an https URL")
    if "font_family" in theme:
        if not _FONT_RE.match(str(theme["font_family"])):
            raise HTTPException(
                status_code=400,
                detail="font_family must be a plain system font stack")
    return dict(theme)


# ---------------------------------------------------------------------------
# Output-shape validation (aggregate-only mechanism, Rosa Major 1)
# ---------------------------------------------------------------------------
def _check_type(spec: Any, value: Any) -> bool:
    if spec == "number":
        # bool is a subclass of int — True must NOT pass as a number.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec == "string":
        return isinstance(value, str)
    if spec == "number|null":
        return value is None or _check_type("number", value)
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return False
        if set(value.keys()) != set(spec.keys()):
            return False  # strict: no extra keys, none missing
        return all(_check_type(spec[k], value[k]) for k in spec)
    if isinstance(spec, list):
        if not isinstance(value, list) or len(spec) != 1:
            return False
        return all(_check_type(spec[0], item) for item in value)
    return False


def validate_metric_value(definition: dict[str, Any], value: Any) -> bool:
    """Check a computed metric value against its declared output shape."""
    output = (definition or {}).get("output") or {}
    shape = output.get("shape")
    if not isinstance(shape, dict):
        return False
    return _check_type(shape, value)


# ---------------------------------------------------------------------------
# Dashboards (org-scoped; cross-org reads return None -> router sends 404)
# ---------------------------------------------------------------------------
def _row_to_dashboard(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "org_id": row.get("org_id"),
        "name": row.get("name"),
        "slug": row.get("slug"),
        "layout": row.get("layout") or {"widgets": []},
        "theme": row.get("theme") or {},
        "is_public": bool(row.get("is_public")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_dashboards(db: Client, org_id: str) -> list[dict[str, Any]]:
    rows = (
        db.table("bi_dashboards").select("*").eq("org_id", org_id)
        .order("created_at").execute()
    ).data or []
    return [_row_to_dashboard(r) for r in rows]


def get_dashboard(db: Client, org_id: str,
                  dashboard_id: str) -> dict[str, Any] | None:
    row = (
        db.table("bi_dashboards").select("*").eq("id", dashboard_id)
        .eq("org_id", org_id).maybe_single().execute()
    ).data
    return _row_to_dashboard(row) if row else None


def create_dashboard(db: Client, org_id: str, name: str, slug: str,
                     layout: dict[str, Any] | None = None,
                     theme: dict[str, Any] | None = None) -> dict[str, Any]:
    theme = validate_theme(theme or {})
    layout = layout or {"widgets": []}
    if not isinstance(layout, dict) or "widgets" not in layout:
        raise HTTPException(status_code=400,
                            detail="layout must be {\"widgets\": [...]}")
    row = (
        db.table("bi_dashboards")
        .insert({
            "id": uuid.uuid4().hex,
            "org_id": org_id,
            "name": name,
            "slug": slug,
            "layout": layout,
            "theme": theme,
            "is_public": False,
        })
        .execute()
    ).data[0]
    return _row_to_dashboard(row)


def update_dashboard(db: Client, org_id: str, dashboard_id: str,
                     patch: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_dashboard(db, org_id, dashboard_id)
    if not existing:
        return None
    updates: dict[str, Any] = {}
    if "name" in patch:
        updates["name"] = patch["name"]
    if "layout" in patch:
        layout = patch["layout"]
        if not isinstance(layout, dict) or "widgets" not in layout:
            raise HTTPException(status_code=400,
                                detail="layout must be {\"widgets\": [...]}")
        updates["layout"] = layout
    if "theme" in patch:
        updates["theme"] = validate_theme(patch["theme"])
    if "is_public" in patch:
        updates["is_public"] = bool(patch["is_public"])
    if updates:
        (db.table("bi_dashboards").update(updates).eq("id", dashboard_id)
         .eq("org_id", org_id).execute())
    return get_dashboard(db, org_id, dashboard_id)


def delete_dashboard(db: Client, org_id: str, dashboard_id: str) -> bool:
    existing = get_dashboard(db, org_id, dashboard_id)
    if not existing:
        return False
    (db.table("bi_embeds").delete().eq("dashboard_id", dashboard_id)
     .eq("org_id", org_id).execute())
    (db.table("bi_dashboards").delete().eq("id", dashboard_id)
     .eq("org_id", org_id).execute())
    return True


# ---------------------------------------------------------------------------
# Embed tokens
# ---------------------------------------------------------------------------
def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_embed_token(db: Client, org_id: str,
                      dashboard_id: str) -> tuple[dict[str, Any], str]:
    """Create an embed token. Returns (embed_row, raw_token shown once)."""
    dashboard = get_dashboard(db, org_id, dashboard_id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="dashboard not found")
    raw_token = secrets.token_urlsafe(32)
    row = (
        db.table("bi_embeds")
        .insert({
            "id": uuid.uuid4().hex,
            "org_id": org_id,
            "dashboard_id": dashboard_id,
            "token_hash": _hash_token(raw_token),
            "revoked": False,
        })
        .execute()
    ).data[0]
    return row, raw_token


def revoke_embed(db: Client, org_id: str, embed_id: str) -> bool:
    row = (
        db.table("bi_embeds").select("id").eq("id", embed_id)
        .eq("org_id", org_id).maybe_single().execute()
    ).data
    if not row:
        return False
    (db.table("bi_embeds").update({"revoked": True}).eq("id", embed_id)
     .eq("org_id", org_id).execute())
    return True


def _is_expired(expires_at: Any) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


def _resolve_embed(db: Client, raw_token: str) -> tuple[dict, dict]:
    """Token -> (embed, dashboard). Raises 401/403/404 per the contract."""
    embed = (
        db.table("bi_embeds").select("*")
        .eq("token_hash", _hash_token(raw_token))
        .maybe_single().execute()
    ).data
    if not embed:
        raise HTTPException(status_code=401, detail="invalid embed token")
    if embed.get("revoked") or _is_expired(embed.get("expires_at")):
        raise HTTPException(status_code=403, detail="embed token revoked")
    dashboard = (
        db.table("bi_dashboards").select("*")
        .eq("id", embed["dashboard_id"]).maybe_single().execute()
    ).data
    # Defense in depth: the embed's org must match the dashboard's org.
    if not dashboard or dashboard.get("org_id") != embed.get("org_id"):
        raise HTTPException(status_code=404, detail="dashboard not found")
    if not dashboard.get("is_public"):
        # is_public is the master kill switch (Rosa Minor 7).
        raise HTTPException(status_code=404, detail="dashboard not found")
    return embed, dashboard


def _snapshot_for(db: Client, org_id: str, connector_key: str,
                  metric_key: str) -> dict[str, Any] | None:
    return (
        db.table("bi_metric_snapshots").select("value, computed_at")
        .eq("org_id", org_id).eq("connector_key", connector_key)
        .eq("metric_key", metric_key).maybe_single().execute()
    ).data


def _render_widgets(db: Client, dashboard: dict[str, Any]) -> list[dict]:
    """Render widgets from snapshots only. Never computes, never writes."""
    definitions = widget_metrics()
    org_id = dashboard["org_id"]
    rendered = []
    for widget in (dashboard.get("layout") or {}).get("widgets", []):
        metric_key = widget.get("metric")
        definition = definitions.get(metric_key)
        base = {"id": widget.get("id"), "metric": metric_key,
                "title": widget.get("title"), "chart": widget.get("chart")}
        if definition is None:
            rendered.append({**base, "error": "unknown metric"})
            continue
        snapshot = _snapshot_for(db, org_id, widget.get("connector") or "",
                                 metric_key)
        if not snapshot:
            rendered.append({**base, "status": "updating"})
            continue
        value = snapshot.get("value")
        if not validate_metric_value(definition, value):
            rendered.append({**base, "error": "shape violation"})
            continue
        rendered.append({**base, "value": value})
    return rendered


def _toll_best_effort(db: Client, org_id: str, event_type: str,
                      idempotency_key: str) -> None:
    try:
        record_toll_event(db, org_id=org_id, event_type=event_type,
                          idempotency_key=idempotency_key)
    except Exception as exc:  # noqa: BLE001 — tolls never break reads (TW-208)
        log.warning("bi toll emit failed", extra={"event": event_type,
                                                 "error": str(exc)})


def render_embed(db: Client, raw_token: str) -> dict[str, Any]:
    """Public embed payload. No JWT; tenant isolation per the invariant."""
    embed, dashboard = _resolve_embed(db, raw_token)
    payload = {
        "dashboard": {
            "name": dashboard.get("name"),
            "slug": dashboard.get("slug"),
            "theme": dashboard.get("theme") or {},
        },
        "widgets": _render_widgets(db, dashboard),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _toll_best_effort(
        db, dashboard["org_id"], "bi.embed_view",
        idempotency_key=f"bi_embed_view:{embed['id']}:{uuid.uuid4().hex}")
    return payload


# ---------------------------------------------------------------------------
# HTML shell for iframe embeds
# ---------------------------------------------------------------------------
def render_embed_html(db: Client, raw_token: str) -> str:
    """Server-rendered HTML page for the iframe snippet."""
    payload = render_embed(db, raw_token)
    theme = payload["dashboard"].get("theme") or {}
    primary = html.escape(str(theme.get("primary_color", "#4f46e5")))
    bg = html.escape(str(theme.get("background_color", "#ffffff")))
    font = html.escape(str(theme.get("font_family", "system-ui, sans-serif")))
    logo = html.escape(str(theme.get("logo_url", "")))
    name = html.escape(str(payload["dashboard"].get("name", "Dashboard")))

    cards = []
    for w in payload["widgets"]:
        title = html.escape(str(w.get("title", "")))
        if w.get("error"):
            cards.append(
                f'<div class="card"><div class="t">{title}</div>'
                f'<div class="err">Unavailable</div></div>')
        elif w.get("status") == "updating":
            cards.append(
                f'<div class="card"><div class="t">{title}</div>'
                f'<div class="muted">Updating…</div></div>')
        else:
            value = w.get("value") or {}
            if isinstance(value, dict) and "points" in value:
                rows = "".join(
                    f"<tr><td>{html.escape(str(p.get('label', '')))}</td>"
                    f"<td class=num>{html.escape(str(p.get('value', '')))}</td></tr>"
                    for p in value.get("points", []) if isinstance(p, dict))
                cards.append(
                    f'<div class="card wide"><div class="t">{title}</div>'
                    f'<table>{rows}</table></div>')
            else:
                num = html.escape(str(value.get("value", "")))
                cards.append(
                    f'<div class="card"><div class="t">{title}</div>'
                    f'<div class="num">{num}</div></div>')
    logo_html = f'<img class="logo" src="{logo}" alt="logo">' if logo else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
:root{{--bi-primary:{primary};--bi-bg:{bg}}}
body{{margin:0;background:var(--bi-bg);color:#16181d;
font-family:{font};font-size:15px}}
.wrap{{max-width:960px;margin:0 auto;padding:24px}}
.masthead{{display:flex;align-items:center;gap:12px;margin-bottom:20px}}
.logo{{height:36px}}.masthead h1{{font-size:22px;margin:0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:16px}}
.card.wide{{grid-column:1/-1}}
.t{{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#5d6470}}
.num{{font-size:34px;font-weight:750;color:var(--bi-primary);margin-top:8px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
td{{padding:6px 4px;border-top:1px solid #eee;font-size:14px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.err,.muted{{color:#5d6470;margin-top:8px}}
.foot{{margin-top:24px;font-size:11px;color:#8a8f9c}}
</style></head>
<body><div class="wrap">
<div class="masthead">{logo_html}<h1>{name}</h1></div>
<div class="grid">{''.join(cards)}</div>
<div class="foot">Powered by Twistor</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Connector sync (authenticated path — the only snapshot writer)
# ---------------------------------------------------------------------------
def sync_connector(db: Client, org_id: str,
                   connector_key: str) -> dict[str, Any]:
    connector = BI_CONNECTOR_REGISTRY.get(connector_key)
    if connector is None:
        raise HTTPException(status_code=400,
                            detail=f"unknown connector: {connector_key}")
    computed = connector.compute(db, org_id) or {}
    definitions = {d["key"]: d for d in connector.metric_definitions()}
    synced: list[str] = []
    for metric_key, value in computed.items():
        definition = definitions.get(metric_key)
        if definition is None or not validate_metric_value(definition, value):
            log.warning("bi sync skipped metric",
                        extra={"connector": connector_key,
                               "metric": metric_key})
            continue
        existing = (
            db.table("bi_metric_snapshots").select("id")
            .eq("org_id", org_id).eq("connector_key", connector_key)
            .eq("metric_key", metric_key).maybe_single().execute()
        ).data
        payload = {
            "org_id": org_id,
            "connector_key": connector_key,
            "metric_key": metric_key,
            "value": value,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        if existing:
            (db.table("bi_metric_snapshots").update(payload)
             .eq("id", existing["id"]).execute())
        else:
            db.table("bi_metric_snapshots").insert(payload).execute()
        synced.append(metric_key)
    _toll_best_effort(
        db, org_id, "bi.connector_sync",
        idempotency_key=f"bi_connector_sync:{org_id}:{connector_key}:"
                        f"{datetime.now(timezone.utc).date().isoformat()}")
    return {"connector": connector_key, "metrics": synced,
            "synced_at": datetime.now(timezone.utc).isoformat()}

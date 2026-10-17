"""White-label BI endpoints (TW-570 M3, Databox wedge)."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import Client

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..services import bi_connectors, bi_service

router = APIRouter(prefix="/bi", tags=["bi"])


class DashboardCreate(BaseModel):
    name: str
    slug: str
    layout: dict[str, Any] | None = None
    theme: dict[str, Any] | None = None


class DashboardPatch(BaseModel):
    name: str | None = None
    layout: dict[str, Any] | None = None
    theme: dict[str, Any] | None = None
    is_public: bool | None = None


def _not_found() -> HTTPException:
    # Cross-org access returns 404, never 403 — no org existence leak.
    return HTTPException(status_code=404, detail="dashboard not found")


# ---------------------------------------------------------------------------
# Authenticated: dashboard CRUD
# ---------------------------------------------------------------------------
@router.get("/dashboards")
def list_dashboards(db: Annotated[Client, Depends(get_db)],
                    auth: Annotated[AuthContext, Depends(get_auth)]):
    return bi_service.list_dashboards(db, auth.org_id)


@router.post("/dashboards", status_code=201)
def create_dashboard(body: DashboardCreate,
                     db: Annotated[Client, Depends(get_db)],
                     auth: Annotated[AuthContext, Depends(get_auth)]):
    return bi_service.create_dashboard(
        db, auth.org_id, body.name, body.slug,
        layout=body.layout, theme=body.theme)


@router.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str,
                  db: Annotated[Client, Depends(get_db)],
                  auth: Annotated[AuthContext, Depends(get_auth)]):
    dashboard = bi_service.get_dashboard(db, auth.org_id, dashboard_id)
    if not dashboard:
        raise _not_found()
    return dashboard


@router.patch("/dashboards/{dashboard_id}")
def patch_dashboard(dashboard_id: str, body: DashboardPatch,
                    db: Annotated[Client, Depends(get_db)],
                    auth: Annotated[AuthContext, Depends(get_auth)]):
    dashboard = bi_service.update_dashboard(
        db, auth.org_id, dashboard_id,
        body.model_dump(exclude_unset=True))
    if not dashboard:
        raise _not_found()
    return dashboard


@router.delete("/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: str,
                     db: Annotated[Client, Depends(get_db)],
                     auth: Annotated[AuthContext, Depends(get_auth)]):
    if not bi_service.delete_dashboard(db, auth.org_id, dashboard_id):
        raise _not_found()
    return {"deleted": True}


@router.get("/dashboards/{dashboard_id}/preview")
def preview_dashboard(dashboard_id: str,
                      db: Annotated[Client, Depends(get_db)],
                      auth: Annotated[AuthContext, Depends(get_auth)]):
    payload = bi_service.preview_dashboard(db, auth.org_id, dashboard_id)
    if not payload:
        raise _not_found()
    return payload


# ---------------------------------------------------------------------------
# Authenticated: embed token issue / revoke
# ---------------------------------------------------------------------------
@router.post("/dashboards/{dashboard_id}/embeds", status_code=201)
def issue_embed(dashboard_id: str,
                db: Annotated[Client, Depends(get_db)],
                auth: Annotated[AuthContext, Depends(get_auth)]):
    try:
        embed_row, raw_token = bi_service.issue_embed_token(
            db, auth.org_id, dashboard_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _not_found()
        raise
    # The raw token is shown ONCE here. It is never stored and never logged.
    return {"id": embed_row.get("id"), "token": raw_token,
            "expires_at": embed_row.get("expires_at")}


@router.delete("/embeds/{embed_id}")
def revoke_embed(embed_id: str,
                 db: Annotated[Client, Depends(get_db)],
                 auth: Annotated[AuthContext, Depends(get_auth)]):
    if not bi_service.revoke_embed(db, auth.org_id, embed_id):
        raise HTTPException(status_code=404, detail="embed not found")
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Public: token-gated embeds (no JWT). Aggregate-only, per the design.
# ---------------------------------------------------------------------------
@router.get("/embed/{token}")
def public_embed(token: str, db: Annotated[Client, Depends(get_db)]):
    return bi_service.render_embed(db, token)


@router.get("/embed/{token}/view", response_class=HTMLResponse)
def public_embed_view(token: str, db: Annotated[Client, Depends(get_db)]):
    return bi_service.render_embed_html(db, token)


# ---------------------------------------------------------------------------
# Authenticated: connectors
# ---------------------------------------------------------------------------
@router.get("/connectors")
def list_connectors(db: Annotated[Client, Depends(get_db)],
                    auth: Annotated[AuthContext, Depends(get_auth)]):
    return [
        {"key": c.key, "label": c.label,
         "metrics": c.metric_definitions()}
        for c in bi_connectors.BI_CONNECTOR_REGISTRY.values()
    ]


@router.post("/connectors/{connector_key}/sync")
def sync_connector(connector_key: str,
                   db: Annotated[Client, Depends(get_db)],
                   auth: Annotated[AuthContext, Depends(get_auth)]):
    return bi_service.sync_connector(db, auth.org_id, connector_key)

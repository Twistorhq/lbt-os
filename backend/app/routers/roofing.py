"""M4 roofing pro pack router (TW-571, JobNimbus wedge).

All endpoints org-scoped via get_auth; cross-org access returns 404
(never 403 — no org existence leak). Supplier contacts are masked in
responses per the PII doctrine (TW-552).
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..services.roofing_service import (
    StormServiceError,
    TakeoffError,
    apply_takeoff,
    fetch_storm_alerts,
    launch_campaign,
    mask_supplier,
    send_purchase_order,
    store_storm_event,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/roofing")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TakeoffIn(BaseModel):
    squares: float = Field(gt=0)
    pitch: str = "4/12"
    waste_pct: float = Field(default=10, ge=0, le=100)
    material: str


class SupplierIn(BaseModel):
    name: str
    branch: Optional[str] = None
    phone: Optional[str] = None
    ordering_email: Optional[str] = None
    account_number: Optional[str] = None


class PurchaseOrderIn(BaseModel):
    supplier_id: str
    job_id: Optional[str] = None
    line_items: list = Field(default_factory=list)


class CampaignIn(BaseModel):
    storm_event_id: Optional[str] = None
    event: Optional[dict] = None  # inline NWS event; stored first
    message_body: str = Field(min_length=1, max_length=320)


# ---------------------------------------------------------------------------
# Takeoff
# ---------------------------------------------------------------------------


@router.post("/quotes/{quote_id}/takeoff")
def quote_takeoff(
    quote_id: str,
    body: TakeoffIn,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    try:
        return apply_takeoff(
            db=db,
            org_id=auth.org_id,
            quote_id=quote_id,
            squares=body.squares,
            pitch=body.pitch,
            waste_pct=body.waste_pct,
            material=body.material,
        )
    except TakeoffError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="quote not found")


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------


@router.get("/suppliers")
def list_suppliers(auth: Annotated[AuthContext, Depends(get_auth)], db=Depends(get_db)):
    rows = (
        db.table("suppliers").select("*").eq("org_id", auth.org_id).execute()
    ).data or []
    return [mask_supplier(r) for r in rows]


@router.post("/suppliers", status_code=201)
def create_supplier(
    body: SupplierIn,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    row = (
        db.table("suppliers")
        .insert(
            {
                "org_id": auth.org_id,
                **body.model_dump(),
            }
        )
        .execute()
    ).data[0]
    return mask_supplier(row)


@router.get("/suppliers/{supplier_id}")
def get_supplier(
    supplier_id: str,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    rows = (
        db.table("suppliers")
        .select("*")
        .eq("id", supplier_id)
        .eq("org_id", auth.org_id)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="supplier not found")
    return mask_supplier(rows[0])


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: str,
    body: SupplierIn,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    rows = (
        db.table("suppliers")
        .update(body.model_dump(exclude_unset=True))
        .eq("id", supplier_id)
        .eq("org_id", auth.org_id)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="supplier not found")
    return mask_supplier(rows[0])


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(
    supplier_id: str,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    (
        db.table("suppliers")
        .delete()
        .eq("id", supplier_id)
        .eq("org_id", auth.org_id)
        .execute()
    )
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------


@router.get("/purchase-orders")
def list_pos(auth: Annotated[AuthContext, Depends(get_auth)], db=Depends(get_db)):
    return (
        db.table("purchase_orders").select("*").eq("org_id", auth.org_id).execute()
    ).data or []


@router.post("/purchase-orders", status_code=201)
def create_po(
    body: PurchaseOrderIn,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    # Supplier must belong to this org (no cross-org linkage).
    sup = (
        db.table("suppliers")
        .select("id")
        .eq("id", body.supplier_id)
        .eq("org_id", auth.org_id)
        .execute()
    ).data or []
    if not sup:
        raise HTTPException(status_code=404, detail="supplier not found")
    row = (
        db.table("purchase_orders")
        .insert(
            {
                "org_id": auth.org_id,
                "supplier_id": body.supplier_id,
                "job_id": body.job_id,
                "line_items": body.line_items,
                "status": "draft",
            }
        )
        .execute()
    ).data[0]
    return row


@router.get("/purchase-orders/{po_id}")
def get_po(
    po_id: str, auth: Annotated[AuthContext, Depends(get_auth)], db=Depends(get_db)
):
    rows = (
        db.table("purchase_orders")
        .select("*")
        .eq("id", po_id)
        .eq("org_id", auth.org_id)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="purchase order not found")
    return rows[0]


@router.patch("/purchase-orders/{po_id}")
def update_po(
    po_id: str,
    body: dict,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    body = {k: v for k, v in body.items() if k in ("line_items", "job_id", "status")}
    if body.get("status") == "sent":
        raise HTTPException(
            status_code=422, detail="use /send to transmit a purchase order"
        )
    rows = (
        db.table("purchase_orders")
        .update(body)
        .eq("id", po_id)
        .eq("org_id", auth.org_id)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="purchase order not found")
    return rows[0]


@router.post("/purchase-orders/{po_id}/send")
def send_po(
    po_id: str, auth: Annotated[AuthContext, Depends(get_auth)], db=Depends(get_db)
):
    try:
        return send_purchase_order(db=db, org_id=auth.org_id, po_id=po_id)
    except TakeoffError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except StormServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="purchase order not found")


# ---------------------------------------------------------------------------
# Storms
# ---------------------------------------------------------------------------


@router.get("/storms/recent")
def storms_recent(
    auth: Annotated[AuthContext, Depends(get_auth)],
    zip: Optional[str] = Query(default=None),
    lat: Optional[float] = Query(default=None),
    lon: Optional[float] = Query(default=None),
    db=Depends(get_db),
):
    """Active roofing-relevant NWS alerts near a ZIP or lat/lon. Keyless."""
    try:
        return {"events": fetch_storm_alerts(lat=lat, lon=lon, zip_code=zip)}
    except StormServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/storms/campaigns", status_code=201)
def create_campaign(
    body: CampaignIn,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db=Depends(get_db),
):
    """Launch a storm campaign to existing customers in affected ZIPs.

    Accepts a stored storm_event_id or an inline NWS event (stored first,
    deduped on nws_event_id).
    """
    try:
        event_id = body.storm_event_id
        if not event_id and body.event:
            event_id = store_storm_event(db=db, org_id=auth.org_id, event=body.event)
        if not event_id:
            raise HTTPException(
                status_code=422, detail="storm_event_id or event is required"
            )
        return launch_campaign(
            db=db,
            org_id=auth.org_id,
            storm_event_id=event_id,
            message_body=body.message_body,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="storm event not found")

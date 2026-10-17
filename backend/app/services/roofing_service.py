"""M4 roofing pro pack service (TW-571, JobNimbus wedge).

Estimating takeoff (exact-cent Decimal math), suppliers + purchase orders,
and storm-triggered campaigns via keyless weather.gov alerts.

PII doctrine (TW-552): supplier phone/email are bronze-layer by necessity
(the PO-send flow must reach the supplier); API responses mask them.
Customer phones are never returned in campaign responses.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

log = logging.getLogger(__name__)


class TakeoffError(ValueError):
    """Bad takeoff input — the router maps this to 422."""


class StormServiceError(RuntimeError):
    """Weather service unreachable — the router maps this to 502."""


# Per-square rates in USD. Documented defaults; a future ticket can move
# these per-org. Material and labor are separate line items.
MATERIAL_RATES: dict[str, dict[str, Decimal]] = {
    "architectural_shingle": {"material": Decimal("145"), "labor": Decimal("95")},
    "metal_standing_seam": {"material": Decimal("380"), "labor": Decimal("160")},
    "tile": {"material": Decimal("520"), "labor": Decimal("220")},
    "tpo": {"material": Decimal("210"), "labor": Decimal("120")},
}

# NWS alert event names that matter to roofers.
_STORM_EVENT_RE = re.compile(r"hail|tornado|thunderstorm|high wind", re.I)

_NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?point={lat},{lon}"
_ZIP_URL = "https://api.zippopotam.us/us/{zip}"
_UA = {"User-Agent": "TwistorTrades/1.0 (holla@twistor.co)"}  # NWS asks for ID


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Estimating takeoff
# ---------------------------------------------------------------------------


def compute_takeoff(squares, pitch: str, waste_pct, material: str) -> dict:
    """Pure takeoff math. Exact cents; raises TakeoffError on bad input."""
    squares = Decimal(str(squares))
    waste_pct = Decimal(str(waste_pct))
    if squares <= 0:
        raise TakeoffError("squares must be positive")
    if waste_pct < 0 or waste_pct > 100:
        raise TakeoffError("waste_pct must be between 0 and 100")
    rates = MATERIAL_RATES.get(material)
    if rates is None:
        raise TakeoffError(f"unknown material: {material}")

    material_squares = _cents(squares * (Decimal("1") + waste_pct / Decimal("100")))
    material_amount = _cents(material_squares * rates["material"])
    labor_amount = _cents(squares * rates["labor"])
    total = _cents(material_amount + labor_amount)

    line_items = [
        {
            "description": f"Material - {material.replace('_', ' ')}",
            "quantity": float(material_squares),
            "unit_price": float(rates["material"]),
            "amount": float(material_amount),
        },
        {
            "description": "Labor - roofing install",
            "quantity": float(squares),
            "unit_price": float(rates["labor"]),
            "amount": float(labor_amount),
        },
    ]
    return {
        "line_items": line_items,
        "total": total,
        "takeoff": {
            "squares": float(squares),
            "pitch": pitch,
            "waste_pct": float(waste_pct),
            "material": material,
            "material_squares": float(material_squares),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def apply_takeoff(
    *, db, org_id: str, quote_id: str, squares, pitch: str, waste_pct, material: str
) -> dict:
    """Compute takeoff, persist to quotes.attributes.takeoff, refresh totals."""
    try:
        result = compute_takeoff(squares, pitch, waste_pct, material)
    except TakeoffError:
        raise

    rows = (
        db.table("quotes").select("*").eq("id", quote_id).eq("org_id", org_id).execute()
    ).data or []
    if not rows:
        raise LookupError("quote not found")
    quote = rows[0]

    attributes = dict(quote.get("attributes") or {})
    attributes["takeoff"] = result["takeoff"]
    db.table("quotes").update(
        {
            "attributes": attributes,
            "line_items": result["line_items"],
            "total": float(result["total"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", quote_id).eq("org_id", org_id).execute()

    try:
        from .toll_service import record_toll_event

        record_toll_event(
            db,
            org_id,
            "takeoff.computed",
            1,
            idempotency_key=f"takeoff:{quote_id}:{result['takeoff']['computed_at']}",
            source_type="quote",
            source_id=quote_id,
        )
    except Exception as exc:  # tolling never breaks the money path (TW-208)
        log.warning("toll emit failed (takeoff): %s", exc)

    return {
        "id": quote_id,
        "line_items": result["line_items"],
        "total": float(result["total"]),
        "attributes": attributes,
    }


# ---------------------------------------------------------------------------
# Suppliers + purchase orders
# ---------------------------------------------------------------------------


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}" if len(local) > 2 else f"***@{domain}"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    return f"(***) ***-{digits[-4:]}" if len(digits) >= 4 else "***"


def mask_supplier(row: dict) -> dict:
    """Public view of a supplier: contacts masked per TW-552."""
    return {
        **row,
        "ordering_email": _mask_email(row.get("ordering_email")),
        "phone": _mask_phone(row.get("phone")),
    }


def _send_supplier_email(*, to: str, subject: str, html: str) -> bool:
    """Send a PO email via the existing Resend path. Returns success."""
    try:
        from .email import _send

        _send(to=to, subject=subject, html=html)
        return True
    except Exception as exc:
        log.warning("supplier email failed: %s", exc)
        return False


def send_purchase_order(*, db, org_id: str, po_id: str) -> dict:
    """Draft → sent: compute exact-cent total, email the supplier."""
    rows = (
        db.table("purchase_orders")
        .select("*")
        .eq("id", po_id)
        .eq("org_id", org_id)
        .execute()
    ).data or []
    if not rows:
        raise LookupError("purchase order not found")
    po = rows[0]
    if po.get("status") != "draft":
        raise TakeoffError("only draft purchase orders can be sent")

    total = Decimal("0")
    for item in po.get("line_items") or []:
        total += Decimal(str(item.get("qty", 0))) * Decimal(
            str(item.get("unit_cost", 0))
        )
    total = _cents(total)

    suppliers = (
        db.table("suppliers")
        .select("*")
        .eq("id", po["supplier_id"])
        .eq("org_id", org_id)
        .execute()
    ).data or []
    supplier = suppliers[0] if suppliers else {}
    ordering_email = supplier.get("ordering_email")
    if not ordering_email:
        raise TakeoffError("supplier has no ordering email")

    lines_html = "".join(
        f"<li>{item.get('qty')} × {item.get('description')} "
        f"@ ${Decimal(str(item.get('unit_cost', 0))):.2f}</li>"
        for item in po.get("line_items") or []
    )
    sent = _send_supplier_email(
        to=ordering_email,
        subject=f"Purchase order from {supplier.get('name', 'a Twistor customer')}",
        html=f"<p>Please fulfill the following order:</p><ul>{lines_html}</ul>"
        f"<p><strong>Total: ${total:.2f}</strong></p>",
    )
    if not sent:
        raise StormServiceError("supplier email failed; PO left as draft")

    db.table("purchase_orders").update(
        {
            "status": "sent",
            "total_cost": float(total),
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", po_id).eq("org_id", org_id).execute()

    try:
        from .toll_service import record_toll_event

        record_toll_event(
            db,
            org_id,
            "po.sent",
            1,
            idempotency_key=f"po:{po_id}:sent",
            source_type="purchase_order",
            source_id=po_id,
        )
    except Exception as exc:
        log.warning("toll emit failed (po.send): %s", exc)

    return {**po, "status": "sent", "total_cost": float(total)}


# ---------------------------------------------------------------------------
# Storm triggers (keyless weather.gov)
# ---------------------------------------------------------------------------


def _http_get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise StormServiceError(f"weather service unreachable: {exc}") from exc


def _nws_get(url: str) -> dict:
    return _http_get_json(url)


def _geocode_zip(zip_code: str) -> tuple[float, float]:
    payload = _http_get_json(_ZIP_URL.format(zip=zip_code))
    places = payload.get("places") or []
    if not places:
        raise StormServiceError(f"unknown zip: {zip_code}")
    return float(places[0]["latitude"]), float(places[0]["longitude"])


def fetch_storm_alerts(
    *, lat: float | None = None, lon: float | None = None, zip_code: str | None = None
) -> list[dict]:
    """Active NWS alerts filtered to roofing-relevant events. Keyless.

    Any failure in the weather path becomes StormServiceError (router → 502);
    we never fabricate alerts and never leak the underlying error shape.
    """
    if zip_code and (lat is None or lon is None):
        lat, lon = _geocode_zip(zip_code)
    if lat is None or lon is None:
        raise StormServiceError("lat/lon or zip is required")
    try:
        payload = _nws_get(_NWS_ALERTS_URL.format(lat=lat, lon=lon))
    except StormServiceError:
        raise
    except Exception as exc:
        raise StormServiceError(f"weather service unreachable: {exc}") from exc
    events = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        event = props.get("event") or ""
        if not _STORM_EVENT_RE.search(event):
            continue
        events.append(
            {
                "nws_event_id": feature.get("id"),
                "event_type": event,
                "headline": props.get("headline"),
                "area_desc": props.get("areaDesc"),
                "severity": props.get("severity"),
                "started_at": props.get("effective"),
                "ends_at": props.get("ends"),
            }
        )
    return events


def _send_sms(*, to: str, body: str) -> bool:
    """Outbound SMS via the existing Twilio sender. Returns success."""
    try:
        from ..config import settings

        if not all(
            [
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from_number,
            ]
        ):
            log.warning("storm SMS skipped: twilio not configured")
            return False
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        client.messages.create(to=to, from_=settings.twilio_from_number, body=body)
        return True
    except Exception as exc:
        log.warning("storm SMS failed: %s", exc)
        return False


def store_storm_event(*, db, org_id: str, event: dict) -> str:
    """Persist an NWS event for an org; dedupe on (org_id, nws_event_id)."""
    nws_id = event.get("nws_event_id")
    existing = (
        db.table("storm_events")
        .select("id")
        .eq("org_id", org_id)
        .eq("nws_event_id", nws_id)
        .execute()
    ).data or []
    if existing:
        return existing[0]["id"]
    row = (
        db.table("storm_events")
        .insert(
            {
                "org_id": org_id,
                "nws_event_id": nws_id,
                "event_type": event.get("event_type"),
                "headline": event.get("headline"),
                "area_desc": event.get("area_desc"),
                "severity": event.get("severity"),
                "started_at": event.get("started_at"),
                "ends_at": event.get("ends_at"),
                "raw": event,
            }
        )
        .execute()
    ).data[0]
    return row["id"]


_OPT_OUT = " Reply STOP to opt out."


def launch_campaign(*, db, org_id: str, storm_event_id: str, message_body: str) -> dict:
    """Storm event → existing customers in affected ZIPs → campaign + leads.

    TCPA (TW-547 Major 2): recipients are the org's EXISTING customers
    (existing business relationship), informational service offer only.
    """
    events = (
        db.table("storm_events")
        .select("*")
        .eq("id", storm_event_id)
        .eq("org_id", org_id)
        .execute()
    ).data or []
    if not events:
        raise LookupError("storm event not found")
    event = events[0]

    zips = set(re.findall(r"\b\d{5}\b", event.get("area_desc") or ""))
    customers = (
        db.table("customers").select("*").eq("org_id", org_id).execute()
    ).data or []
    recipients = [
        c
        for c in customers
        if c.get("phone") and any(z in (c.get("address") or "") for z in zips)
    ]

    campaign = (
        db.table("storm_campaigns")
        .insert(
            {
                "org_id": org_id,
                "storm_event_id": storm_event_id,
                "status": "launched",
                "message_body": message_body,
                "recipient_count": 0,
            }
        )
        .execute()
    ).data[0]

    sent = 0
    for customer in recipients:
        body = message_body + _OPT_OUT
        if _send_sms(to=customer["phone"], body=body):
            sent += 1
        db.table("leads").insert(
            {
                "org_id": org_id,
                "customer_id": customer["id"],
                "name": customer.get("name"),
                "source": "storm_campaign",
                "notes": f"Storm campaign {campaign['id']}: {event.get('headline')}",
            }
        ).execute()
        try:
            from .toll_service import record_toll_event

            record_toll_event(
                db,
                org_id,
                "sms.auto_reply",
                1,
                idempotency_key=f"storm:{campaign['id']}:{customer['id']}",
                source_type="storm_campaign",
                source_id=campaign["id"],
            )
        except Exception as exc:
            log.warning("toll emit failed (storm sms): %s", exc)

    db.table("storm_campaigns").update(
        {"recipient_count": sent, "status": "completed"}
    ).eq("id", campaign["id"]).execute()

    try:
        from .toll_service import record_toll_event

        record_toll_event(
            db,
            org_id,
            "storm.campaign_launched",
            1,
            idempotency_key=f"storm:campaign:{campaign['id']}",
            source_type="storm_campaign",
            source_id=campaign["id"],
        )
    except Exception as exc:
        log.warning("toll emit failed (storm campaign): %s", exc)

    return {
        "id": campaign["id"],
        "storm_event_id": storm_event_id,
        "status": "completed",
        "recipient_count": sent,
        "message_body": message_body,
    }

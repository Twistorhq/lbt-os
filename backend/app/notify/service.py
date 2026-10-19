"""TW-620 Twistor Notify — send orchestration.

One entry point: template render -> cost preview -> least-cost route ->
attempt trail -> delivery ledger -> toll. Every step is recorded; the
ledger is the receipt ("every dollar shows its source").

TW-208: a toll-ledger failure must never fail the notification itself.
"""

from __future__ import annotations

import hashlib
import logging

from supabase import Client

from ..services import toll_service
from . import providers, routing, templates

log = logging.getLogger(__name__)

# Priorities: system messages (rate-limit warnings etc.) skip preference
# checks but never skip the cost preview or the ledger.
PRIORITY_NORMAL = "normal"
PRIORITY_SYSTEM = "system"


def _recipient_hash(recipient: str) -> str:
    return (
        "sha256:" + hashlib.sha256(recipient.strip().lower().encode()).hexdigest()[:12]
    )


def build_provider(name: str, channel: str, config: dict) -> providers.Provider:
    """Adapter factory. Credentials come from the environment, never from
    the database — ntf_providers.config holds non-secret tuning only."""
    if name == "null":
        p = providers.NullProvider()
        p.channel = channel
        return p
    if name == "resend":
        from ..config import settings

        return providers.ResendEmailProvider(
            api_key=getattr(settings, "resend_api_key", None),
            from_addr=config.get("from_addr", "noreply@twistor.co"),
        )
    if name == "twilio_sms":
        from ..config import settings

        return providers.TwilioSmsProvider(
            account_sid=getattr(settings, "twilio_account_sid", None),
            auth_token=getattr(settings, "twilio_auth_token", None),
            from_number=getattr(settings, "twilio_from_number", None),
        )
    raise ValueError(f"unknown notify provider: {name!r}")


def load_provider_configs(db: Client, org_id: str) -> list[routing.ProviderConfig]:
    """Read the org's provider bindings. Falls back to a null pipe when the
    org has configured nothing — Notify never hard-fails for lack of setup."""
    rows: list[dict] = []
    try:
        result = db.table("ntf_providers").select("*").eq("org_id", org_id).execute()
        rows = result.data or []
    except Exception as exc:
        log.warning("notify: provider lookup failed, using null pipe: %s", exc)
    configs: list[routing.ProviderConfig] = []
    for row in rows:
        try:
            adapter = build_provider(
                row["provider_name"], row["channel"], row.get("config") or {}
            )
        except ValueError:
            log.warning("notify: unknown provider %r skipped", row.get("provider_name"))
            continue
        configs.append(
            routing.ProviderConfig(
                name=row["provider_name"],
                channel=row["channel"],
                provider=adapter,
                cost_cents=int(row.get("cost_cents_per_unit", 0)),
                priority=int(row.get("priority", 100)),
                enabled=bool(row.get("is_enabled", True)),
            )
        )
    if not configs:
        null = providers.NullProvider()
        configs.append(
            routing.ProviderConfig(
                name="null",
                channel="email",
                provider=null,
                cost_cents=0,
                priority=100,
                enabled=True,
            )
        )
    return configs


def preview_cost(configs: list[routing.ProviderConfig], channel: str) -> dict:
    """The TW-630 wedge: exact per-send cost BEFORE anything goes out."""
    ordered = routing.order_providers(configs, channel)
    winner = ordered[0]
    return {
        "channel": channel,
        "provider": winner.name,
        "cost_cents": winner.cost_cents,
        "failover_count": len(ordered) - 1,
    }


def send(
    db: Client,
    org_id: str,
    *,
    channel: str,
    to: str,
    subject: str | None = None,
    body: str = "",
    variables: dict | None = None,
    template_name: str | None = None,
    workflow: str | None = None,
    priority: str = PRIORITY_NORMAL,
    provider_configs: list[routing.ProviderConfig] | None = None,
) -> dict:
    """Orchestrate one notification. Returns the ledger message record."""
    variables = variables or {}
    if subject:
        subject = templates.render_template(subject, variables)
    if body:
        body = templates.render_template(body, variables)

    configs = (
        provider_configs
        if provider_configs is not None
        else load_provider_configs(db, org_id)
    )
    preview = preview_cost(configs, channel)

    result, attempts = routing.route_send(
        configs,
        channel,
        to=to,
        subject=subject,
        body=body,
        metadata={"org_id": org_id, "workflow": workflow, "priority": priority},
    )

    message = {
        "org_id": org_id,
        "template_name": template_name,
        "channel": channel,
        "workflow": workflow,
        "priority": priority,
        "recipient": to,  # PII note in design doc: RLS deny-all, never logged
        "recipient_hash": _recipient_hash(to),
        "subject": subject,
        "status": "sent" if result.ok else "failed",
        "provider_used": attempts[-1].provider if attempts else None,
        "cost_preview_cents": preview["cost_cents"],
        "cost_actual_cents": result.cost_cents if result.ok else 0,
        "toll_event_id": None,
    }
    saved = _insert(db, "ntf_messages", message)
    message_id = saved.get("id")
    for i, attempt in enumerate(attempts, start=1):
        _insert(
            db,
            "ntf_attempts",
            {
                "message_id": message_id,
                "provider": attempt.provider,
                "attempt_no": i,
                "status": "sent" if attempt.ok else "failed",
                "error": (attempt.error or "")[:300],
                "latency_ms": attempt.latency_ms,
            },
        )

    # Toll the orchestration. TW-208: ledger failure never fails the send.
    if message_id:
        try:
            toll = toll_service.record_toll_event(
                db,
                org_id,
                "notify.message_sent",
                quantity=1,
                idempotency_key=f"notify:{message_id}",
                source_type="ntf_messages",
                source_id=str(message_id),
            )
            saved["toll_event_id"] = (toll or {}).get("id")
            message["toll_event_id"] = saved["toll_event_id"]
        except Exception as exc:
            log.warning("notify: toll emission failed (send unaffected): %s", exc)
    if not result.ok:
        log.warning(
            "notify: message %s failed on all providers: %s",
            message_id,
            result.error,
        )
    else:
        log.info(
            "notify: sent via %s cost=%dc",
            message["provider_used"],
            message["cost_actual_cents"],
        )
    message["id"] = message_id
    return message


def _insert(db: Client, table: str, row: dict) -> dict:
    result = db.table(table).insert(row).execute()
    data = result.data if result and result.data else []
    if isinstance(data, list) and data:
        return data[0]
    return dict(row)

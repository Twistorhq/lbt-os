"""TW-620 Twistor Notify — drop-in migration shim.

Call sites using direct Twilio/Resend switch one import at a time:

    from app.services import email as email_service        # before
    from app.notify import legacy as email_service         # after

Behavior on day one is pinned to the current provider (no surprise
routing changes): email pins `resend`, SMS pins `twilio_sms`. Orgs
opt into least-cost routing by configuring ntf_providers.
"""

from __future__ import annotations

import logging

from supabase import Client

from . import routing, service

log = logging.getLogger(__name__)


def _pinned_config(name: str, channel: str) -> routing.ProviderConfig:
    adapter = service.build_provider(name, channel, {})
    return routing.ProviderConfig(
        name=name,
        channel=channel,
        provider=adapter,
        cost_cents=adapter.cost_cents_per_unit(),
        priority=100,
        enabled=True,
    )


def send_email(
    db: Client,
    org_id: str,
    *,
    to: str,
    subject: str,
    html: str,
    from_addr: str | None = None,
    workflow: str | None = None,
) -> dict:
    """Same shape as services/email.py::_send call sites. Pinned to resend.
    from_addr threads through to the provider — day-one sender identity is
    unchanged (from_addr or settings.from_email, the old default)."""
    from ..config import settings

    return service.send(
        db,
        org_id,
        channel="email",
        to=to,
        subject=subject,
        body=html,
        workflow=workflow or "legacy_email",
        provider_configs=[_pinned_config("resend", "email")],
        metadata={"from_addr": from_addr or settings.from_email},
    )


def send_sms(
    db: Client,
    org_id: str,
    *,
    to: str,
    body: str,
    workflow: str | None = None,
) -> dict:
    """Same shape as the Twilio path in services/call_service.py. Pinned."""
    return service.send(
        db,
        org_id,
        channel="sms",
        to=to,
        body=body,
        workflow=workflow or "legacy_sms",
        provider_configs=[_pinned_config("twilio_sms", "sms")],
    )

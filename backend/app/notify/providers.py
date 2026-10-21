"""TW-620 Twistor Notify — provider abstraction.

Clean-room from the Novu *mechanism* (provider/channel abstraction, MIT).
No Novu code. Every adapter is written fresh against the public APIs of
the underlying vendor SDKs.

A Provider is a dumb pipe: it sends one message on one channel and reports
what it cost. All intelligence (routing, failover, cost preview, ledger)
lives in routing.py and service.py — never in the adapters.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    cost_cents: int = 0
    latency_ms: int = 0


class Provider(ABC):
    """One channel, one vendor. Fresh code per adapter."""

    name: str = "base"
    channel: str = "email"

    @abstractmethod
    def cost_cents_per_unit(self) -> int:
        """Declared per-message cost in cents. 0 for dev/test pipes."""

    @abstractmethod
    def send(
        self,
        *,
        to: str,
        subject: str | None,
        body: str,
        metadata: dict,
    ) -> ProviderResult:
        """Send one message. Never raises for provider errors — returns
        ProviderResult(ok=False) instead so the router can fail over."""

    def healthy(self) -> bool:
        """Cheap liveness check. Adapters may override with a real probe."""
        return True


class NullProvider(Provider):
    """Dev/test pipe. Sends nothing, always succeeds, costs nothing."""

    name = "null"
    channel = "email"

    def cost_cents_per_unit(self) -> int:
        return 0

    def send(self, *, to, subject, body, metadata) -> ProviderResult:
        started = time.monotonic()
        log.debug("notify(null): would send to sha256:%s", _tag(to))
        return ProviderResult(
            ok=True,
            provider_message_id="null-" + _tag(to),
            cost_cents=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


class ResendEmailProvider(Provider):
    """Email via Resend. SDK imported lazily so the module imports clean
    without the optional dependency."""

    name = "resend"
    channel = "email"

    def __init__(self, api_key: str | None, from_addr: str = "noreply@twistor.co"):
        self._api_key = api_key
        self._from_addr = from_addr

    def cost_cents_per_unit(self) -> int:
        # Resend list: ~$0.10 per 1k emails on paid tiers; declared so the
        # router can compare. Orgs override per-unit cost in ntf_providers.
        return 1

    def send(self, *, to, subject, body, metadata) -> ProviderResult:
        started = time.monotonic()
        if not self._api_key:
            return ProviderResult(ok=False, error="resend api key not configured")
        try:
            import resend

            resend.api_key = self._api_key
            resp = resend.Emails.send(
                {
                    "from": metadata.get("from_addr") or self._from_addr,
                    "to": [to],
                    "subject": subject or "(no subject)",
                    "html": body,
                }
            )
            return ProviderResult(
                ok=True,
                provider_message_id=str(getattr(resp, "id", resp)),
                cost_cents=self.cost_cents_per_unit(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # provider failure -> failover, never raise
            log.warning("notify(resend) send failed: %s", _tag(to))
            return ProviderResult(ok=False, error=str(exc)[:300])


class TwilioSmsProvider(Provider):
    """SMS via Twilio. Dumb pipe — Twilio owns delivery, Notify owns the
    routing decision."""

    name = "twilio_sms"
    channel = "sms"

    def __init__(
        self,
        account_sid: str | None,
        auth_token: str | None,
        from_number: str | None,
    ):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number

    def cost_cents_per_unit(self) -> int:
        # US outbound SMS segment baseline; orgs override in ntf_providers.
        return 2

    def send(self, *, to, subject, body, metadata) -> ProviderResult:
        started = time.monotonic()
        if not all([self._sid, self._token, self._from]):
            return ProviderResult(ok=False, error="twilio credentials not configured")
        try:
            from twilio.rest import Client

            client = Client(self._sid, self._token)
            msg = client.messages.create(to=to, from_=self._from, body=body)
            return ProviderResult(
                ok=True,
                provider_message_id=msg.sid,
                cost_cents=self.cost_cents_per_unit(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            log.warning("notify(twilio_sms) send failed: %s", _tag(to))
            return ProviderResult(ok=False, error=str(exc)[:300])


class PushProviderStub(Provider):
    """Push channel placeholder. Fails loud until a real push provider is
    wired — silent no-ops are how notifications get lost."""

    name = "push_stub"
    channel = "push"

    def cost_cents_per_unit(self) -> int:
        return 0

    def send(self, *, to, subject, body, metadata) -> ProviderResult:
        raise NotImplementedError("push provider not configured")


def _tag(value: str) -> str:
    """Log-safe identifier: truncated sha256, never the raw address/number."""
    import hashlib

    return "sha256:" + hashlib.sha256(value.strip().lower().encode()).hexdigest()[:12]

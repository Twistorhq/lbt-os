"""TW-620 Twistor Notify — least-cost routing + multi-provider failover.

The router is the product: cheapest working provider first, automatic
walk-down on failure, full attempt trail for the debugger. No provider
can hold an org hostage — that is the TW-630 wedge made structural.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .providers import Provider, ProviderResult


class NoProviderAvailable(Exception):
    """No enabled provider exists for the requested channel."""


def _sanitize_error(text: str | None) -> str | None:
    """Provider exception text can echo the destination (Twilio errors
    21211/21608 echo the number; SMTP errors echo the address). Scrub
    before storing, logging, or returning — PII doctrine TW-552 bans
    phone numbers and emails in app logs and API responses.
    Precedent: services/call_service.py::_sanitize_error."""
    if not text:
        return text
    text = re.sub(r"\d{7,}", "[redacted]", text)
    text = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[redacted]", text)
    return text


@dataclass
class ProviderConfig:
    """One org's binding of a provider to a channel."""

    name: str
    channel: str
    provider: Provider
    cost_cents: int  # org-level override; wins over the adapter default
    priority: int = 100  # lower = preferred on cost ties
    enabled: bool = True


@dataclass
class AttemptRecord:
    provider: str
    ok: bool
    error: str | None
    cost_cents: int
    latency_ms: int


def order_providers(
    configs: list[ProviderConfig], channel: str
) -> list[ProviderConfig]:
    """Cheapest enabled provider first for the channel; priority breaks
    ties. Raises NoProviderAvailable when nothing can serve the channel."""
    eligible = [c for c in configs if c.enabled and c.channel == channel]
    if not eligible:
        raise NoProviderAvailable(f"no enabled provider for channel {channel!r}")
    return sorted(eligible, key=lambda c: (c.cost_cents, c.priority))


def route_send(
    configs: list[ProviderConfig],
    channel: str,
    *,
    to: str,
    subject: str | None,
    body: str,
    metadata: dict,
) -> tuple[ProviderResult, list[AttemptRecord]]:
    """Walk cheapest-first; on provider failure try the next. Returns the
    final result plus the full attempt trail (the silent-failure debugger
    reads this)."""
    ordered = order_providers(configs, channel)
    attempts: list[AttemptRecord] = []
    last: ProviderResult | None = None
    for cfg in ordered:
        if not cfg.provider.healthy():
            attempts.append(
                AttemptRecord(
                    provider=cfg.name,
                    ok=False,
                    error="provider unhealthy (health check failed)",
                    cost_cents=0,
                    latency_ms=0,
                )
            )
            continue
        try:
            result = cfg.provider.send(
                to=to, subject=subject, body=body, metadata=metadata
            )
        except Exception as exc:  # adapter bug must not kill the walk
            result = ProviderResult(ok=False, error=f"adapter raised: {exc}"[:300])
        # Stamp the org-level cost, not the adapter default.
        result = ProviderResult(
            ok=result.ok,
            provider_message_id=result.provider_message_id,
            error=result.error,
            cost_cents=cfg.cost_cents if result.ok else 0,
            latency_ms=result.latency_ms,
        )
        attempts.append(
            AttemptRecord(
                provider=cfg.name,
                ok=result.ok,
                error=_sanitize_error(result.error),
                cost_cents=result.cost_cents,
                latency_ms=result.latency_ms,
            )
        )
        last = result
        if result.ok:
            break
    assert last is not None  # order_providers guarantees non-empty
    if not last.ok:
        # attempts[-1].error is already sanitized; reuse it.
        last = ProviderResult(
            ok=False,
            error=f"all {len(attempts)} providers failed; last error: {attempts[-1].error}",
            cost_cents=0,
            latency_ms=sum(a.latency_ms for a in attempts),
        )
    return last, attempts

"""Lookup ports for TW-884: carrier line type + DNC registry scrub.

Both ports follow the TW-284 fail-closed pattern. The line-type lookup
is TW-885 HR-05: it runs before EVERY dial, and an unconfigured lookup
raises LineTypeNotConfigured so the dialer cannot dial blind (an
unknown line type is treated as undialable by the agent). The registry
port backs HR-11 batch scrubbing against the National DNC Registry
(plus state lists for open expansion states); wiring the paid registry
access is a go-live step, so the production factory fails closed too.
Tests inject fakes for both ports.
"""

from __future__ import annotations

import abc


class LineTypeNotConfigured(RuntimeError):
    """No carrier lookup is configured; the dialer fails closed."""


class RegistryNotConfigured(RuntimeError):
    """No DNC registry access is configured; scrubbing fails closed."""


#: Line types the lookup can return. Only "business_landline" is
#: cold-dialable by the agent (HR-06); "wireless" and "residential"
#: require a prior-express-written-consent record (HR-07); everything
#: else fails closed.
LINE_TYPES = (
    "business_landline",
    "wireless",
    "residential",
    "voip_unknown",
    "unknown",
)


class LineTypeProvider(abc.ABC):
    """Carrier lookup: what IS this line, regardless of our labels."""

    @abc.abstractmethod
    def lookup(self, phone_e164: str) -> dict:
        """Return {line_type, carrier, verified} for the number."""


class RegistryProvider(abc.ABC):
    """DNC registry membership check used by batch scrubbing."""

    @abc.abstractmethod
    def is_registered(self, phone_e164: str) -> bool:
        """True when the number appears on a scrubbed registry."""


def get_line_type_provider() -> LineTypeProvider:
    """Factory. No production lookup is wired in slice 1: fail closed.

    Go-live wiring (Twilio Lookup or equivalent) lands with Justynn's
    number purchase; until then any real dial path raises here instead
    of guessing a line type.
    """
    raise LineTypeNotConfigured(
        "carrier line-type lookup is not wired yet (go-live step); "
        "the dialer fails closed (TW-885 HR-05/HR-06)"
    )


def get_registry_provider() -> RegistryProvider:
    """Factory. Registry access is a go-live step: fail closed."""
    raise RegistryNotConfigured(
        "DNC registry access is not wired yet (go-live step); "
        "scrubbing fails closed (TW-885 HR-11)"
    )

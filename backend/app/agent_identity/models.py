"""TW-767 Twistor Agent Identity — persona, coverage, identity record.

Spec 1 of TW-764 (the Instinct-shape build for Twistor Trades) and the
foundation of the TW-766 Agent Model Standard: each agent is a named
employee of the shop, with ONE email address and ONE phone number, a
persistent persona, and coverage hours. Spec 3 assembles task prompts
from the persona record defined here; Spec 6 renders the Agent Card
from the identity record.

TW-787 amendment: a shop runs a ROSTER of agents. The first agent is
the subscription's included everything agent; additional agents are
hires carrying a role and an archetype slot (the TW-788 catalog loads
onto the slot; the slot and its mechanics live here). TW-766 is
unchanged and now reads per agent: one identity, one email, one
number, one persona — per agent, never shared across the roster.

Fail-closed defaults are a property of these models, not of callers:
a default Coverage is mode 'unconfigured' (the agent initiates nothing
until the shop sets hours), and a default RapportSettings is on at low
volume per the TW-766 standard, owner-tunable down to off.

All semantic validation raises IdentityError RAW (via model_post_init,
never field_validator — pydantic wraps field_validator errors in
ValidationError, which would break the fail-loud contract; same pattern
as Twistor Flags' DefinitionError).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class IdentityError(ValueError):
    """An identity/persona/coverage value that cannot be used. Fails loud."""


class _StrictBase(BaseModel):
    """Base that surfaces EVERY construction failure as raw IdentityError."""

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except IdentityError:
            raise
        except ValidationError as e:
            raise IdentityError(str(e)) from e


CADENCES = ("off", "low", "standard")
COVERAGE_MODES = ("unconfigured", "always", "windows")
IDENTITY_STATUSES = ("unprovisioned", "active", "suspended")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# TW-787: the roster role vocabulary. 'everything' is the included
# generalist's role; a hire defaults to 'general' until the customer
# assigns it a specific task in the UI (Spec 6). The migration pins
# the same set as a CHECK constraint so the layers cannot drift.
AGENT_ROLES = (
    "everything",
    "general",
    "hr",
    "sales",
    "marketing",
    "ads",
    "seo",
    "inbox",
    "payroll",
    "frontend_dev",
    "backend_dev",
    "executive",
)

# The included agent's slug: stable, and its email keeps the TW-767
# allocation formula exactly (addresses already issued never move).
PRIMARY_AGENT_SLUG = "primary"

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_archetype_key(key: str | None) -> None:
    """An archetype key is a slug naming a TW-788 catalog entry, or
    None (generalist, no archetype loaded). Format only: the catalog
    itself is TW-788's, and an unknown-but-well-formed key is stored
    as-is so catalog additions never need a registry migration."""
    if key is None:
        return
    if not _SLUG.match(key):
        raise IdentityError(
            f"archetype key {key!r} is not a slug (lowercase letters, "
            "digits, single hyphens)"
        )


class VoiceSettings(_StrictBase):
    """How the agent sounds. voice_id binds to the TW-284/Fonoster voice
    lane when go-live wiring lands; style is the prompt-level direction
    Spec 3 assembles from until then (and after)."""

    voice_id: str | None = None
    style: str = "warm_professional"

    def model_post_init(self, _ctx: Any) -> None:
        if not self.style or not self.style.strip():
            raise IdentityError("voice style must be non-empty")


class RapportSettings(_StrictBase):
    """The agent's personal check-ins with the owner (TW-766 rapport).

    Default ON at low volume: a real employee keeps a running, human
    relationship, not just task traffic. Owner-tunable, down to off.
    """

    enabled: bool = True
    cadence: str = "low"
    max_checkins_per_week: int = 1

    def model_post_init(self, _ctx: Any) -> None:
        if self.cadence not in CADENCES:
            raise IdentityError(
                f"unknown rapport cadence {self.cadence!r}; want one of {CADENCES}"
            )
        if not 0 <= self.max_checkins_per_week <= 7:
            raise IdentityError(
                "max_checkins_per_week must be between 0 and 7, "
                f"got {self.max_checkins_per_week}"
            )
        if self.cadence == "off" and self.max_checkins_per_week != 0:
            raise IdentityError("cadence 'off' requires max_checkins_per_week 0")


class Persona(_StrictBase):
    """The persistent persona record. One record per agent; the phone,
    email, and text lanes all read THIS agent's object, so the employee
    who writes the email is the employee who answered the phone."""

    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    tone: str = "warm, direct, plain-spoken"
    sign_off: str = ""  # empty = sign with the agent's name
    greeting: str = ""  # empty = assemble from name + shop at runtime
    rapport: RapportSettings = Field(default_factory=RapportSettings)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.tone or not self.tone.strip():
            raise IdentityError("persona tone must be non-empty")


class CoverageWindow(_StrictBase):
    """One weekly coverage window. days: 0 = Monday .. 6 = Sunday."""

    days: list[int]
    start: str  # "HH:MM", 24h
    end: str  # "HH:MM", 24h

    def model_post_init(self, _ctx: Any) -> None:
        if not self.days:
            raise IdentityError("coverage window needs at least one day")
        for d in self.days:
            if not 0 <= d <= 6:
                raise IdentityError(f"coverage day {d} out of range 0..6")
        if len(set(self.days)) != len(self.days):
            raise IdentityError("coverage window days must not repeat")
        if not _HHMM.match(self.start or ""):
            raise IdentityError(f"coverage start {self.start!r} is not HH:MM")
        if not _HHMM.match(self.end or ""):
            raise IdentityError(f"coverage end {self.end!r} is not HH:MM")
        if self.start >= self.end:
            raise IdentityError(
                f"coverage window start {self.start} must precede end {self.end} "
                "(overnight windows are two windows, not one)"
            )


class Coverage(_StrictBase):
    """When the agent works. Fail-closed: the default is 'unconfigured',
    which authorizes nothing until the shop sets real hours."""

    mode: str = "unconfigured"
    timezone: str = "UTC"
    windows: list[CoverageWindow] = Field(default_factory=list)

    def model_post_init(self, _ctx: Any) -> None:
        if self.mode not in COVERAGE_MODES:
            raise IdentityError(
                f"unknown coverage mode {self.mode!r}; want one of {COVERAGE_MODES}"
            )
        if not self.timezone or not self.timezone.strip():
            raise IdentityError("coverage timezone must be non-empty")
        if self.mode == "unconfigured" and self.windows:
            raise IdentityError(
                "coverage 'unconfigured' cannot carry windows; "
                "set mode 'windows' to authorize hours"
            )
        if self.mode == "windows" and not self.windows:
            raise IdentityError("coverage mode 'windows' needs at least one window")
        if self.mode == "always" and self.windows:
            raise IdentityError("coverage mode 'always' takes no windows")


class AgentIdentity(_StrictBase):
    """The full identity record as the API returns it: one record the
    Spec 6 Agent Card renders and Spec 3's runtime reads. One record
    per agent; an org's roster is a list of these (TW-787)."""

    id: str | None = None
    org_id: str
    agent_name: str
    agent_slug: str | None = None
    role: str = "everything"
    archetype_key: str | None = None
    included_with_subscription: bool = False
    # Task budget in standard agent units (config: STANDARD_AGENT_
    # POWER_UNITS per hired agent, INCLUDED_AGENT_POWER_UNITS for
    # the included agent). Derived by the service from the config
    # constants, never stored per row. Spec 3's runtime enforces it
    # as queued-task throughput / concurrent task slots.
    # INTERNAL BUILD STANDARD ONLY (Justynn, Oct 29, 2026): the
    # multiplier is never named to the customer — no power language
    # in any API response, UI string, error, or customer-visible
    # doc. exclude=True keeps the value off every serialized view;
    # the runtime reads the attribute in-process, and externally the
    # first agent is simply the standard agent.
    power_units: int = Field(default=1, exclude=True)
    email_address: str | None = None
    phone_number: str | None = None
    persona: Persona = Field(default_factory=Persona)
    coverage: Coverage = Field(default_factory=Coverage)
    status: str = "unprovisioned"
    version: int = 1
    provisioned: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if not self.agent_name or not self.agent_name.strip():
            raise IdentityError("agent name must be non-empty")
        if self.status not in IDENTITY_STATUSES:
            raise IdentityError(
                f"unknown identity status {self.status!r}; "
                f"want one of {IDENTITY_STATUSES}"
            )
        if self.role not in AGENT_ROLES:
            raise IdentityError(
                f"unknown agent role {self.role!r}; want one of {AGENT_ROLES}"
            )
        if self.agent_slug is not None and not _SLUG.match(self.agent_slug):
            raise IdentityError(
                f"agent slug {self.agent_slug!r} is not a slug (lowercase "
                "letters, digits, single hyphens)"
            )
        validate_archetype_key(self.archetype_key)


def default_persona() -> Persona:
    return Persona()


def default_coverage() -> Coverage:
    return Coverage()

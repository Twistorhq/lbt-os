"""TW-650 Twistor Social — fail-loud Pydantic models.

These mirror the rsn_ schema contract (spec Section 4 + the TW-648
fix-round deltas). Fail-loud rules:

- unknown fields are refused (extra='forbid'), never ignored;
- mandatory age ratings have no default: an unrated group, event, or
  series cannot be constructed (spec Section 5.9);
- a check-in carries a proof verdict only. There is deliberately no
  coordinate field on CheckinCreate, so raw coordinates cannot cross
  the wire through this API (spec Section 7.1);
- no model here accepts a date of birth or any raw identity field:
  RSN works with bands and booleans from the verify service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Enum vocabularies — kept in lockstep with the CHECK constraints in
# supabase/migration_social_v1.sql and migration_verify_v1.sql.
AGE_BANDS = ("under_16", "age_16_17", "age_18_20", "age_21_plus")
MIN_AGE_BANDS = ("age_16_plus", "age_18_plus", "age_21_plus")
AUDIENCE_SCOPES = ("group", "connections", "close_friends")
TIER_GATES = ("verified", "host_approved")
RSVP_STATUSES = ("going", "waitlist", "declined", "no_show", "requested")
RSVP_WINDOWS = (
    "keyholder",
    "pillar",
    "fixture",
    "regular",
    "general",
    "waitlist_promotion",
    "host_held",
)
PROOF_TYPES = ("geo_soft", "qr_strong", "host_confirm")
WELFARE_RESPONSES = ("fine", "need_a_minute", "need_help", "no_response")
HELP_ACTIONS = (
    "none",
    "trusted_contact_notified",
    "location_share_started",
    "exit_help_shown",
    "silent_alert",
)
REPORT_CATEGORIES = (
    "harassment",
    "identity_mismatch",
    "unsafe_behavior",
    "fraud",
    "age_misrating",
    "threats",
    "sexual_misconduct",
    "identity_fraud",
    "other_serious",
)
TRUST_EVENT_TYPES = (
    "verification_tier_set",
    "identity_confirm_positive",
    "identity_strike",
    "review_received",
    "no_show",
    "report_substantiated",
    "report_unfounded",
    "vouch_received",
    "checkin_completed",
    "series_streak",
)
STANDING_TIERS = ("verified", "regular", "fixture", "pillar", "keyholder")
TRUST_BANDS = ("trusted", "good_standing", "watch", "restricted")

AgeBand = Literal["under_16", "age_16_17", "age_18_20", "age_21_plus"]
MinAgeBand = Literal["age_16_plus", "age_18_plus", "age_21_plus"]
AudienceScope = Literal["group", "connections", "close_friends"]
TierGate = Literal["verified", "host_approved"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _non_blank(v: str, field_name: str) -> str:
    if not v.strip():
        raise ValueError(f"{field_name} must not be blank")
    return v.strip()


class ProfileIn(_Strict):
    display_name: str = Field(min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=500)
    avatar_ref: str | None = None
    home_metro: str | None = None
    locale: Literal["en", "es"] = "en"
    show_attendance_default: bool = True
    show_standing_default: bool = True

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _non_blank(v, "display_name")


class ProfileOut(ProfileIn):
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class GroupCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    description: str | None = Field(default=None, max_length=2000)
    category: str = Field(min_length=1, max_length=64)
    metro: str = Field(min_length=1, max_length=64)
    visibility: Literal["public", "private", "hidden"] = "public"
    meetup_min_tier: TierGate = "verified"
    # Mandatory at creation, no unrated state (spec Section 5.9).
    min_age_band: MinAgeBand

    @field_validator("name", "category", "metro")
    @classmethod
    def _check_non_blank(cls, v: str) -> str:
        return _non_blank(v, "field")


class EventCreate(_Strict):
    group_id: UUID
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    starts_at: datetime
    ends_at: datetime
    capacity: int | None = Field(default=None, gt=0)
    guests_enabled: bool = True
    min_tier_view: TierGate = "verified"
    min_tier_rsvp: TierGate = "verified"
    # Mandatory at creation, no unrated state (spec Section 5.9).
    min_age_band: MinAgeBand
    venue_type: Literal["venue", "home"] = "venue"
    audience_scope: AudienceScope = "group"
    series_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        return _non_blank(v, "title")

    @model_validator(mode="after")
    def _check_window(self) -> "EventCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class RsvpCreate(_Strict):
    event_id: UUID
    couple_link_id: UUID | None = None
    plus_one_of_rsvp_id: UUID | None = None


class RsvpDecision(_Strict):
    """Host decision on a `requested` RSVP (TW-648 delta). The decider
    comes from the verified token, never from the request body."""

    decision: Literal["approve", "decline"]


class CheckinCreate(_Strict):
    """A check-in is a verdict, not a location. No coordinate fields
    exist on this model by design; extra='forbid' makes any attempt
    to send them fail loud (spec Section 7.1)."""

    event_id: UUID
    proof_type: Literal["geo_soft", "qr_strong", "host_confirm"]


class EncounterConfirmCreate(_Strict):
    event_id: UUID
    subject_id: UUID
    met_them: bool
    identity_matched: bool | None = None


class WelfareResponseIn(_Strict):
    event_id: UUID
    response: Literal["fine", "need_a_minute", "need_help", "no_response"]
    help_action: Literal[
        "none",
        "trusted_contact_notified",
        "location_share_started",
        "exit_help_shown",
        "silent_alert",
    ] = "none"


class ReviewCreate(_Strict):
    event_id: UUID
    subject_type: Literal["person", "event", "guest_pass"]
    subject_id: UUID | None = None
    guest_pass_id: UUID | None = None
    interaction_rating: int | None = Field(default=None, ge=1, le=5)
    good_person: bool | None = None
    would_meet_again: bool | None = None
    would_join_again: bool | None = None
    free_text: str | None = Field(default=None, max_length=2000)


class ReportCreate(_Strict):
    target_type: Literal["user", "event", "post", "comment", "group"]
    target_id: UUID
    subject_user_id: UUID | None = None
    category: Literal[
        "harassment",
        "identity_mismatch",
        "unsafe_behavior",
        "fraud",
        "age_misrating",
        "threats",
        "sexual_misconduct",
        "identity_fraud",
        "other_serious",
    ]
    description: str = Field(min_length=1, max_length=4000)

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        return _non_blank(v, "description")


class TrustEventIn(_Strict):
    user_id: UUID
    event_type: Literal[
        "verification_tier_set",
        "identity_confirm_positive",
        "identity_strike",
        "review_received",
        "no_show",
        "report_substantiated",
        "report_unfounded",
        "vouch_received",
        "checkin_completed",
        "series_streak",
    ]
    weight: float
    ref_type: str | None = None
    ref_id: UUID | None = None


class StandingOut(_Strict):
    """A member's own standing detail. The band is staff machinery
    plus the member's private view; it renders on no other member's
    surface (TW-648 display rules)."""

    standing_tier: Literal["verified", "regular", "fixture", "pillar", "keyholder"]
    band: Literal["trusted", "good_standing", "watch", "restricted"]
    suspended: bool
    age_band: AgeBand

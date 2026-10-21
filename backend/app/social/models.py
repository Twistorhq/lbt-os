"""TW-650 Twistor Social: fail-loud Pydantic models.

These mirror the rsn_ schema contract (spec Section 4 + the TW-648
fix-round deltas). Fail-loud rules:

- unknown fields are refused (extra='forbid'), never ignored;
- mandatory age ratings have no default: an unrated group, event, or
  series cannot be constructed (spec Section 5.9);
- a check-in carries a proof verdict only (verdict, accuracy
  bucket, timestamp for geo proof; token for QR proof; nothing
  client-asserted for host confirm). There is deliberately no
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

from . import geo

# Enum vocabularies, kept in lockstep with the CHECK constraints in
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
# Lockstep with geo.ACCURACY_BUCKETS (asserted by contract test):
# the wire vocabulary for a geo verdict's precision is one list.
ACCURACY_BUCKETS = ("coarse", "fine")
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


class MembershipDecision(_Strict):
    """Owner/admin decision on a pending (`invited`) membership."""

    decision: Literal["approve", "decline"]


class MemberRoleUpdate(_Strict):
    """Owner-only role assignment. The owner's own role is not
    assignable through this model (the service refuses it)."""

    role: Literal["admin", "moderator", "member"]


class EventLocationIn(_Strict):
    """The bronze location write (spec Section 4): exact address,
    written only through the host-only location endpoints, stored
    only in rsn_event_locations, and never part of any read model
    except the gated reveal."""

    venue_name: str | None = Field(default=None, max_length=200)
    street_address: str = Field(min_length=1, max_length=500)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    arrival_instructions: str | None = Field(default=None, max_length=1000)

    @field_validator("street_address")
    @classmethod
    def _check_address(cls, v: str) -> str:
        return _non_blank(v, "street_address")


class SeriesCreate(_Strict):
    """A recurring series (spec Section 4). The rating is mandatory,
    same as events. `venue_type` and `audience_scope` are
    materialization inputs for the instances: rsn_event_series
    carries no columns for them (flagged in the TW-660 evidence doc),
    so instances materialized later inherit them from the series'
    existing instances."""

    group_id: UUID
    title: str = Field(min_length=1, max_length=200)
    description_template: str | None = Field(default=None, max_length=4000)
    recurrence_rule: str = Field(min_length=1, max_length=500)
    first_starts_at: datetime
    default_capacity: int | None = Field(default=None, gt=0)
    default_guests_enabled: bool = True
    default_min_tier_view: TierGate = "verified"
    default_min_tier_rsvp: TierGate = "verified"
    # Mandatory at creation, no unrated state (spec Section 5.9).
    default_min_age_band: MinAgeBand
    venue_type: Literal["venue", "home"] = "venue"
    audience_scope: AudienceScope = "group"

    @field_validator("title")
    @classmethod
    def _check_series_title(cls, v: str) -> str:
        return _non_blank(v, "title")


class CheckinCreate(_Strict):
    """The Section 7.1 wire contract: a verdict, an accuracy bucket,
    and a timestamp, never coordinates. No coordinate fields exist
    on this model by design; extra='forbid' makes any attempt to
    send them fail loud.

    Validation is conditional on the proof type:
    - geo_soft carries the device's geofence verdict payload, and
      the payload is checked by geo.validate_arrival_payload, the
      same fail-closed validator the geo module pins;
    - qr_strong carries the scanned venue token and nothing else
      (the scan is the assertion; a client-supplied verdict beside
      it would be a second, weaker claim);
    - host_confirm carries nothing the client asserts: the host
      confirms out-of-band and the server stamps the arrival.
    """

    event_id: UUID
    proof_type: Literal["geo_soft", "qr_strong", "host_confirm"]
    within_geofence: bool | None = None
    accuracy_bucket: Literal["coarse", "fine"] | None = None
    observed_at: datetime | None = None
    qr_token: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _check_proof_payload(self) -> "CheckinCreate":
        if self.proof_type == "geo_soft":
            if (
                self.within_geofence is None
                or self.accuracy_bucket is None
                or self.observed_at is None
            ):
                raise ValueError(
                    "geo_soft check-in requires within_geofence, "
                    "accuracy_bucket, and observed_at"
                )
            if self.qr_token is not None:
                raise ValueError("geo_soft check-in must not carry a qr_token")
            # Bind the wire contract to the geo module's validator so
            # the two can never drift apart.
            try:
                geo.validate_arrival_payload(
                    {
                        "within_geofence": self.within_geofence,
                        "accuracy_bucket": self.accuracy_bucket,
                    }
                )
            except geo.GeoError as exc:
                raise ValueError(str(exc)) from exc
        elif self.proof_type == "qr_strong":
            if self.qr_token is None:
                raise ValueError("qr_strong check-in requires a qr_token")
            if (
                self.within_geofence is not None
                or self.accuracy_bucket is not None
                or self.observed_at is not None
            ):
                raise ValueError(
                    "qr_strong check-in carries the token only, no geo verdict fields"
                )
        else:  # host_confirm
            if (
                self.within_geofence is not None
                or self.accuracy_bucket is not None
                or self.observed_at is not None
                or self.qr_token is not None
            ):
                raise ValueError(
                    "host_confirm check-in carries no client-asserted payload"
                )
        return self


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

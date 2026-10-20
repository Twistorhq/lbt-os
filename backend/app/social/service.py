"""TW-650 Twistor Social — service layer (Phase 3 skeleton).

Every function here is an honest stub: it names the build phase that
implements it and raises NotImplementedError. The router converts
that to a 501, so the skeleton never pretends to work (spec Section
13 sequencing: Phase 4 groups/events/RSVP/reveal, Phase 5 check-in
loop and trust, Phase 6 safety system).

Contracts are fixed now so Sofia's surfaces and the feature phases
build against stable signatures:
- db is the Supabase client (app.database.get_db), service role only;
  every table is RLS deny-all to anything else.
- user_id always comes from the verified Identity token, never from
  a request body.
- reveal_event_location is fail-closed by doctrine: until Phase 4
  lands the full condition set (band attestation, tier floor, trust
  band, block check, audience scope, reveal window, reveal logging),
  it returns nothing, to anyone, ever.
"""

from __future__ import annotations

from uuid import UUID

from . import models

_PHASE_4 = "Phase 4 (tasks.md items 15-18)"
_PHASE_5 = "Phase 5 (tasks.md items 19-23)"
_PHASE_6 = "Phase 6 (tasks.md items 24-27)"


def _todo(phase: str) -> NotImplementedError:
    return NotImplementedError(f"TW-650 skeleton: implemented in {phase}")


def get_my_profile(db, user_id: UUID) -> models.ProfileOut:
    raise _todo(_PHASE_4)


def upsert_my_profile(
    db, user_id: UUID, payload: models.ProfileIn
) -> models.ProfileOut:
    raise _todo(_PHASE_4)


def create_group(db, user_id: UUID, payload: models.GroupCreate) -> dict:
    raise _todo(_PHASE_4)


def get_group(db, group_id: UUID) -> dict:
    raise _todo(_PHASE_4)


def create_event(db, user_id: UUID, payload: models.EventCreate) -> dict:
    raise _todo(_PHASE_4)


def get_event(db, event_id: UUID) -> dict:
    raise _todo(_PHASE_4)


def request_rsvp(db, user_id: UUID, payload: models.RsvpCreate) -> dict:
    raise _todo(_PHASE_4)


def decide_rsvp(
    db, host_user_id: UUID, rsvp_id: UUID, payload: models.RsvpDecision
) -> dict:
    raise _todo(_PHASE_4)


def reveal_event_location(db, user_id: UUID, event_id: UUID) -> dict:
    # Fail-closed: no stub path may ever return a location.
    raise _todo(_PHASE_4)


def create_checkin(db, user_id: UUID, payload: models.CheckinCreate) -> dict:
    raise _todo(_PHASE_5)


def create_report(db, reporter_id: UUID, payload: models.ReportCreate) -> dict:
    raise _todo(_PHASE_6)

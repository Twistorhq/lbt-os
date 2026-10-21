"""TW-650/TW-660 Twistor Social: service layer.

Phase 4 core loop (TW-660) is implemented here: groups, events,
recurring series, RSVPs, and the location reveal, over the merged
rsn_ schema (TW-650), plus the member-facing read of the TW-651 gold
events index. Profiles remain a later Phase 4 slice; check-ins are
Phase 5 and reports Phase 6, and those stubs still raise honestly.

Contracts (from the TW-650 skeleton, unchanged):

- db is the Supabase client (app.database.get_db), service role only;
  every table is RLS deny-all to anything else.
- user_id always comes from the verified Identity token, never from
  a request body.
- reads are viewer-relative: get_group / get_event / get_series /
  get_listing take the viewer's user_id because the band filter
  (spec Section 5.9), audience scope, and block checks that make a
  read legal are all computed against a specific viewer.

Cross-cutting enforcement (TW-660 brief, spec Sections 5.9-5.11):

- Member context is resolved per request from the verify service's
  own tables: vfy_verification_states (status + tier) and
  vfy_age_attestations (band, via the identity anchor) are what RSN
  reads; RSN never touches bronze document data. Standing and the
  safety band come from rsn_trust_scores (the trust job's output;
  absent a row, a member reads as Verified / good standing).
- Voice: every write requires a passed T2 verdict plus a live band
  attestation. T1 (vouches and history) unlocks nothing (spec
  Section 11). An unverified account is the ghost town: read-only
  skeleton surfaces, and every write refused at this layer. Token
  scope minting is Identity-side; this per-request re-check is the
  enforcement that binds until scopes land, and it is also what
  makes a revocation or ban land immediately (spec Section 5.11).
- Band invisibility is fail-closed and applied before any count or
  page is produced: under-band and (for non-top-band members)
  unrated content does not exist on member surfaces, and a shared
  link to it answers the same NotFoundError as a target that was
  never there.
- The exact location lives only in rsn_event_locations (bronze) and
  leaves this layer only through reveal_event_location, which checks
  the full Section 8 condition set at request time and logs every
  successful reveal to rsn_location_reveals. No list or detail
  payload ever carries an address, coordinates, or arrival
  instructions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from dateutil.rrule import rrulestr

from . import models

log = logging.getLogger(__name__)

_PHASE_5 = "Phase 5 (tasks.md items 19-23)"
_PHASE_6 = "Phase 6 (tasks.md items 24-27)"


def _todo(phase: str) -> NotImplementedError:
    return NotImplementedError(f"TW-650 skeleton: implemented in {phase}")


# ---------------------------------------------------------------------------
# Errors. The router maps these: NotFoundError is the standard
# restricted shell (Section 5.9: an invisible target answers exactly
# like a nonexistent one), ForbiddenError is an entitlement refusal
# the caller may know about, ConflictError is a state conflict.
# ---------------------------------------------------------------------------


class SocialError(Exception):
    """Base class for service-layer refusals."""


class NotFoundError(SocialError):
    def __init__(self, detail: str = "not found"):
        super().__init__(detail)


class ForbiddenError(SocialError):
    pass


class ConflictError(SocialError):
    pass


# ---------------------------------------------------------------------------
# Constants (spec Sections 5.8-5.10, 7.5, 7.7; reward catalog values)
# ---------------------------------------------------------------------------

BAND_RANK = {"under_16": 0, "age_16_17": 1, "age_18_20": 2, "age_21_plus": 3}
MIN_BAND_RANK = {"age_16_plus": 1, "age_18_plus": 2, "age_21_plus": 3}
BAND_LABEL = {"age_16_plus": "16+", "age_18_plus": "18+", "age_21_plus": "21+"}
STANDING_RANK = {
    "verified": 0,
    "regular": 1,
    "fixture": 2,
    "pillar": 3,
    "keyholder": 4,
}
# Host capacity by standing (Section 7.7): below Fixture 12, Fixture
# 25, Pillar 50, Keyholder 100.
HOST_CAPACITY = {
    "verified": 12,
    "regular": 12,
    "fixture": 25,
    "pillar": 50,
    "keyholder": 100,
}
# RSVP early-access waves (Section 7.7 + reward catalog): how long
# before the general opening (rsn_events.rsvp_opens_at, TW-661) each
# standing tier may RSVP. Verified is the general opening itself.
RSVP_WAVE_EARLY_ACCESS = {
    "verified": timedelta(hours=0),
    "regular": timedelta(hours=12),
    "fixture": timedelta(hours=24),
    "pillar": timedelta(hours=48),
    "keyholder": timedelta(hours=72),
}
# Waitlist promotion order (Section 7.5): band, then RSVP time.
TRUST_BAND_PROMOTION_ORDER = {
    "trusted": 0,
    "good_standing": 1,
    "watch": 2,
    "restricted": 3,
}
HOSTING_BANDS = ("trusted", "good_standing")
VENUE_REVEAL_WINDOW = timedelta(hours=48)
HOME_REVEAL_WINDOW = timedelta(hours=3)
SERIES_HORIZON_DAYS = 28
# rsn_event_series carries no default-duration column; a materialized
# instance runs two hours unless a later schema slice says otherwise
# (flagged in the TW-660 evidence doc).
SERIES_INSTANCE_DURATION = timedelta(hours=2)


# ---------------------------------------------------------------------------
# Clock + row plumbing. The service speaks the small fluent subset of
# the Supabase client that the whole codebase uses; timestamps are
# normalized on read because the real client returns ISO strings.
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_now(now: datetime | None) -> datetime:
    return now if now is not None else _utcnow()


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value) -> str | None:
    parsed = _parse_ts(value)
    return parsed.isoformat() if parsed else None


def _fetch(db, table: str, **filters) -> list[dict]:
    query = db.table(table).select("*")
    for col, val in filters.items():
        query = query.eq(col, val)
    return query.execute().data or []


def _fetch_one(db, table: str, **filters) -> dict | None:
    rows = _fetch(db, table, **filters)
    return rows[0] if rows else None


def _insert(db, table: str, row: dict) -> dict:
    data = db.table(table).insert(row).execute().data
    return data[0] if data else row


def _update_by_id(db, table: str, row_id, patch: dict) -> dict | None:
    data = db.table(table).update(patch).eq("id", str(row_id)).execute().data
    return data[0] if data else None


def _delete_by_id(db, table: str, row_id) -> None:
    db.table(table).delete().eq("id", str(row_id)).execute()


# ---------------------------------------------------------------------------
# Member context: the per-request resolution every rule reads from.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberContext:
    user_id: str
    verified: bool
    tier: int
    band: str | None
    standing_tier: str
    trust_band: str
    banned: bool
    anchor_hash: str | None

    @property
    def band_rank(self) -> int:
        return BAND_RANK.get(self.band or "", 0)

    @property
    def can_write(self) -> bool:
        """Voice (Sections 5.10-5.11): a passed T2 verdict. The band
        gate is applied separately by _require_voice, because a
        verified member whose attestation is gone has a verdict
        record but nothing a gate may lawfully read."""
        return self.verified and self.tier >= 2 and not self.banned

    @property
    def is_skeleton(self) -> bool:
        """Skeleton viewers (the ghost town) are everyone without
        voice, plus anyone with no live band attestation: Section
        5.9 puts a member with no attestation in the ghost town
        whatever their self-attested age."""
        return not self.can_write or self.band_rank == 0


def _live_band(attestations: list[dict]) -> str | None:
    """Resolve the one live age band deterministically.

    The verify schema keys attestations by anchor, so a single live
    row is the contract. If an engine fault ever leaves two, the
    newest band_as_of governs (computed_at breaks that tie), never
    storage order: a safety input must not depend on which row a
    query happened to return first. Two live rows tied on both
    timestamps with different bands are ambiguous and fail closed
    to no band."""
    if not attestations:
        return None

    def _key(row: dict) -> tuple[str, str]:
        return (
            str(row.get("band_as_of") or ""),
            str(row.get("computed_at") or ""),
        )

    ordered = sorted(attestations, key=_key, reverse=True)
    top = ordered[0]
    for other in ordered[1:]:
        if _key(other) == _key(top) and other.get("band") != top.get("band"):
            return None
    return top.get("band")


def get_member_context(db, user_id: UUID | str) -> MemberContext:
    uid = str(user_id)
    verified = False
    tier = 0
    band = None
    anchor = None
    banned = False
    state = _fetch_one(db, "vfy_verification_states", user_id=uid)
    if state is not None:
        anchor = state.get("identity_anchor_hash")
        tier = int(state.get("current_tier") or 0)
        verified = state.get("status") == "verified"
        if anchor:
            attestations = (
                db.table("vfy_age_attestations")
                .select("*")
                .eq("identity_anchor_hash", anchor)
                .is_("superseded_at", "null")
                .execute()
                .data
                or []
            )
            if attestations:
                band = _live_band(attestations)
            anchor_row = _fetch_one(db, "vfy_identity_anchors", anchor_hash=anchor)
            if anchor_row is not None and anchor_row.get("is_banned"):
                banned = True
            for ban in _fetch(db, "rsn_bans", identity_anchor_hash=anchor):
                if ban.get("scope") == "platform" and ban.get("lifted_at") is None:
                    banned = True
    scores = _fetch_one(db, "rsn_trust_scores", user_id=uid)
    standing = scores.get("standing_tier") if scores else "verified"
    trust_band = scores.get("band") if scores else "good_standing"
    return MemberContext(
        user_id=uid,
        verified=verified,
        tier=tier,
        band=band,
        standing_tier=standing or "verified",
        trust_band=trust_band or "good_standing",
        banned=banned,
        anchor_hash=anchor,
    )


def _require_not_banned(ctx: MemberContext) -> None:
    if ctx.banned:
        # Section 5.11: a banned identity gets less than the ghost
        # town: refusal, on reads as well as writes.
        raise ForbiddenError("account refused")


def _require_voice(ctx: MemberContext) -> None:
    _require_not_banned(ctx)
    if not ctx.can_write:
        raise ForbiddenError(
            "verification required: speaking requires a passed T2 verification"
        )
    if ctx.band_rank == 0:
        raise ForbiddenError("no live age-band attestation")


def _require_hosting_band(ctx: MemberContext) -> None:
    # Section 7.5: watch band and below cannot host until the band
    # recovers or staff reviews.
    if ctx.trust_band not in HOSTING_BANDS:
        raise ForbiddenError(
            f"hosting is suspended while the safety band is {ctx.trust_band}"
        )


# ---------------------------------------------------------------------------
# Relationship helpers (membership, blocks, close friends, couples)
# ---------------------------------------------------------------------------


def _membership(db, group_id, user_id) -> dict | None:
    return _fetch_one(
        db, "rsn_group_memberships", group_id=str(group_id), user_id=str(user_id)
    )


def _active_member_ids(db, group_id) -> list[str]:
    rows = _fetch(db, "rsn_group_memberships", group_id=str(group_id))
    return [r["user_id"] for r in rows if r.get("status") == "active"]


def _member_count(db, group_id) -> int:
    return len(_active_member_ids(db, group_id))


def _blocked_between(db, a, b) -> bool:
    if _fetch_one(db, "rsn_blocks", blocker_id=str(a), blocked_id=str(b)):
        return True
    return (
        _fetch_one(db, "rsn_blocks", blocker_id=str(b), blocked_id=str(a)) is not None
    )


def _is_close_friend(db, owner_id, friend_id) -> bool:
    return (
        _fetch_one(
            db,
            "rsn_close_friends",
            owner_id=str(owner_id),
            friend_id=str(friend_id),
        )
        is not None
    )


def _couple_partner_ids(db, user_id) -> set[str]:
    partners: set[str] = set()
    uid = str(user_id)
    for row in _fetch(db, "rsn_couple_links", user_low_id=uid):
        if row.get("status") == "active":
            partners.add(row["user_high_id"])
    for row in _fetch(db, "rsn_couple_links", user_high_id=uid):
        if row.get("status") == "active":
            partners.add(row["user_low_id"])
    return partners


def _member_count_band(count: int) -> str:
    if count <= 0:
        return "0"
    if count < 10:
        return "under 10"
    if count < 25:
        return "10 to 24"
    if count <= 50:
        return "25 to 50"
    if count <= 100:
        return "51 to 100"
    return "over 100"


# ---------------------------------------------------------------------------
# Profiles (later Phase 4 slice; outside TW-660 scope, still honest)
# ---------------------------------------------------------------------------


def get_my_profile(db, user_id: UUID) -> models.ProfileOut:
    raise _todo("a later Phase 4 slice (profiles are outside TW-660)")


def upsert_my_profile(
    db, user_id: UUID, payload: models.ProfileIn
) -> models.ProfileOut:
    raise _todo("a later Phase 4 slice (profiles are outside TW-660)")


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _group_card(db, group: dict) -> dict:
    """The ghost-town directory card (Section 5.11): the closed
    preview field list, nothing more. No description, no host
    identity, counts as ranges, never exact."""
    return {
        "id": group["id"],
        "name": group["name"],
        "slug": group["slug"],
        "category": group["category"],
        "metro": group["metro"],
        "min_age_band": group["min_age_band"],
        "band_label": BAND_LABEL[group["min_age_band"]],
        "member_count_band": _member_count_band(_member_count(db, group["id"])),
        "host_verified": True,
        "shell": "preview",
    }


def _group_out(db, group: dict, membership: dict | None) -> dict:
    return {
        "id": group["id"],
        "name": group["name"],
        "slug": group["slug"],
        "description": group.get("description"),
        "category": group["category"],
        "metro": group["metro"],
        "visibility": group["visibility"],
        "meetup_min_tier": group["meetup_min_tier"],
        "min_age_band": group["min_age_band"],
        "band_label": BAND_LABEL[group["min_age_band"]],
        "created_by": group["created_by"],
        "created_at": _iso(group.get("created_at")),
        "member_count": _member_count(db, group["id"]),
        "viewer_role": (
            membership.get("role")
            if membership is not None and membership.get("status") == "active"
            else None
        ),
        "viewer_membership_status": (
            membership.get("status") if membership is not None else None
        ),
    }


def _group_visible_to_member(db, ctx: MemberContext, group: dict) -> bool:
    """Band, block, and hidden rules for a verified viewer. Private
    groups are visible as directory cards; their detail is members
    only (get_group applies that second step)."""
    if ctx.band_rank < MIN_BAND_RANK[group["min_age_band"]]:
        return False
    if _blocked_between(db, ctx.user_id, group["created_by"]):
        return False
    if group["visibility"] == "hidden":
        # A hidden group exists only for its active members. A
        # pending (invited) requester holds no visibility until the
        # owner or an admin approves (Rosa M2): the directory,
        # detail, and hosting paths all read through this gate.
        membership = _membership(db, group["id"], ctx.user_id)
        if membership is None or membership.get("status") != "active":
            return False
    return True


def create_group(db, user_id: UUID, payload: models.GroupCreate, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    # Section 7.7: group creation requires Regular standing or
    # better, and a safety band of trusted or good standing.
    if STANDING_RANK[ctx.standing_tier] < STANDING_RANK["regular"]:
        raise ForbiddenError("group creation requires Regular standing or better")
    _require_hosting_band(ctx)
    if _fetch_one(db, "rsn_groups", slug=payload.slug) is not None:
        raise ConflictError("slug is already taken")
    now = _resolve_now(now)
    group = _insert(
        db,
        "rsn_groups",
        {
            "id": str(uuid4()),
            "name": payload.name,
            "slug": payload.slug,
            "description": payload.description,
            "category": payload.category,
            "metro": payload.metro,
            "visibility": payload.visibility,
            "meetup_min_tier": payload.meetup_min_tier,
            "min_age_band": payload.min_age_band,
            "created_by": ctx.user_id,
            "created_at": now,
            "updated_at": now,
        },
    )
    membership = _insert(
        db,
        "rsn_group_memberships",
        {
            "id": str(uuid4()),
            "group_id": group["id"],
            "user_id": ctx.user_id,
            "role": "owner",
            "status": "active",
            "created_at": now,
        },
    )
    return _group_out(db, group, membership)


def list_groups(
    db, viewer_user_id: UUID, *, metro=None, category=None, now=None
) -> list[dict]:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    groups = _fetch(db, "rsn_groups")
    if metro is not None:
        groups = [g for g in groups if g.get("metro") == metro]
    if category is not None:
        groups = [g for g in groups if g.get("category") == category]
    if ctx.is_skeleton:
        # The ghost directory: public, lowest band, previews only.
        cards = [
            _group_card(db, g)
            for g in groups
            if g["visibility"] == "public" and g["min_age_band"] == "age_16_plus"
        ]
        return sorted(cards, key=lambda c: c["name"])
    visible = [g for g in groups if _group_visible_to_member(db, ctx, g)]
    out = []
    for group in visible:
        membership = _membership(db, group["id"], ctx.user_id)
        out.append(_group_out(db, group, membership))
    return sorted(out, key=lambda c: c["name"])


def get_group(db, viewer_user_id: UUID, group_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(group_id))
    if group is None:
        raise NotFoundError()
    if ctx.is_skeleton:
        if group["visibility"] != "public" or group["min_age_band"] != "age_16_plus":
            raise NotFoundError()
        shell = _group_card(db, group)
        shell["next_gathering"] = _next_gathering(db, group, _resolve_now(now))
        return shell
    if not _group_visible_to_member(db, ctx, group):
        raise NotFoundError()
    membership = _membership(db, group["id"], ctx.user_id)
    if group["visibility"] == "private" and not (
        membership is not None and membership.get("status") == "active"
    ):
        raise NotFoundError()
    return _group_out(db, group, membership)


def _next_gathering(db, group: dict, now: datetime) -> dict | None:
    """The ghost shell's next public gathering, in index form only:
    title, start, coarse area, band. No host, no location."""
    events = _fetch(db, "rsn_events", group_id=group["id"])
    upcoming = []
    for event in events:
        if event.get("status") != "published":
            continue
        # The shell is public index content (Section 5.11), so only
        # a group-scope gathering may appear on it. Connections and
        # close_friends scopes are audience-restricted (Section 8):
        # neither admits a non-member, and their titles and times
        # never reach an unverified viewer. The check is a positive
        # allowlist, so any future stricter scope fails closed here.
        if (event.get("audience_scope") or "group") != "group":
            continue
        if MIN_BAND_RANK[event["min_age_band"]] > MIN_BAND_RANK["age_16_plus"]:
            continue
        starts = _parse_ts(event.get("starts_at"))
        if starts is not None and starts >= now:
            upcoming.append((starts, event))
    if not upcoming:
        return None
    starts, event = min(upcoming, key=lambda pair: pair[0])
    return {
        "title": event["title"],
        "starts_at": starts.isoformat(),
        "metro": group["metro"],
        "min_age_band": event["min_age_band"],
    }


def _membership_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "group_id": row["group_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "status": row["status"],
        "created_at": _iso(row.get("created_at")),
    }


def join_group(db, user_id: UUID, group_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(group_id))
    if group is None:
        raise NotFoundError()
    if ctx.band_rank < MIN_BAND_RANK[group["min_age_band"]]:
        raise NotFoundError()
    if _blocked_between(db, ctx.user_id, group["created_by"]):
        raise NotFoundError()
    existing = _membership(db, group["id"], ctx.user_id)
    if existing is not None:
        if existing.get("status") == "active":
            return _membership_out(existing)
        if existing.get("status") == "invited":
            return _membership_out(existing)
        if existing.get("status") == "removed":
            raise ForbiddenError("removed members cannot rejoin")
        # status "left": fall through and re-join on the same row.
    # Private and hidden groups land joins as pending requests the
    # owner or an admin decides (hidden: Rosa M2, decided by Justynn
    # 2026-10-20; the id is a request, not an invitation).
    pending = group["visibility"] in ("private", "hidden") or (
        group["meetup_min_tier"] == "host_approved"
    )
    status = "invited" if pending else "active"
    now = _resolve_now(now)
    if existing is not None:
        updated = _update_by_id(
            db, "rsn_group_memberships", existing["id"], {"status": status}
        )
        return _membership_out(updated or existing)
    row = _insert(
        db,
        "rsn_group_memberships",
        {
            "id": str(uuid4()),
            "group_id": group["id"],
            "user_id": ctx.user_id,
            "role": "member",
            "status": status,
            "created_at": now,
        },
    )
    return _membership_out(row)


def decide_membership(
    db,
    actor_user_id: UUID,
    group_id: UUID,
    member_user_id: UUID,
    payload: models.MembershipDecision,
    *,
    now=None,
) -> dict:
    ctx = get_member_context(db, actor_user_id)
    _require_voice(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(group_id))
    if group is None:
        raise NotFoundError()
    actor = _membership(db, group["id"], ctx.user_id)
    if (
        actor is None
        or actor.get("status") != "active"
        or actor.get("role") not in ("owner", "admin")
    ):
        raise ForbiddenError("only the group owner or an admin decides membership")
    target = _membership(db, group["id"], str(member_user_id))
    if target is None or target.get("status") != "invited":
        raise ConflictError("no pending membership request")
    if payload.decision == "approve":
        updated = _update_by_id(
            db, "rsn_group_memberships", target["id"], {"status": "active"}
        )
        return _membership_out(updated or target)
    # Declined requests leave no membership: memberships are owned,
    # ephemeral rows, not evidence (schema header, TW-650).
    _delete_by_id(db, "rsn_group_memberships", target["id"])
    return {
        "group_id": group["id"],
        "user_id": str(member_user_id),
        "status": "declined",
    }


def set_member_role(
    db,
    actor_user_id: UUID,
    group_id: UUID,
    member_user_id: UUID,
    payload: models.MemberRoleUpdate,
    *,
    now=None,
) -> dict:
    ctx = get_member_context(db, actor_user_id)
    _require_voice(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(group_id))
    if group is None:
        raise NotFoundError()
    actor = _membership(db, group["id"], ctx.user_id)
    if actor is None or actor.get("status") != "active" or actor.get("role") != "owner":
        raise ForbiddenError("only the group owner assigns roles")
    target = _membership(db, group["id"], str(member_user_id))
    if target is None:
        raise NotFoundError()
    if target.get("role") == "owner":
        raise ConflictError("the owner's role cannot be reassigned")
    updated = _update_by_id(
        db, "rsn_group_memberships", target["id"], {"role": payload.role}
    )
    return _membership_out(updated or target)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _event_location_row(db, event_id) -> dict | None:
    return _fetch_one(db, "rsn_event_locations", event_id=str(event_id))


def _going_count(db, event_id) -> int:
    rows = _fetch(db, "rsn_rsvps", event_id=str(event_id))
    return sum(1 for r in rows if r.get("status") == "going")


def _release_expired_holds(db, event_id, now: datetime) -> None:
    """Lazy half of the TW-661 release rules: a hold whose
    release_at has passed settles to 'expired' the next time the
    event's capacity is consulted (the SQL function
    rsn_release_expired_seat_holds is the sweeper half)."""
    for hold in _fetch(db, "rsn_event_seat_holds", event_id=str(event_id)):
        if hold.get("status") != "held":
            continue
        release_at = _parse_ts(hold.get("release_at"))
        if release_at is not None and release_at <= now:
            _update_by_id(
                db,
                "rsn_event_seat_holds",
                hold["id"],
                {"status": "expired", "released_at": now},
            )


def _active_held_seats(db, event_id, now: datetime) -> int:
    """Seats a host is holding against this event right now. Held
    seats are counted here and nowhere else: they are not RSVPs, so
    _going_count never sees them, and a seat is never counted
    twice (TW-661 accounting rule)."""
    total = 0
    for hold in _fetch(db, "rsn_event_seat_holds", event_id=str(event_id)):
        if hold.get("status") != "held":
            continue
        release_at = _parse_ts(hold.get("release_at"))
        if release_at is not None and release_at > now:
            total += int(hold.get("seats") or 0)
    return total


def _seats_taken(db, event, now: datetime) -> int:
    """Capacity consumption: going RSVPs + active held seats."""
    _release_expired_holds(db, event["id"], now)
    return _going_count(db, event["id"]) + _active_held_seats(db, event["id"], now)


def _rsvp_opening(ctx: MemberContext, event: dict, now: datetime):
    """The member's opening for this event and the window label a
    row written now must carry (TW-661). No rsvp_opens_at means
    TW-660 behavior exactly: open at publish, window 'general'."""
    opens = _parse_ts(event.get("rsvp_opens_at"))
    if opens is None:
        return "general", None
    early = RSVP_WAVE_EARLY_ACCESS.get(ctx.standing_tier, timedelta(0))
    window = ctx.standing_tier if now < opens else "general"
    return window, opens - early


def _can_view_event(db, ctx: MemberContext, event: dict, group: dict) -> bool:
    """Section 5.9 band invisibility (the event's rating and its
    container's, so a low-rated event inside a high-rated group
    inherits the stricter wall, the Section 4 container rule) plus
    Section 8 audience scopes and blocks. Skeleton viewers see no
    member events at all (Section 5.11)."""
    if ctx.is_skeleton:
        return False
    if ctx.band_rank < MIN_BAND_RANK[group["min_age_band"]]:
        return False
    if ctx.band_rank < MIN_BAND_RANK[event["min_age_band"]]:
        return False
    if _blocked_between(db, ctx.user_id, event["host_user_id"]):
        return False
    membership = _membership(db, group["id"], ctx.user_id)
    is_member = membership is not None and membership.get("status") == "active"
    is_host = ctx.user_id == event["host_user_id"]
    if group["visibility"] in ("private", "hidden") and not is_member and not is_host:
        return False
    if event.get("status") == "draft" and not is_host:
        return False
    scope = event.get("audience_scope") or "group"
    if scope == "group":
        return is_member or is_host
    if scope == "close_friends":
        return is_host or _is_close_friend(db, event["host_user_id"], ctx.user_id)
    # connections: group members, the host's close friends, and
    # members couple-linked to the host or to an active member.
    if is_member or is_host:
        return True
    if _is_close_friend(db, event["host_user_id"], ctx.user_id):
        return True
    partners = _couple_partner_ids(db, ctx.user_id)
    return bool(
        partners & (set(_active_member_ids(db, group["id"])) | {event["host_user_id"]})
    )


def _event_out(db, event: dict, group: dict, ctx: MemberContext) -> dict:
    location = _event_location_row(db, event["id"])
    viewer_rsvp = _fetch_one(db, "rsn_rsvps", event_id=event["id"], user_id=ctx.user_id)
    out = {
        "id": event["id"],
        "group_id": event["group_id"],
        "host_user_id": event["host_user_id"],
        "title": event["title"],
        "description": event.get("description"),
        "starts_at": _iso(event.get("starts_at")),
        "ends_at": _iso(event.get("ends_at")),
        "rsvp_opens_at": _iso(event.get("rsvp_opens_at")),
        "capacity": event.get("capacity"),
        "going_count": _going_count(db, event["id"]),
        "guests_enabled": event.get("guests_enabled"),
        "min_tier_view": event["min_tier_view"],
        "min_tier_rsvp": event["min_tier_rsvp"],
        "min_age_band": event["min_age_band"],
        "venue_type": event["venue_type"],
        "audience_scope": event.get("audience_scope"),
        "series_id": event.get("series_id"),
        "status": event["status"],
        "coarse_area": group["metro"],
        "is_host": ctx.user_id == event["host_user_id"],
        "viewer_rsvp": _rsvp_out(viewer_rsvp) if viewer_rsvp else None,
        "created_at": _iso(event.get("created_at")),
    }
    # A public venue's name is a public fact and rides the detail
    # payload; a home's label never does, and the exact address,
    # coordinates, and arrival instructions never ride any payload
    # (they leave only through the gated reveal).
    if event["venue_type"] == "venue" and location is not None:
        out["venue_name"] = location.get("venue_name")
    return out


def create_event(db, user_id: UUID, payload: models.EventCreate, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(payload.group_id))
    if group is None:
        raise NotFoundError()
    if not _group_visible_to_member(db, ctx, group):
        raise NotFoundError()
    membership = _membership(db, group["id"], ctx.user_id)
    if membership is None or membership.get("status") != "active":
        raise ForbiddenError("only active group members host events in the group")
    _require_hosting_band(ctx)
    if MIN_BAND_RANK[payload.min_age_band] > ctx.band_rank:
        raise ForbiddenError("a host cannot rate content above their own verified band")
    capacity_cap = HOST_CAPACITY[ctx.standing_tier]
    capacity = payload.capacity if payload.capacity is not None else capacity_cap
    if capacity > capacity_cap:
        raise ForbiddenError(
            f"capacity {capacity} exceeds the host standing cap of {capacity_cap}"
        )
    if payload.series_id is not None:
        series = _fetch_one(db, "rsn_event_series", id=str(payload.series_id))
        if (
            series is None
            or series["group_id"] != group["id"]
            or series["host_user_id"] != ctx.user_id
        ):
            raise ForbiddenError("series_id must name the host's series in this group")
    now = _resolve_now(now)
    event = _insert(
        db,
        "rsn_events",
        {
            "id": str(uuid4()),
            "group_id": group["id"],
            "host_user_id": ctx.user_id,
            "title": payload.title,
            "description": payload.description,
            "starts_at": payload.starts_at,
            "ends_at": payload.ends_at,
            "capacity": capacity,
            "guests_enabled": payload.guests_enabled,
            "min_tier_view": payload.min_tier_view,
            "min_tier_rsvp": payload.min_tier_rsvp,
            "min_age_band": payload.min_age_band,
            "venue_type": payload.venue_type,
            "audience_scope": payload.audience_scope,
            "rsvp_opens_at": payload.rsvp_opens_at,
            "series_id": str(payload.series_id) if payload.series_id else None,
            "status": "published",
            "created_at": now,
            "updated_at": now,
        },
    )
    return _event_out(db, event, group, ctx)


def get_event(db, viewer_user_id: UUID, event_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    event = _fetch_one(db, "rsn_events", id=str(event_id))
    if event is None:
        raise NotFoundError()
    group = _fetch_one(db, "rsn_groups", id=event["group_id"])
    if group is None or not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    return _event_out(db, event, group, ctx)


def set_event_location(
    db, user_id: UUID, event_id: UUID, payload: models.EventLocationIn, *, now=None
) -> dict:
    """The bronze write path: host-only, stored only in
    rsn_event_locations, echoed back as nothing but a receipt."""
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    event = _fetch_one(db, "rsn_events", id=str(event_id))
    if event is None:
        raise NotFoundError()
    if event["host_user_id"] != ctx.user_id:
        raise ForbiddenError("only the host sets the event location")
    now = _resolve_now(now)
    fields = {
        "venue_name": payload.venue_name,
        "street_address": payload.street_address,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "arrival_instructions": payload.arrival_instructions,
    }
    existing = _event_location_row(db, event["id"])
    if existing is not None:
        _update_by_id(db, "rsn_event_locations", existing["id"], fields)
    else:
        _insert(
            db,
            "rsn_event_locations",
            {"id": str(uuid4()), "event_id": event["id"], "created_at": now, **fields},
        )
    return {"event_id": event["id"], "location_set": True}


# ---------------------------------------------------------------------------
# Series: first-class recurring objects. Instances are ordinary
# rsn_events rows materialized on a rolling 4-week horizon, each with
# its own RSVPs (spec Section 4).
# ---------------------------------------------------------------------------


def _series_out(db, series: dict) -> dict:
    instances = _fetch(db, "rsn_events", series_id=series["id"])
    return {
        "id": series["id"],
        "group_id": series["group_id"],
        "host_user_id": series["host_user_id"],
        "title": series["title"],
        "description_template": series.get("description_template"),
        "recurrence_rule": series["recurrence_rule"],
        "default_capacity": series.get("default_capacity"),
        "default_guests_enabled": series.get("default_guests_enabled"),
        "default_min_tier_view": series["default_min_tier_view"],
        "default_min_tier_rsvp": series["default_min_tier_rsvp"],
        "default_min_age_band": series["default_min_age_band"],
        "default_rsvp_opens_at": _iso(series.get("default_rsvp_opens_at")),
        "venue_ref": series.get("venue_ref"),
        "status": series["status"],
        "instance_count": len(instances),
        "created_at": _iso(series.get("created_at")),
    }


def _bake_dtstart(rule_text: str, first_starts_at: datetime) -> str:
    """rsn_event_series has no first-start column, so the DTSTART
    anchor is baked into the stored RRULE text at creation; the
    stored rule is then self-contained for every later tick."""
    if "dtstart" in rule_text.lower():
        return rule_text
    start = _parse_ts(first_starts_at)
    stamp = start.strftime("%Y%m%dT%H%M%SZ")
    return f"DTSTART:{stamp}\n{rule_text.strip()}"


def create_series(db, user_id: UUID, payload: models.SeriesCreate, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    # Section 7.7: series creation requires Fixture standing or
    # better, and hosting is band-gated like any hosting.
    if STANDING_RANK[ctx.standing_tier] < STANDING_RANK["fixture"]:
        raise ForbiddenError("series creation requires Fixture standing or better")
    _require_hosting_band(ctx)
    group = _fetch_one(db, "rsn_groups", id=str(payload.group_id))
    if group is None:
        raise NotFoundError()
    if not _group_visible_to_member(db, ctx, group):
        raise NotFoundError()
    membership = _membership(db, group["id"], ctx.user_id)
    if membership is None or membership.get("status") != "active":
        raise ForbiddenError("only active group members host a series in the group")
    if MIN_BAND_RANK[payload.default_min_age_band] > ctx.band_rank:
        raise ForbiddenError("a host cannot rate content above their own verified band")
    capacity_cap = HOST_CAPACITY[ctx.standing_tier]
    default_capacity = (
        payload.default_capacity
        if payload.default_capacity is not None
        else capacity_cap
    )
    if default_capacity > capacity_cap:
        raise ForbiddenError(
            f"default capacity {default_capacity} exceeds the host standing cap of {capacity_cap}"
        )
    now = _resolve_now(now)
    series = _insert(
        db,
        "rsn_event_series",
        {
            "id": str(uuid4()),
            "group_id": group["id"],
            "host_user_id": ctx.user_id,
            "title": payload.title,
            "description_template": payload.description_template,
            "recurrence_rule": _bake_dtstart(
                payload.recurrence_rule, payload.first_starts_at
            ),
            "default_capacity": default_capacity,
            "default_guests_enabled": payload.default_guests_enabled,
            "default_min_tier_view": payload.default_min_tier_view,
            "default_min_tier_rsvp": payload.default_min_tier_rsvp,
            "default_min_age_band": payload.default_min_age_band,
            "default_rsvp_opens_at": payload.default_rsvp_opens_at,
            "venue_ref": None,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        },
    )
    materialize_series(
        db,
        series["id"],
        now=now,
        venue_type=payload.venue_type,
        audience_scope=payload.audience_scope,
    )
    return _series_out(db, series)


def get_series(db, viewer_user_id: UUID, series_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    series = _fetch_one(db, "rsn_event_series", id=str(series_id))
    if series is None:
        raise NotFoundError()
    group = _fetch_one(db, "rsn_groups", id=series["group_id"])
    if group is None or ctx.is_skeleton:
        raise NotFoundError()
    if ctx.band_rank < MIN_BAND_RANK[group["min_age_band"]]:
        raise NotFoundError()
    if ctx.band_rank < MIN_BAND_RANK[series["default_min_age_band"]]:
        raise NotFoundError()
    if _blocked_between(db, ctx.user_id, series["host_user_id"]):
        raise NotFoundError()
    if group["visibility"] in ("private", "hidden"):
        membership = _membership(db, group["id"], ctx.user_id)
        if (
            not (membership is not None and membership.get("status") == "active")
            and ctx.user_id != series["host_user_id"]
        ):
            raise NotFoundError()
    return _series_out(db, series)


def set_series_location(
    db, user_id: UUID, series_id: UUID, payload: models.EventLocationIn, *, now=None
) -> dict:
    """The series' usual location (the venue_ref template row, the
    schema's single source of truth for series locations). Existing
    instances that have no location row yet receive their own copy,
    so a template set after materialization still reaches them."""
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    series = _fetch_one(db, "rsn_event_series", id=str(series_id))
    if series is None:
        raise NotFoundError()
    if series["host_user_id"] != ctx.user_id:
        raise ForbiddenError("only the host sets the series location")
    now = _resolve_now(now)
    fields = {
        "venue_name": payload.venue_name,
        "street_address": payload.street_address,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "arrival_instructions": payload.arrival_instructions,
    }
    template = None
    if series.get("venue_ref"):
        template = _fetch_one(db, "rsn_event_locations", id=series["venue_ref"])
    if template is not None:
        _update_by_id(db, "rsn_event_locations", template["id"], fields)
        template = {**template, **fields}
    else:
        template = _insert(
            db,
            "rsn_event_locations",
            {"id": str(uuid4()), "event_id": None, "created_at": now, **fields},
        )
        _update_by_id(
            db, "rsn_event_series", series["id"], {"venue_ref": template["id"]}
        )
    for instance in _fetch(db, "rsn_events", series_id=series["id"]):
        if _event_location_row(db, instance["id"]) is None:
            _insert(
                db,
                "rsn_event_locations",
                {
                    "id": str(uuid4()),
                    "event_id": instance["id"],
                    "venue_name": template.get("venue_name"),
                    "street_address": template["street_address"],
                    "latitude": template["latitude"],
                    "longitude": template["longitude"],
                    "arrival_instructions": template.get("arrival_instructions"),
                    "created_at": now,
                },
            )
    return {"series_id": series["id"], "location_set": True}


def materialize_series(
    db,
    series_id,
    *,
    now=None,
    horizon_days: int = SERIES_HORIZON_DAYS,
    venue_type: str | None = None,
    audience_scope: str | None = None,
) -> dict:
    """Expand the series' RRULE into rsn_events rows inside the
    horizon, skipping occurrences already materialized. Idempotent:
    the scheduler tick (sessions.run_tick, Phase 5) calls this per
    active series; creation calls it with the payload's venue type
    and audience scope, and later calls inherit both from the
    series' most recent instance (the schema stores them nowhere
    else; flagged in the TW-660 evidence doc)."""
    series = _fetch_one(db, "rsn_event_series", id=str(series_id))
    if series is None:
        raise NotFoundError()
    now = _resolve_now(now)
    horizon_end = now + timedelta(days=horizon_days)
    existing = _fetch(db, "rsn_events", series_id=series["id"])
    existing_starts = {_parse_ts(e.get("starts_at")) for e in existing}
    if existing:
        latest = max(existing, key=lambda e: _parse_ts(e.get("starts_at")))
        venue_type = venue_type or latest.get("venue_type") or "venue"
        audience_scope = audience_scope or latest.get("audience_scope") or "group"
    venue_type = venue_type or "venue"
    audience_scope = audience_scope or "group"
    template = None
    if series.get("venue_ref"):
        template = _fetch_one(db, "rsn_event_locations", id=series["venue_ref"])
    rule = rrulestr(series["recurrence_rule"])
    created = 0
    for occurrence in rule.between(now, horizon_end, inc=True):
        if occurrence.tzinfo is None:
            occurrence = occurrence.replace(tzinfo=timezone.utc)
        if occurrence in existing_starts:
            continue
        event = _insert(
            db,
            "rsn_events",
            {
                "id": str(uuid4()),
                "group_id": series["group_id"],
                "host_user_id": series["host_user_id"],
                "title": series["title"],
                "description": series.get("description_template"),
                "starts_at": occurrence,
                "ends_at": occurrence + SERIES_INSTANCE_DURATION,
                "capacity": series.get("default_capacity"),
                "guests_enabled": series.get("default_guests_enabled", True),
                "min_tier_view": series["default_min_tier_view"],
                "min_tier_rsvp": series["default_min_tier_rsvp"],
                "min_age_band": series["default_min_age_band"],
                "venue_type": venue_type,
                "audience_scope": audience_scope,
                "rsvp_opens_at": series.get("default_rsvp_opens_at"),
                "series_id": series["id"],
                "status": "published",
                "created_at": now,
                "updated_at": now,
            },
        )
        existing_starts.add(occurrence)
        created += 1
        if template is not None:
            _insert(
                db,
                "rsn_event_locations",
                {
                    "id": str(uuid4()),
                    "event_id": event["id"],
                    "venue_name": template.get("venue_name"),
                    "street_address": template["street_address"],
                    "latitude": template["latitude"],
                    "longitude": template["longitude"],
                    "arrival_instructions": template.get("arrival_instructions"),
                    "created_at": now,
                },
            )
    return {"series_id": series["id"], "created": created, "horizon_days": horizon_days}


# ---------------------------------------------------------------------------
# RSVPs: per-instance state. Home events and host-approved-tier
# events enter `requested` until the host decides; the host's close
# friends never enter requested (schema contract, TW-650).
# ---------------------------------------------------------------------------


def _rsvp_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "couple_link_id": row.get("couple_link_id"),
        "plus_one_of_rsvp_id": row.get("plus_one_of_rsvp_id"),
        "rsvp_window": row.get("rsvp_window"),
        "host_decided_by": row.get("host_decided_by"),
        "host_decided_at": _iso(row.get("host_decided_at")),
        "created_at": _iso(row.get("created_at")),
    }


def _initial_status(db, event: dict, user_id: str, now: datetime) -> str:
    needs_request = (
        event["venue_type"] == "home" or event["min_tier_rsvp"] == "host_approved"
    ) and not _is_close_friend(db, event["host_user_id"], user_id)
    if needs_request:
        return "requested"
    capacity = event.get("capacity")
    if capacity is not None and _seats_taken(db, event, now) >= capacity:
        return "waitlist"
    return "going"


def _create_rsvp_row(
    db,
    event,
    user_id,
    now,
    *,
    couple_link_id=None,
    plus_one_of=None,
    rsvp_window="general",
):
    row = _insert(
        db,
        "rsn_rsvps",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "user_id": user_id,
            "status": _initial_status(db, event, user_id, now),
            "couple_link_id": couple_link_id,
            "plus_one_of_rsvp_id": plus_one_of,
            # TW-661: rsvp_opens_at now anchors the Section 7.7
            # standing waves, so the window recorded is the one the
            # caller actually entered through (its standing tier in
            # an early wave, 'general' at/after the general opening);
            # waitlist promotion still relabels its own rows.
            "rsvp_window": rsvp_window,
            "host_decided_by": None,
            "host_decided_at": None,
            "created_at": now,
        },
    )
    return row


def _try_couple_rpc(db, params: dict) -> list[dict] | None:
    """The rsn_rsvp_couple RPC (TW-661): both rows in one database
    transaction, or neither. Returns the two written rows, or None
    when the RPC is unavailable (client without rpc, function not
    yet deployed): the caller then runs the TW-660 service path,
    which validates both partners before either row exists, so
    the observable behavior is the same. A failed or malformed RPC
    result logs a warning before the fallback runs (Rosa TW-661
    m1): a broken or undeployed RPC must not stay invisible while
    the fallback quietly carries production."""
    rpc = getattr(db, "rpc", None)
    if rpc is None:
        return None
    try:
        data = rpc("rsn_rsvp_couple", params).execute().data
    except Exception:
        log.warning(
            "rsn_rsvp_couple RPC failed; falling back to service inserts",
            exc_info=True,
        )
        return None
    rows = [r for r in (data or []) if isinstance(r, dict)]
    if len(rows) != 2:
        log.warning(
            "rsn_rsvp_couple returned %d rows, expected 2; "
            "falling back to service inserts",
            len(rows),
        )
        return None
    return rows


def request_rsvp(db, user_id: UUID, payload: models.RsvpCreate, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    event = _fetch_one(db, "rsn_events", id=str(payload.event_id))
    if event is None:
        raise NotFoundError()
    group = _fetch_one(db, "rsn_groups", id=event["group_id"])
    if group is None or not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    if ctx.trust_band == "restricted":
        raise ForbiddenError("RSVP is suspended while the safety band is restricted")
    now = _resolve_now(now)
    if event["status"] != "published":
        raise ConflictError(f"event is {event['status']}")
    starts = _parse_ts(event.get("starts_at"))
    if starts is not None and starts <= now:
        raise ConflictError("event has already started")
    rsvp_window, member_opens = _rsvp_opening(ctx, event, now)
    if member_opens is not None and now < member_opens:
        raise ConflictError("RSVP has not opened for your standing tier yet")
    existing = _fetch_one(db, "rsn_rsvps", event_id=event["id"], user_id=ctx.user_id)
    if existing is not None:
        if existing.get("status") in ("going", "waitlist", "requested"):
            return _rsvp_out(existing)
        raise ConflictError(f"existing RSVP is {existing.get('status')}")
    plus_one_of = None
    if payload.plus_one_of_rsvp_id is not None:
        anchor = _fetch_one(db, "rsn_rsvps", id=str(payload.plus_one_of_rsvp_id))
        if (
            anchor is None
            or anchor["event_id"] != event["id"]
            or anchor["user_id"] == ctx.user_id
        ):
            raise ConflictError(
                "plus_one_of_rsvp_id must name another member's RSVP on this event"
            )
        plus_one_of = anchor["id"]
    if payload.couple_link_id is None:
        row = _create_rsvp_row(
            db,
            event,
            ctx.user_id,
            now,
            plus_one_of=plus_one_of,
            rsvp_window=rsvp_window,
        )
        return _rsvp_out(row)
    # A couple RSVP is two first-class rows (spec Section 4), and it
    # is atomic here: both partners must independently pass every
    # gate before either row exists.
    link = _fetch_one(db, "rsn_couple_links", id=str(payload.couple_link_id))
    if link is None or link.get("status") != "active":
        raise ConflictError("couple link is not active")
    if ctx.user_id not in (link["user_low_id"], link["user_high_id"]):
        raise ForbiddenError("only a partner on the couple link RSVPs as a pair")
    partner_id = (
        link["user_high_id"]
        if link["user_low_id"] == ctx.user_id
        else link["user_low_id"]
    )
    partner_ctx = get_member_context(db, partner_id)
    if not partner_ctx.can_write or partner_ctx.band_rank == 0:
        raise ForbiddenError("couple partner has no voice or band attestation")
    if not _can_view_event(db, partner_ctx, event, group):
        raise NotFoundError()
    if partner_ctx.trust_band == "restricted":
        raise ForbiddenError("couple partner is in the restricted safety band")
    partner_existing = _fetch_one(
        db, "rsn_rsvps", event_id=event["id"], user_id=partner_id
    )
    if partner_existing is not None:
        raise ConflictError("couple partner already holds an RSVP on this event")
    # The partner's own wave gates the pair (Rosa TW-661 M1): the
    # opening is computed from partner_ctx, never borrowed from the
    # requester, and the pair is refused with zero rows written
    # while the partner's window has not opened. Eligibility is
    # atomic with the write: both partners eligible or neither row.
    partner_window, partner_opens = _rsvp_opening(partner_ctx, event, now)
    if partner_opens is not None and now < partner_opens:
        raise ConflictError(
            "RSVP has not opened for your couple partner's standing tier yet"
        )
    # Both partners validated above; the write itself now prefers
    # the rsn_rsvp_couple RPC so the pair lands in one database
    # transaction (TW-661), falling back to the two service inserts
    # when the RPC is unavailable. Each row records the wave that
    # admitted THAT member (Section 7.7), in both paths.
    rpc_rows = _try_couple_rpc(
        db,
        {
            "p_event_id": event["id"],
            "p_user_id": ctx.user_id,
            "p_partner_id": partner_id,
            "p_couple_link_id": link["id"],
            "p_user_status": _initial_status(db, event, ctx.user_id, now),
            "p_partner_status": _initial_status(db, event, partner_id, now),
            "p_rsvp_window": rsvp_window,
            "p_partner_rsvp_window": partner_window,
            "p_user_plus_one_of": plus_one_of,
            "p_now": now,
        },
    )
    if rpc_rows is not None:
        own = next((r for r in rpc_rows if r["user_id"] == ctx.user_id), rpc_rows[0])
        return _rsvp_out(own)
    own = _create_rsvp_row(
        db,
        event,
        ctx.user_id,
        now,
        couple_link_id=link["id"],
        plus_one_of=plus_one_of,
        rsvp_window=rsvp_window,
    )
    _create_rsvp_row(
        db,
        event,
        partner_id,
        now,
        couple_link_id=link["id"],
        rsvp_window=partner_window,
    )
    return _rsvp_out(own)


def decide_rsvp(
    db, host_user_id: UUID, rsvp_id: UUID, payload: models.RsvpDecision, *, now=None
) -> dict:
    ctx = get_member_context(db, host_user_id)
    _require_voice(ctx)
    row = _fetch_one(db, "rsn_rsvps", id=str(rsvp_id))
    if row is None:
        raise NotFoundError()
    event = _fetch_one(db, "rsn_events", id=row["event_id"])
    if event is None:
        raise NotFoundError()
    if event["host_user_id"] != ctx.user_id:
        raise ForbiddenError("only the host decides a requested RSVP")
    if row.get("status") != "requested":
        raise ConflictError("only a requested RSVP awaits a host decision")
    now = _resolve_now(now)
    if payload.decision == "decline":
        patch = {
            "status": "declined",
            "host_decided_by": ctx.user_id,
            "host_decided_at": now,
        }
    else:
        capacity = event.get("capacity")
        status = "going"
        if capacity is not None and _seats_taken(db, event, now) >= capacity:
            status = "waitlist"
        patch = {
            "status": status,
            "host_decided_by": ctx.user_id,
            "host_decided_at": now,
        }
    updated = _update_by_id(db, "rsn_rsvps", row["id"], patch)
    return _rsvp_out(updated or {**row, **patch})


def withdraw_rsvp(db, user_id: UUID, rsvp_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    row = _fetch_one(db, "rsn_rsvps", id=str(rsvp_id))
    if row is None:
        raise NotFoundError()
    if row["user_id"] != ctx.user_id:
        raise ForbiddenError("a member withdraws only their own RSVP")
    if row.get("status") not in ("going", "waitlist", "requested"):
        raise ConflictError(f"RSVP is {row.get('status')}")
    now = _resolve_now(now)
    was_going = row.get("status") == "going"
    # The schema has no "withdrawn" status: a member withdrawal
    # writes "declined" with host_decided_by left null, which is
    # what distinguishes it from a host decline in the raw rows.
    updated = _update_by_id(db, "rsn_rsvps", row["id"], {"status": "declined"})
    if was_going:
        event = _fetch_one(db, "rsn_events", id=row["event_id"])
        if event is not None:
            _promote_waitlist(db, event, now)
    return _rsvp_out(updated or {**row, "status": "declined"})


def _promote_waitlist(db, event: dict, now: datetime) -> None:
    """Fill freed seats from the waitlist in Section 7.5 order:
    safety band first, then RSVP time. Restricted-band members are
    never promoted into a seat they could not claim directly."""
    capacity = event.get("capacity")
    if capacity is None:
        return
    while _seats_taken(db, event, now) < capacity:
        waiters = [
            r
            for r in _fetch(db, "rsn_rsvps", event_id=event["id"])
            if r.get("status") == "waitlist"
        ]
        if not waiters:
            return

        def sort_key(item):
            idx, waiter = item
            scores = _fetch_one(db, "rsn_trust_scores", user_id=waiter["user_id"])
            band = scores.get("band") if scores else "good_standing"
            return (
                TRUST_BAND_PROMOTION_ORDER.get(band or "good_standing", 1),
                _parse_ts(waiter.get("created_at")) or now,
                idx,
            )

        ordered = [w for _, w in sorted(enumerate(waiters), key=sort_key)]
        promoted = False
        for waiter in ordered:
            scores = _fetch_one(db, "rsn_trust_scores", user_id=waiter["user_id"])
            band = scores.get("band") if scores else "good_standing"
            if band == "restricted":
                continue
            _update_by_id(
                db,
                "rsn_rsvps",
                waiter["id"],
                {"status": "going", "rsvp_window": "waitlist_promotion"},
            )
            promoted = True
            break
        if not promoted:
            return


# ---------------------------------------------------------------------------
# Location reveal: the only path an exact location ever leaves this
# layer. Every condition is re-evaluated at request time (Sections
# 5.9 and 8), failures answer one generic refusal, and every success
# writes its evidence row before the payload is returned.
# ---------------------------------------------------------------------------


def reveal_event_location(db, user_id: UUID, event_id: UUID, *, now=None) -> dict:
    ctx = get_member_context(db, user_id)
    _require_not_banned(ctx)
    event = _fetch_one(db, "rsn_events", id=str(event_id))
    if event is None:
        raise NotFoundError()
    group = _fetch_one(db, "rsn_groups", id=event["group_id"])
    if group is None or not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    now = _resolve_now(now)
    starts = _parse_ts(event.get("starts_at"))
    ends = _parse_ts(event.get("ends_at"))
    window = (
        HOME_REVEAL_WINDOW if event["venue_type"] == "home" else VENUE_REVEAL_WINDOW
    )
    if starts is None or now < starts - window or (ends is not None and now > ends):
        raise ForbiddenError("location is not available")
    if ctx.user_id != event["host_user_id"]:
        if not ctx.can_write or ctx.band_rank == 0:
            raise ForbiddenError("location is not available")
        membership = _membership(db, group["id"], ctx.user_id)
        if membership is None or membership.get("status") != "active":
            raise ForbiddenError("location is not available")
        if ctx.trust_band == "restricted":
            raise ForbiddenError("location is not available")
        rsvp = _fetch_one(db, "rsn_rsvps", event_id=event["id"], user_id=ctx.user_id)
        if rsvp is None or rsvp.get("status") != "going":
            raise ForbiddenError("location is not available")
        host_decided = rsvp.get("host_decided_by") is not None
        close_friend = _is_close_friend(db, event["host_user_id"], ctx.user_id)
        if event["min_tier_view"] == "host_approved" and not (
            host_decided or close_friend
        ):
            raise ForbiddenError("location is not available")
        if event["venue_type"] == "home" and not (host_decided or close_friend):
            # A home address reaches only the host's close friends
            # and host-approved confirmed guests (TW-660 brief).
            raise ForbiddenError("location is not available")
    location = _event_location_row(db, event["id"])
    if location is None:
        raise ForbiddenError("location is not available")
    _insert(
        db,
        "rsn_location_reveals",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "user_id": ctx.user_id,
            "revealed_at": now,
        },
    )
    profile = _fetch_one(db, "rsn_profiles", user_id=ctx.user_id)
    return {
        "event_id": event["id"],
        "venue_name": location.get("venue_name"),
        "street_address": location["street_address"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "arrival_instructions": location.get("arrival_instructions"),
        # Section 8: the revealed view carries the viewer's display
        # name as a visible watermark; screenshots cannot be
        # prevented, so attribution does the deterring.
        "watermark_display_name": profile.get("display_name") if profile else None,
    }


# ---------------------------------------------------------------------------
# Gold listings on the member surface: read-only facts from the
# TW-651 index. Claiming is out of scope here; a listing that exists
# in gold is unclaimed by construction (the publish step owns gold).
# The payload is the public fact set: no street address and no
# coordinates leave through the member API (Section 10 owns the
# public map surface; the member's need is discovery, not the door).
# ---------------------------------------------------------------------------


def _listing_card(row: dict) -> dict:
    return {
        "listing_id": row["listing_id"],
        "title": row["title"],
        "starts_at": _iso(row.get("starts_at")),
        "ends_at": _iso(row.get("ends_at")),
        "venue_name": row.get("venue_name"),
        "city": row.get("city"),
        "neighborhood": row.get("neighborhood"),
        "category": row.get("category"),
        "min_age_band": row.get("min_age_band"),
        "band_label": BAND_LABEL.get(row.get("min_age_band") or ""),
        "attribution_text": row.get("attribution_text"),
        "primary_source_url": row.get("primary_source_url"),
    }


def _listing_visible(ctx: MemberContext, row: dict) -> bool:
    band = row.get("min_age_band")
    if ctx.band_rank == 0:
        # Skeleton and bandless viewers: lowest band only, unrated
        # excluded (Sections 5.9 and 5.11 fail-closed).
        return band == "age_16_plus"
    if band is None:
        # Unrated content is invisible below the top band.
        return ctx.band_rank >= BAND_RANK["age_21_plus"]
    return ctx.band_rank >= MIN_BAND_RANK[band]


def list_listings(db, viewer_user_id: UUID, *, now=None) -> list[dict]:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    rows = _fetch(db, "evt_gold_listings")
    visible = [r for r in rows if _listing_visible(ctx, r)]
    visible.sort(key=lambda r: _parse_ts(r.get("starts_at")) or _resolve_now(now))
    return [_listing_card(r) for r in visible]


def get_listing(db, viewer_user_id: UUID, listing_id, *, now=None) -> dict:
    ctx = get_member_context(db, viewer_user_id)
    _require_not_banned(ctx)
    row = _fetch_one(db, "evt_gold_listings", listing_id=str(listing_id))
    if row is None or not _listing_visible(ctx, row):
        raise NotFoundError()
    return _listing_card(row)


# ---------------------------------------------------------------------------
# Check-ins (Phase 5) and reports (Phase 6): still honest stubs.
# ---------------------------------------------------------------------------


def create_checkin(db, user_id: UUID, payload: models.CheckinCreate) -> dict:
    raise _todo(_PHASE_5)


def create_report(db, reporter_id: UUID, payload: models.ReportCreate) -> dict:
    raise _todo(_PHASE_6)

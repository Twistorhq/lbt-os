"""TW-650/TW-660 Twistor Social: service layer.

Phase 4 core loop (TW-660) is implemented here: groups, events,
recurring series, RSVPs, and the location reveal, over the merged
rsn_ schema (TW-650), plus the member-facing read of the TW-651 gold
events index. TW-673 adds the Phase 5 safety check-in loop below
(geo arrival, encounter confirms, welfare checks, write-ups, and
the standing recompute). Profiles remain a later Phase 4 slice;
reports are Phase 6 and that stub still raises honestly.

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

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from dateutil.rrule import rrulestr

from . import models, safety

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
    # TW-940 Safety Desk enforcement state, resolved from active
    # rsn_sanctions rows for this user (see the Safety Desk section).
    suspended: bool = False
    suspended_until: datetime | None = None
    restricted: bool = False

    @property
    def band_rank(self) -> int:
        return BAND_RANK.get(self.band or "", 0)

    @property
    def can_write(self) -> bool:
        """Voice (Sections 5.10-5.11): a passed T2 verdict. The band
        gate is applied separately by _require_voice, because a
        verified member whose attestation is gone has a verdict
        record but nothing a gate may lawfully read."""
        return (
            self.verified and self.tier >= 2 and not self.banned and not self.suspended
        )

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


def get_member_context(
    db, user_id: UUID | str, *, now: datetime | None = None
) -> MemberContext:
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
    # TW-940: active Safety Desk sanctions bind at this layer, the
    # same place bans already bind. A suspension removes voice until
    # its expiry (an expired suspension no longer binds); a
    # restriction removes hosting / RSVP via the gates below.
    suspended = False
    suspended_until = None
    restricted = False
    now = _resolve_now(now)
    for sanction in _fetch(db, "rsn_sanctions", user_id=uid):
        if sanction.get("status") != "active":
            continue
        expires = _parse_ts(sanction.get("expires_at"))
        if expires is not None and expires <= now:
            continue
        if sanction.get("level") == "suspension":
            suspended = True
            suspended_until = expires
        elif sanction.get("level") == "restriction":
            restricted = True
    return MemberContext(
        user_id=uid,
        verified=verified,
        tier=tier,
        band=band,
        standing_tier=standing or "verified",
        trust_band=trust_band or "good_standing",
        banned=banned,
        anchor_hash=anchor,
        suspended=suspended,
        suspended_until=suspended_until,
        restricted=restricted,
    )


def _require_not_banned(ctx: MemberContext) -> None:
    if ctx.banned:
        # Section 5.11: a banned identity gets less than the ghost
        # town: refusal, on reads as well as writes.
        raise ForbiddenError("account refused")


def _require_voice(ctx: MemberContext) -> None:
    _require_not_banned(ctx)
    if ctx.suspended:
        raise ForbiddenError("account suspended by the safety desk")
    if not ctx.can_write:
        raise ForbiddenError(
            "verification required: speaking requires a passed T2 verification"
        )
    if ctx.band_rank == 0:
        raise ForbiddenError("no live age-band attestation")


def _require_hosting_band(ctx: MemberContext) -> None:
    # TW-940: an active safety-desk restriction removes hosting
    # outright, whatever the trust band says.
    if ctx.restricted:
        raise ForbiddenError(
            "hosting is suspended while a safety restriction is active"
        )
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
    ctx = get_member_context(db, user_id, now=now)
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
    if ctx.trust_band == "restricted" or ctx.restricted:
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
    if partner_ctx.trust_band == "restricted" or partner_ctx.restricted:
        # Both restriction sources, matching the single path above:
        # the trust band AND the Safety Desk restriction flag
        # (TW-940 fix round 1, Rosa nit).
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
# The safety check-in loop (TW-673, Phase 5; spec Section 7).
#
# Stage 1 geo arrival: the geofence verdict is computed ON-DEVICE.
# This layer receives a verdict, an accuracy bucket, and a
# timestamp (models.CheckinCreate has no coordinate field, and
# rsn_checkins has no coordinate column): there is nothing here
# that could store a location diary even if a caller wanted one.
# Stage 2 encounter confirms keep identity honest socially: a "no"
# on identity is a strike on the subject's ledger and opens a
# verification review (the strike event plus the confirm row are
# the review record the Phase 6 safety console consumes; RSN never
# writes into the verify service's session-bound bronze signals).
# Stage 3 welfare records are enums and timestamps only. Stage 4
# write-ups feed reputation through the trust ledger, and standing
# is recomputed from the records: it accrues only from showing up.
# ---------------------------------------------------------------------------

ARRIVAL_WINDOW_EARLY = timedelta(minutes=30)
ARRIVAL_CLASS_LATE_AFTER = timedelta(minutes=30)
# Proof quality (Section 7.5): a host-confirmed arrival outweighs a
# geo_soft verdict in the trust model. qr_strong carries NO weight
# here on purpose: it is refused in create_checkin until the venue
# token registry lands (Rosa TW-673 M3), so no code path can record
# an unvalidated QR assertion at any weight, let alone strong.
CHECKIN_WEIGHTS = {"geo_soft": 2, "host_confirm": 3}
IDENTITY_CONFIRM_WEIGHT = 1
IDENTITY_STRIKE_WEIGHT = -10
NO_SHOW_WEIGHT = -3
REVIEW_WEIGHTS = {True: 1, False: -1, None: 0}
HELP_OPTIONS = (
    "notify_trusted_contact",
    "share_location_with_trusted_contact",
    "exit_help",
    "emergency_services",
)


def _append_trust_event(
    db, user_id: str, event_type: str, weight, *, ref_type, ref_id, now
) -> dict:
    return _insert(
        db,
        "rsn_trust_events",
        {
            "id": str(uuid4()),
            "user_id": user_id,
            "event_type": event_type,
            "weight": weight,
            "ref_type": ref_type,
            "ref_id": str(ref_id) if ref_id else None,
            "created_at": now,
        },
    )


def _trust_event_exists(db, user_id: str, event_type: str, ref_id) -> bool:
    for row in _fetch(db, "rsn_trust_events", user_id=user_id):
        if row.get("event_type") == event_type and row.get("ref_id") == str(ref_id):
            return True
    return False


def _event_group_or_404(db, event_id) -> tuple[dict, dict]:
    event = _fetch_one(db, "rsn_events", id=str(event_id))
    if event is None:
        raise NotFoundError()
    group = _fetch_one(db, "rsn_groups", id=event["group_id"])
    if group is None:
        raise NotFoundError()
    return event, group


def _going_rsvp(db, event_id, user_id: str) -> dict | None:
    row = _fetch_one(db, "rsn_rsvps", event_id=str(event_id), user_id=user_id)
    if row is not None and row.get("status") == "going":
        return row
    return None


def _checkin_row(db, event_id, user_id: str) -> dict | None:
    return _fetch_one(db, "rsn_checkins", event_id=str(event_id), user_id=user_id)


def _arrival_class(event: dict, now: datetime) -> str:
    starts = _parse_ts(event.get("starts_at"))
    if starts is None or now < starts:
        return "early"
    if now > starts + ARRIVAL_CLASS_LATE_AFTER:
        return "late"
    return "on_time"


def _checkin_out(row: dict, event: dict, now: datetime) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "user_id": row["user_id"],
        "proof_type": row["proof_type"],
        "arrived_at": _iso(row.get("arrived_at")),
        "arrival_class": _arrival_class(event, _parse_ts(row.get("arrived_at")) or now),
    }


def _require_arrival_window(event: dict, now: datetime) -> None:
    starts = _parse_ts(event.get("starts_at"))
    ends = _parse_ts(event.get("ends_at"))
    if starts is None or now < starts - ARRIVAL_WINDOW_EARLY:
        raise ConflictError("the arrival window has not opened for this event")
    if ends is not None and now > ends:
        raise ConflictError("the event has ended; check-in is closed")


def _record_checkin(db, event: dict, user_id: str, proof_type: str, now) -> dict:
    existing = _checkin_row(db, event["id"], user_id)
    if existing is not None:
        return _checkin_out(existing, event, now)
    row = _insert(
        db,
        "rsn_checkins",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "user_id": user_id,
            "proof_type": proof_type,
            "arrived_at": now,
            "departed_at": None,
        },
    )
    _append_trust_event(
        db,
        user_id,
        "checkin_completed",
        CHECKIN_WEIGHTS[proof_type],
        ref_type="event",
        ref_id=event["id"],
        now=now,
    )
    return _checkin_out(row, event, now)


def create_checkin(
    db, user_id: UUID, payload: models.CheckinCreate, *, now=None
) -> dict:
    """Stage 1: a member's own arrival, by geo verdict.

    host_confirm is refused here by design: the model contract says
    a host confirmation carries nothing the member asserts, so it
    can only be recorded by the host (host_confirm_checkin below).
    qr_strong is refused until the venue token registry lands
    (Rosa TW-673 M3): Section 7.1's strong proof is the host's
    issued venue QR code, and a non-empty string from the member's
    own client proves nothing, so an unvalidated QR assertion is
    never recorded as a check-in at any weight. The token value is
    never stored either way (rsn_checkins has no token column)."""
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    if payload.proof_type == "host_confirm":
        raise ForbiddenError(
            "host confirmation is recorded by the host, never self-asserted"
        )
    event, group = _event_group_or_404(db, payload.event_id)
    if not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    if event["status"] != "published":
        raise ConflictError(f"event is {event['status']}")
    now = _resolve_now(now)
    _require_arrival_window(event, now)
    if ctx.user_id != event["host_user_id"]:
        if ctx.trust_band == "restricted":
            raise ForbiddenError(
                "restricted-band members arrive by host confirmation at the door"
            )
        if _going_rsvp(db, event["id"], ctx.user_id) is None:
            raise ForbiddenError("check-in requires a going RSVP")
    if payload.proof_type == "qr_strong":
        raise ForbiddenError(
            "venue QR check-in is not yet available: QR tokens are "
            "unvalidated until the venue token registry lands; check "
            "in by geo verdict or host confirmation at the door"
        )
    if payload.proof_type == "geo_soft":
        if payload.within_geofence is not True:
            raise ForbiddenError("geofence verdict is outside the venue geofence")
        if payload.observed_at is not None and payload.observed_at > (
            now + timedelta(minutes=5)
        ):
            raise ConflictError("observed_at is in the future")
        starts = _parse_ts(event.get("starts_at"))
        if (
            payload.observed_at is not None
            and starts is not None
            and payload.observed_at < starts - ARRIVAL_WINDOW_EARLY
        ):
            raise ConflictError(
                "observed_at predates the arrival window: a verdict "
                "observed before the window opened is not an arrival verdict"
            )
    return _record_checkin(db, event, ctx.user_id, payload.proof_type, now)


def host_confirm_checkin(
    db, host_user_id: UUID, event_id: UUID, subject_id: UUID, *, now=None
) -> dict:
    """The door path: the event's host stamps an attendee's arrival.
    This is the strong corroboration in the trust model and the
    only arrival path open to a restricted-band member."""
    ctx = get_member_context(db, host_user_id)
    _require_voice(ctx)
    event, _group = _event_group_or_404(db, event_id)
    if event["host_user_id"] != ctx.user_id:
        raise ForbiddenError("only the event host confirms arrivals at the door")
    if event["status"] != "published":
        raise ConflictError(f"event is {event['status']}")
    now = _resolve_now(now)
    _require_arrival_window(event, now)
    subject = get_member_context(db, subject_id)
    if subject.banned:
        raise ForbiddenError("account refused")
    if str(subject_id) != event["host_user_id"] and (
        _going_rsvp(db, event["id"], str(subject_id)) is None
    ):
        raise ForbiddenError("host confirmation requires a going RSVP")
    return _record_checkin(db, event, str(subject_id), "host_confirm", now)


# ---------------------------------------------------------------------------
# Stage 2: encounter confirms (Section 7.2)
# ---------------------------------------------------------------------------


def _confirm_out(row: dict, *, review_opened: bool) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "confirmer_id": row["confirmer_id"],
        "subject_id": row["subject_id"],
        "met_them": row["met_them"],
        "identity_matched": row.get("identity_matched"),
        "identity_review_opened": review_opened,
        "created_at": _iso(row.get("created_at")),
    }


def record_encounter_confirm(
    db, user_id: UUID, payload: models.EncounterConfirmCreate, *, now=None
) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    event, group = _event_group_or_404(db, payload.event_id)
    if not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    subject_id = str(payload.subject_id)
    if subject_id == ctx.user_id:
        raise ForbiddenError("a member cannot confirm an encounter with themselves")
    if _blocked_between(db, ctx.user_id, subject_id):
        raise NotFoundError()
    if _checkin_row(db, event["id"], ctx.user_id) is None:
        raise ForbiddenError(
            "encounter confirms are written from the room: check in first"
        )
    subject_checkin = _checkin_row(db, event["id"], subject_id)
    subject_rsvp = _fetch_one(db, "rsn_rsvps", event_id=event["id"], user_id=subject_id)
    if (
        subject_checkin is None
        and subject_rsvp is None
        and subject_id != event["host_user_id"]
    ):
        raise NotFoundError()
    existing = None
    for row in _fetch(db, "rsn_encounter_confirms", event_id=event["id"]):
        if row["confirmer_id"] == ctx.user_id and row["subject_id"] == subject_id:
            existing = row
            break
    if existing is not None:
        if (
            existing.get("met_them") == payload.met_them
            and existing.get("identity_matched") == payload.identity_matched
        ):
            return _confirm_out(
                existing, review_opened=existing.get("identity_matched") is False
            )
        raise ConflictError("this encounter was already confirmed differently")
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_encounter_confirms",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "confirmer_id": ctx.user_id,
            "subject_id": subject_id,
            "met_them": payload.met_them,
            "identity_matched": payload.identity_matched,
            "created_at": now,
        },
    )
    review_opened = False
    if payload.met_them is False and subject_checkin is None:
        # A "did not meet them" against a member with no arrival of
        # their own is the possible no-show / phantom signal.
        if not _trust_event_exists(db, subject_id, "no_show", event["id"]):
            _append_trust_event(
                db,
                subject_id,
                "no_show",
                NO_SHOW_WEIGHT,
                ref_type="event",
                ref_id=event["id"],
                now=now,
            )
    if payload.identity_matched is False:
        # The identity strike: a trust event against the subject and
        # an automatically opened verification review. One strike
        # starts the review; it does not convict.
        _append_trust_event(
            db,
            subject_id,
            "identity_strike",
            IDENTITY_STRIKE_WEIGHT,
            ref_type="encounter_confirm",
            ref_id=row["id"],
            now=now,
        )
        review_opened = True
        recompute_member_standing(db, subject_id, now=now)
    elif payload.met_them is True and payload.identity_matched is True:
        _append_trust_event(
            db,
            subject_id,
            "identity_confirm_positive",
            IDENTITY_CONFIRM_WEIGHT,
            ref_type="encounter_confirm",
            ref_id=row["id"],
            now=now,
        )
    return _confirm_out(row, review_opened=review_opened)


# ---------------------------------------------------------------------------
# Stage 3: welfare checks (Section 7.3). Records are enums and
# timestamps only. Escalation is a record shape for the Phase 6
# safety console: it surfaces help options fast and freezes the
# session's evidence on a silent alert; it never dispatches rescue
# and never claims to.
# ---------------------------------------------------------------------------


def _welfare_out(row: dict, escalation: dict | None) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "user_id": row["user_id"],
        "prompted_at": _iso(row.get("prompted_at")),
        "response": row.get("response"),
        "help_action": row.get("help_action") or "none",
        "responded_at": _iso(row.get("responded_at")),
        "escalation": escalation,
    }


def _escalation(reason: str, now: datetime) -> dict:
    return {
        "reason": reason,
        "help_options": list(HELP_OPTIONS),
        "notify_trusted_contact": True,
        "evidence_packet_freeze_requested": reason == "silent_alert",
        "dispatches_rescue": False,
        "recorded_at": _iso(now),
    }


def _escalation_for(db, event_id, user_id: str, now: datetime) -> dict | None:
    rows = _fetch(db, "rsn_welfare_checks", event_id=str(event_id), user_id=user_id)
    if any(r.get("help_action") == "silent_alert" for r in rows):
        return _escalation("silent_alert", now)
    if any(r.get("response") == "need_help" for r in rows):
        return _escalation("need_help", now)
    unanswered = [r for r in rows if r.get("response") is None]
    if len(unanswered) >= 2:
        return _escalation("no_response", now)
    return None


def welfare_escalation(db, event_id: UUID, user_id: UUID, *, now=None) -> dict | None:
    """The escalation state of one member's welfare record on one
    event, readable by the safety console slice (Phase 6)."""
    return _escalation_for(db, event_id, str(user_id), _resolve_now(now))


def prompt_welfare_check(db, event_id: UUID, user_id: UUID, *, now=None) -> dict:
    """Record one welfare prompt (scheduler tick / safety button).
    Prompts are production records, not sends: Notify delivery is
    the scheduler's concern (Section 7.6)."""
    event, _group = _event_group_or_404(db, event_id)
    if _checkin_row(db, event["id"], str(user_id)) is None:
        raise ForbiddenError("welfare prompts go to checked-in attendees")
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_welfare_checks",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "user_id": str(user_id),
            "prompted_at": now,
            "response": None,
            "help_action": "none",
            "responded_at": None,
        },
    )
    return _welfare_out(row, None)


def respond_welfare_check(
    db, user_id: UUID, payload: models.WelfareResponseIn, *, now=None
) -> dict:
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    event, group = _event_group_or_404(db, payload.event_id)
    if not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    if _checkin_row(db, event["id"], ctx.user_id) is None:
        raise ForbiddenError("welfare checks answer from the room: check in first")
    now = _resolve_now(now)
    pending = [
        r
        for r in _fetch(
            db, "rsn_welfare_checks", event_id=event["id"], user_id=ctx.user_id
        )
        if r.get("response") is None
    ]
    patch = {
        "response": payload.response,
        "help_action": payload.help_action,
        "responded_at": now,
    }
    if pending:
        pending.sort(key=lambda r: _parse_ts(r.get("prompted_at")) or now)
        row = _update_by_id(db, "rsn_welfare_checks", pending[0]["id"], patch)
        row = row or {**pending[0], **patch}
    else:
        row = _insert(
            db,
            "rsn_welfare_checks",
            {
                "id": str(uuid4()),
                "event_id": event["id"],
                "user_id": ctx.user_id,
                "prompted_at": now,
                **patch,
            },
        )
    return _welfare_out(row, _escalation_for(db, event["id"], ctx.user_id, now))


# ---------------------------------------------------------------------------
# Session completion + Stage 4 write-ups (Sections 7.4, 7.5)
# ---------------------------------------------------------------------------


def complete_event(db, host_user_id: UUID, event_id: UUID, *, now=None) -> dict:
    """Close a session: the event completes, going RSVPs with no
    arrival become no-shows on the record and the ledger, and every
    attendee's standing is recomputed now that nights can count."""
    ctx = get_member_context(db, host_user_id)
    _require_voice(ctx)
    event, _group = _event_group_or_404(db, event_id)
    if event["host_user_id"] != ctx.user_id:
        raise ForbiddenError("only the host completes their event")
    if event["status"] != "published":
        raise ConflictError(f"event is {event['status']}")
    now = _resolve_now(now)
    updated = _update_by_id(db, "rsn_events", event["id"], {"status": "completed"})
    event = updated or {**event, "status": "completed"}
    no_show_ids: list[str] = []
    for rsvp_row in _fetch(db, "rsn_rsvps", event_id=event["id"]):
        if rsvp_row.get("status") != "going":
            continue
        member_id = rsvp_row["user_id"]
        if _checkin_row(db, event["id"], member_id) is not None:
            continue
        _update_by_id(db, "rsn_rsvps", rsvp_row["id"], {"status": "no_show"})
        if not _trust_event_exists(db, member_id, "no_show", event["id"]):
            _append_trust_event(
                db,
                member_id,
                "no_show",
                NO_SHOW_WEIGHT,
                ref_type="event",
                ref_id=event["id"],
                now=now,
            )
        no_show_ids.append(member_id)
    touched = {r["user_id"] for r in _fetch(db, "rsn_checkins", event_id=event["id"])}
    for member_id in touched | set(no_show_ids):
        recompute_member_standing(db, member_id, now=now)
    recompute_member_standing(db, ctx.user_id, now=now)
    return {
        "id": event["id"],
        "status": "completed",
        "no_show_user_ids": sorted(no_show_ids),
    }


def _review_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "reviewer_id": row["reviewer_id"],
        "subject_type": row["subject_type"],
        "subject_id": row.get("subject_id"),
        "guest_pass_id": row.get("guest_pass_id"),
        "interaction_rating": row.get("interaction_rating"),
        "good_person": row.get("good_person"),
        "would_meet_again": row.get("would_meet_again"),
        "would_join_again": row.get("would_join_again"),
        "free_text": row.get("free_text"),
        "created_at": _iso(row.get("created_at")),
    }


def create_review(db, user_id: UUID, payload: models.ReviewCreate, *, now=None) -> dict:
    """Stage 4: the structured write-up. One live review per
    (event, reviewer, subject) triple, the latest submission
    counting (Section 7.5). Person-review free text is staff-side
    pattern data; no member surface in this slice serves it back
    to the subject (my_review_summary is aggregates only)."""
    ctx = get_member_context(db, user_id)
    _require_voice(ctx)
    event, group = _event_group_or_404(db, payload.event_id)
    if not _can_view_event(db, ctx, event, group):
        raise NotFoundError()
    if event["status"] != "completed":
        raise ConflictError("write-ups open when the event has completed")
    if _checkin_row(db, event["id"], ctx.user_id) is None:
        raise ForbiddenError("write-ups are written by attendees: check in first")
    subject_id = str(payload.subject_id) if payload.subject_id else None
    if payload.subject_type == "person":
        if subject_id is None:
            raise ConflictError("a person review names its subject")
        if subject_id == ctx.user_id:
            raise ForbiddenError("a member cannot review themselves")
        # Section 7.5's rate is defined over reviews where reviewer
        # and subject both completed the session, so the subject
        # must have arrived (check-in) or hosted; an RSVP alone is
        # not attendance (Rosa TW-673 m2).
        attended = (
            subject_id == event["host_user_id"]
            or _checkin_row(db, event["id"], subject_id) is not None
        )
        if not attended:
            raise NotFoundError()
        if (
            payload.would_meet_again is None
            and payload.good_person is None
            and payload.interaction_rating is None
        ):
            raise ConflictError(
                "a person review carries at least one structured answer"
            )
    elif payload.subject_type == "event":
        if subject_id is not None:
            raise ConflictError("an event review carries no person subject")
        if payload.would_join_again is None:
            raise ConflictError("an event review answers would_join_again")
    else:  # guest_pass
        if payload.guest_pass_id is None:
            raise ConflictError("a guest review names its guest pass")
        guest_pass = _fetch_one(db, "rsn_guest_passes", id=str(payload.guest_pass_id))
        if guest_pass is None or guest_pass["event_id"] != event["id"]:
            raise NotFoundError()
    now = _resolve_now(now)
    fields = {
        "interaction_rating": payload.interaction_rating,
        "good_person": payload.good_person,
        "would_meet_again": payload.would_meet_again,
        "would_join_again": payload.would_join_again,
        "free_text": payload.free_text,
    }
    existing = None
    for row in _fetch(db, "rsn_reviews", event_id=event["id"]):
        if (
            row["reviewer_id"] != ctx.user_id
            or row["subject_type"] != payload.subject_type
        ):
            continue
        if payload.subject_type == "person" and row.get("subject_id") == subject_id:
            existing = row
            break
        if payload.subject_type == "event" and row.get("subject_id") is None:
            existing = row
            break
        if payload.subject_type == "guest_pass" and row.get("guest_pass_id") == str(
            payload.guest_pass_id
        ):
            existing = row
            break
    if existing is not None:
        updated = _update_by_id(db, "rsn_reviews", existing["id"], fields)
        if payload.subject_type == "person" and subject_id is not None:
            # The latest submission counts (Section 7.5) for the
            # ledger too, not just the row and the rates: the insert
            # wrote REVIEW_WEIGHTS[old answer], so an edit writes the
            # compensating review_received delta referencing the same
            # review row and recomputes the subject. Without this the
            # stored score keeps the first submission forever while
            # the summary reads the latest (Rosa TW-673 M1).
            delta = (
                REVIEW_WEIGHTS[payload.would_meet_again]
                - REVIEW_WEIGHTS[existing.get("would_meet_again")]
            )
            if delta != 0:
                _append_trust_event(
                    db,
                    subject_id,
                    "review_received",
                    delta,
                    ref_type="review",
                    ref_id=existing["id"],
                    now=now,
                )
            recompute_member_standing(db, subject_id, now=now)
        return _review_out(updated or {**existing, **fields})
    row = _insert(
        db,
        "rsn_reviews",
        {
            "id": str(uuid4()),
            "event_id": event["id"],
            "reviewer_id": ctx.user_id,
            "subject_id": subject_id,
            "subject_type": payload.subject_type,
            "guest_pass_id": str(payload.guest_pass_id)
            if payload.guest_pass_id
            else None,
            "created_at": now,
            **fields,
        },
    )
    if payload.subject_type == "person" and subject_id is not None:
        _append_trust_event(
            db,
            subject_id,
            "review_received",
            REVIEW_WEIGHTS[payload.would_meet_again],
            ref_type="review",
            ref_id=row["id"],
            now=now,
        )
        recompute_member_standing(db, subject_id, now=now)
    return _review_out(row)


def my_review_summary(db, user_id: UUID, *, now=None) -> dict:
    """A member's own reputation aggregate (Section 7.4): rates and
    counts over person reviews received. No reviewer identity, no
    free text: a reputation you can see and improve, not a dossier
    on who said what."""
    ctx = get_member_context(db, user_id)
    _require_not_banned(ctx)
    rows = [
        r
        for r in _fetch(db, "rsn_reviews", subject_id=ctx.user_id)
        if r.get("subject_type") == "person"
    ]
    wma = [r["would_meet_again"] for r in rows if r.get("would_meet_again") is not None]
    good = [r["good_person"] for r in rows if r.get("good_person") is not None]
    ratings = [
        r["interaction_rating"] for r in rows if r.get("interaction_rating") is not None
    ]
    return {
        "user_id": ctx.user_id,
        "reviews_received": len(rows),
        "would_meet_again_rate": (sum(1 for v in wma if v) / len(wma)) if wma else None,
        "good_person_rate": (sum(1 for v in good if v) / len(good)) if good else None,
        "average_interaction_rating": (sum(ratings) / len(ratings))
        if ratings
        else None,
    }


# ---------------------------------------------------------------------------
# Standing recompute (Section 7.5): the projection that turns loop
# records into tier, band, and score. Pure rules live in trust.py;
# this is the I/O that feeds them. Standing is earned only by
# showing up: nothing in this projection reads a payment, a post,
# or a hand-set tier, and a stored row claiming more than the
# records support is overwritten by the records.
# ---------------------------------------------------------------------------


def recompute_member_standing(db, user_id: UUID | str, *, now=None) -> dict:
    from . import trust

    uid = str(user_id)
    now = _resolve_now(now)
    events = {r["id"]: r for r in _fetch(db, "rsn_events")}
    my_rsvps = _fetch(db, "rsn_rsvps", user_id=uid)
    my_checkins = _fetch(db, "rsn_checkins", user_id=uid)
    all_checkins = _fetch(db, "rsn_checkins")
    confirms = _fetch(db, "rsn_encounter_confirms", subject_id=uid)
    ledger = _fetch(db, "rsn_trust_events", user_id=uid)

    checked_in_events = {c["event_id"] for c in my_checkins}
    confirmed_events = {c["event_id"] for c in confirms if c.get("met_them") is True}

    def _night_completed(event_id: str) -> bool:
        event = events.get(event_id)
        if event is None or event.get("status") != "completed":
            return False
        if event_id not in checked_in_events:
            return False
        mine = [c for c in my_checkins if c["event_id"] == event_id]
        if any(c.get("proof_type") == "host_confirm" for c in mine):
            return True
        if event_id in confirmed_events:
            return True
        # The host's own night is corroborated by any other verified
        # attendee's arrival in their room.
        if event["host_user_id"] == uid:
            return any(
                c["event_id"] == event_id and c["user_id"] != uid for c in all_checkins
            )
        return False

    night_event_ids = {eid for eid in checked_in_events if _night_completed(eid)}
    completed_nights = len(night_event_ids)

    no_shows = 0
    for rsvp_row in my_rsvps:
        event = events.get(rsvp_row["event_id"])
        if rsvp_row.get("status") == "no_show":
            no_shows += 1
        elif (
            rsvp_row.get("status") == "going"
            and event is not None
            and event.get("status") == "completed"
            and rsvp_row["event_id"] not in checked_in_events
        ):
            no_shows += 1
    # Hosted events are the host's own obligation (Section 7.5).
    for event in events.values():
        if (
            event["host_user_id"] == uid
            and event.get("status") == "completed"
            and not any(
                c["event_id"] == event["id"] and c["user_id"] != uid
                for c in all_checkins
            )
            and event["id"] not in checked_in_events
        ):
            no_shows += 1

    reviews = [
        r
        for r in _fetch(db, "rsn_reviews", subject_id=uid)
        if r.get("subject_type") == "person"
    ]
    hosted_clean = 0
    for event in events.values():
        if event["host_user_id"] != uid or event.get("status") != "completed":
            continue
        if not any(
            c["event_id"] == event["id"] and c["user_id"] != uid for c in all_checkins
        ):
            continue
        if any(
            e.get("event_type") == "report_substantiated"
            and e.get("ref_id") == event["id"]
            for e in ledger
        ):
            continue
        hosted_clean += 1

    # Series streaks: walk each series' held instances in order from
    # the member's first attended one; a held instance they did not
    # complete a night at resets the current run (Section 7.5).
    best_streak = 0
    series_ids = {
        events[eid].get("series_id")
        for eid in night_event_ids
        if events.get(eid) and events[eid].get("series_id")
    }
    for series_id in series_ids:
        instances = sorted(
            (
                e
                for e in events.values()
                if e.get("series_id") == series_id
                and e.get("status") != "cancelled"
                and (_parse_ts(e.get("starts_at")) or now) <= now
            ),
            key=lambda e: _parse_ts(e.get("starts_at")) or now,
        )
        attended = [i for i, e in enumerate(instances) if e["id"] in night_event_ids]
        if not attended:
            continue
        run = 0
        for instance in instances[attended[0] :]:
            if instance["id"] in night_event_ids:
                run += 1
                best_streak = max(best_streak, run)
            else:
                run = 0

    state = _fetch_one(db, "vfy_verification_states", user_id=uid)
    verified_at = _parse_ts(state.get("verified_at")) if state else None
    tenure_days = max(0, (now - verified_at).days) if verified_at else 0

    stats = trust.MemberStats(
        completed_nights=completed_nights,
        no_shows=no_shows,
        reviews_received=len(reviews),
        would_meet_again_positive=sum(
            1 for r in reviews if r.get("would_meet_again") is True
        ),
        hosted_clean=hosted_clean,
        best_series_streak=best_streak,
        distinct_vouches=len(_fetch(db, "rsn_vouches", vouchee_id=uid)),
        tenure_days=tenure_days,
        substantiated_reports=sum(
            1 for e in ledger if e.get("event_type") == "report_substantiated"
        ),
        identity_strikes=sum(
            1 for e in ledger if e.get("event_type") == "identity_strike"
        ),
    )

    signals: list[datetime] = []
    for c in my_checkins:
        ts = _parse_ts(c.get("arrived_at"))
        if ts:
            signals.append(ts)
    for r in _fetch(db, "rsn_reviews", reviewer_id=uid):
        ts = _parse_ts(r.get("created_at"))
        if ts:
            signals.append(ts)
    for c in _fetch(db, "rsn_encounter_confirms", confirmer_id=uid):
        ts = _parse_ts(c.get("created_at"))
        if ts:
            signals.append(ts)
    dormant_days = max(0, (now - max(signals)).days) if signals else 0

    earned = trust.earned_tier(stats)
    result = trust.compute_standing(stats, dormant_days=dormant_days)
    score = sum(float(e.get("weight") or 0) for e in ledger)
    if float(score).is_integer():
        score = int(score)
    existing = _fetch_one(db, "rsn_trust_scores", user_id=uid)
    patch = {
        "score": score,
        "band": result.band,
        "standing_tier": result.tier,
        "computed_at": now,
    }
    if existing is not None:
        db.table("rsn_trust_scores").update(patch).eq("user_id", uid).execute()
    else:
        _insert(db, "rsn_trust_scores", {"user_id": uid, **patch})
    return {
        "user_id": uid,
        "standing_tier": result.tier,
        "earned_tier": earned,
        "band": result.band,
        "suspended": result.suspended,
        "score": score,
        "completed_nights": completed_nights,
        "no_shows": no_shows,
        "show_rate": trust.show_rate(stats),
        "would_meet_again_rate": trust.would_meet_again_rate(stats),
        "hosted_clean": hosted_clean,
        "best_series_streak": best_streak,
        "dormant_days": dormant_days,
    }


# ---------------------------------------------------------------------------
# Safety Desk (TW-940, Phase 6 minimum for the Jan 1 Denver launch)
#
# Interim trust model (Justynn's ruling, TW-937): no live identity
# verification at launch; accountability is behavioral (standing,
# strikes, bans). Nothing in this section touches verification,
# weakens the Section 5.9 age-band fail-closed rules, or widens
# ghost-town scope: desk endpoints are staff machinery and owner-
# scoped disclosure reads, never member surfaces, and they return
# no age-rated content.
#
# AUTHORIZATION (security-relevant; for Rosa's review and Rex's
# posture pass):
# - Staff: an rsn_safety_staff row (migration_social_v3, shipped
#   UNAPPLIED with this change) with status 'active'. Role 'staff'
#   may triage, sanction (strike / suspend / restrict), build
#   evidence packets, and intake external disclosures. Role
#   'safety_lead' additionally may issue bans, decide appeals,
#   set legal holds, and decide external disclosures. No row or a
#   non-active row is not staff: fail closed. Desk accounts are
#   provisioned by hand into that table; there is no self-service
#   path to a desk role.
# - Ambassadors: an rsn_ambassadors row with status 'active'
#   (TW-648 scope). They may list / claim only categories that
#   safety.ambassador_may_triage allows AND whose case metro is
#   their metro (an unresolvable metro fails closed to staff),
#   and may only resolve as 'unfounded' or 'escalated'. They may
#   never sanction, ban, touch evidence, decide appeals, or
#   answer disclosures.
# - A reviewer never claims or decides their own case: the
#   reporter cannot claim their own report, the SUBJECT of a
#   report cannot claim or resolve their own case at all (fix
#   round 1: the subject refusal is total, not reporter-only), a
#   sanction's issuer can neither acknowledge nor decide its
#   appeal (fresh reviewer, Section 6.6), and nobody sanctions or
#   bans themselves through a case. Appeal decisions additionally
#   require safety_lead, the same grade that issues bans.
# - All desk authorization routes through _require_desk (triage
#   included, ambassadors via its ambassador branch): a banned or
#   suspended desk actor loses desk power, triage included. The
#   disclosure staff view carries the same gate, and a staff
#   member who is the case subject gets the subject view instead.
# - Appeal intake is the one desk write open to a banned or
#   suspended member: due process requires it. It grants no voice
#   anywhere else; _require_voice still refuses every other write.
#
# AUDIT: every desk action below writes rsn_audit_log with the
# actor's user id, a dotted action name, the target, and the
# sha256 of a detail string. That includes the sensitive reads:
# the triage queue list, the appeals list, evidence packet reads,
# and every disclosure view (the audience is the detail). Raw
# notes never enter the audit log; they live on their case rows
# (resolution_note, sanction / ban note, appeal outcome_note),
# which are staff-side only. Subject disclosure views project
# sanctions and bans without the note or issued_by.
#
# ENFORCEMENT HOOKS: bans write rsn_bans rows keyed by identity
# anchor, which get_member_context already enforces (platform
# scope, unlifted). Sanctions write rsn_sanctions rows;
# get_member_context now also resolves active 'suspension' rows
# (voice removed until expiry) and 'restriction' rows (hosting
# and RSVP removed) from them. A strike additionally writes the
# identity_strike trust event the standing job already counts.
# Sanctions and bans are anchored: a subject with no identity
# anchor cannot be sanctioned through the desk (ConflictError),
# because an unanchored sanction could never be enforced.
# ---------------------------------------------------------------------------

_DESK_STAFF = "staff"
_DESK_LEAD = "lead"
_DESK_AMBASSADOR = "ambassador"

REPORT_SUBSTANTIATED_WEIGHT = -5
REPORT_UNFOUNDED_WEIGHT = 1


def _audit(db, actor_id, action: str, target: str, detail: str, *, now) -> None:
    _insert(
        db,
        "rsn_audit_log",
        {
            "id": str(uuid4()),
            "actor": str(actor_id),
            "action": action,
            "target": target,
            "detail_hash": hashlib.sha256(detail.encode("utf-8")).hexdigest(),
            "created_at": now,
        },
    )


def _desk_role(db, user_id) -> tuple[str, dict | None]:
    """Resolve the caller's desk role fail-closed. Staff outranks
    ambassador when both rows exist (staff scope is wider)."""
    staff = _fetch_one(db, "rsn_safety_staff", user_id=str(user_id))
    if staff is not None and staff.get("status") == "active":
        return (
            _DESK_LEAD if staff.get("role") == "safety_lead" else _DESK_STAFF,
            staff,
        )
    ambassador = _fetch_one(db, "rsn_ambassadors", user_id=str(user_id))
    if ambassador is not None and ambassador.get("status") == "active":
        return (_DESK_AMBASSADOR, ambassador)
    return ("none", None)


def _require_desk(
    db, actor_id, *, lead: bool = False, allow_ambassador: bool = False
) -> tuple[MemberContext, str, dict]:
    """The one gate for desk authorization (TW-940 fix round 1:
    triage used to hand-roll its own role check and never tested
    suspension). Every desk actor needs a live desk row and must
    not themselves be banned or suspended; ambassadors (only where
    the caller allows them) additionally need voice, because they
    are members first. Age-band attestation is NOT required: the
    desk is staff machinery, not a member surface, and no
    age-rated content moves through it."""
    ctx = get_member_context(db, actor_id)
    _require_not_banned(ctx)
    if ctx.suspended:
        raise ForbiddenError("account suspended by the safety desk")
    role, row = _desk_role(db, actor_id)
    if lead:
        if role != _DESK_LEAD:
            raise ForbiddenError("safety_lead role required for this action")
    elif role == _DESK_AMBASSADOR and allow_ambassador:
        _require_voice(ctx)
    elif role not in (_DESK_STAFF, _DESK_LEAD):
        raise ForbiddenError("safety staff role required for this action")
    return ctx, role, row


def _ambassador_may_see(role_row: dict, report: dict) -> bool:
    """Ambassador case visibility: eligible category AND the case
    metro is the ambassador's metro. No metro, no case."""
    if not safety.ambassador_may_triage(report.get("category") or ""):
        return False
    metro = report.get("metro")
    return metro is not None and metro == role_row.get("metro")


def _report_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "reporter_id": row["reporter_id"],
        "subject_user_id": row.get("subject_user_id"),
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "category": row["category"],
        "description": row["description"],
        "status": row["status"],
        "assigned_to": row.get("assigned_to"),
        "metro": row.get("metro"),
        # Intake routing (TW-648): serious categories land on staff,
        # everything else may be worked by ambassadors first-line.
        "queue": (
            "ambassador" if safety.ambassador_may_triage(row["category"]) else "staff"
        ),
        "resolution_note": row.get("resolution_note"),
        "created_at": _iso(row.get("created_at")),
        "resolved_at": _iso(row.get("resolved_at")),
    }


def _report_or_404(db, report_id) -> dict:
    report = _fetch_one(db, "rsn_reports", id=str(report_id))
    if report is None:
        raise NotFoundError()
    return report


def _report_metro(db, payload: models.ReportCreate) -> str | None:
    """Resolve a report's metro for ambassador scoping. Only group
    and event targets resolve; anything else is None and therefore
    staff-only (fail closed)."""
    if payload.target_type == "group":
        group = _fetch_one(db, "rsn_groups", id=str(payload.target_id))
        return group.get("metro") if group else None
    if payload.target_type == "event":
        event = _fetch_one(db, "rsn_events", id=str(payload.target_id))
        if event is None:
            return None
        group = _fetch_one(db, "rsn_groups", id=event["group_id"])
        return group.get("metro") if group else None
    return None


def create_report(
    db, reporter_id: UUID, payload: models.ReportCreate, *, now=None
) -> dict:
    ctx = get_member_context(db, reporter_id)
    _require_voice(ctx)
    subject_id = payload.subject_user_id
    if subject_id is None and payload.target_type == "user":
        subject_id = payload.target_id
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_reports",
        {
            "id": str(uuid4()),
            "reporter_id": ctx.user_id,
            "subject_user_id": str(subject_id) if subject_id else None,
            "target_type": payload.target_type,
            "target_id": str(payload.target_id),
            "category": payload.category,
            "description": payload.description,
            "status": "open",
            "assigned_to": None,
            "metro": _report_metro(db, payload),
            "resolution_note": None,
            "created_at": now,
            "resolved_at": None,
        },
    )
    _audit(
        db,
        ctx.user_id,
        "report.filed",
        f"report:{row['id']}",
        payload.category,
        now=now,
    )
    return _report_out(row)


def list_report_queue(
    db, actor_id: UUID, *, status: str | None = None, now=None
) -> list[dict]:
    ctx, role, role_row = _require_desk(db, actor_id, allow_ambassador=True)
    reports = _fetch(db, "rsn_reports")
    if status is not None:
        reports = [r for r in reports if r.get("status") == status]
    if role == _DESK_AMBASSADOR:
        reports = [
            r
            for r in reports
            if r.get("status") in ("open", "triaged")
            and (r.get("assigned_to") is None or r.get("assigned_to") == ctx.user_id)
            and _ambassador_may_see(role_row, r)
        ]
    reports.sort(key=lambda r: str(r.get("created_at") or ""))
    now = _resolve_now(now)
    _audit(
        db,
        ctx.user_id,
        "report.queue_listed",
        "reports:queue",
        f"role={role}",
        now=now,
    )
    return [_report_out(r) for r in reports]


def claim_report(db, actor_id: UUID, report_id: UUID, *, now=None) -> dict:
    ctx, role, role_row = _require_desk(db, actor_id, allow_ambassador=True)
    report = _report_or_404(db, report_id)
    if report["reporter_id"] == ctx.user_id:
        raise ForbiddenError("the reporter cannot triage their own report")
    if report.get("subject_user_id") and report["subject_user_id"] == ctx.user_id:
        # TW-940 fix round 1 (Rosa blocker): the subject of a case
        # never works it, whatever desk role they hold.
        raise ForbiddenError("the subject cannot triage their own case")
    if report.get("status") != "open":
        raise ConflictError(f"report is {report.get('status')}, not open")
    if role == _DESK_AMBASSADOR and not _ambassador_may_see(role_row, report):
        raise ForbiddenError("this category is staff-only at intake")
    now = _resolve_now(now)
    updated = _update_by_id(
        db,
        "rsn_reports",
        report["id"],
        {"status": "triaged", "assigned_to": ctx.user_id},
    )
    _audit(db, ctx.user_id, "report.claimed", f"report:{report['id']}", role, now=now)
    return _report_out(
        updated or {**report, "status": "triaged", "assigned_to": ctx.user_id}
    )


def _linked_sanctions(db, report_id) -> list[dict]:
    return _fetch(db, "rsn_sanctions", source_ref=f"report:{report_id}")


def _linked_bans(db, report_id) -> list[dict]:
    return _fetch(db, "rsn_bans", source_report_id=str(report_id))


def _require_assignee(report: dict, actor_id: str) -> None:
    assigned = report.get("assigned_to")
    if assigned is None:
        raise ConflictError("claim the report before acting on it")
    if assigned != actor_id:
        raise ForbiddenError("report is assigned to another reviewer")


def resolve_report(
    db, actor_id: UUID, report_id: UUID, payload: models.ReportResolve, *, now=None
) -> dict:
    ctx, role, role_row = _require_desk(db, actor_id, allow_ambassador=True)
    report = _report_or_404(db, report_id)
    if report.get("subject_user_id") and report["subject_user_id"] == ctx.user_id:
        # Defense in depth with claim_report: even a subject who
        # somehow holds the claim never resolves their own case
        # (and never collects the report_unfounded trust event).
        raise ForbiddenError("the subject cannot resolve their own case")
    if role == _DESK_AMBASSADOR and not _ambassador_may_see(role_row, report):
        raise ForbiddenError("this category is staff-only at intake")
    if report.get("status") not in ("open", "triaged"):
        raise ConflictError(f"report is already {report.get('status')}")
    _require_assignee(report, ctx.user_id)
    if role == _DESK_AMBASSADOR and payload.outcome not in ("unfounded", "escalated"):
        # TW-648 bounded decisions: ambassadors clear or escalate;
        # 'actioned' is a staff decision backed by a staff sanction.
        raise ForbiddenError("ambassadors may only close as unfounded or escalate")
    now = _resolve_now(now)
    if payload.outcome == "escalated":
        updated = _update_by_id(
            db,
            "rsn_reports",
            report["id"],
            {"status": "open", "assigned_to": None, "resolution_note": payload.note},
        )
        _audit(
            db,
            ctx.user_id,
            "report.escalated",
            f"report:{report['id']}",
            payload.note,
            now=now,
        )
        return _report_out(updated or {**report, "status": "open", "assigned_to": None})
    if payload.outcome == "actioned":
        if not _linked_sanctions(db, report["id"]) and not _linked_bans(
            db, report["id"]
        ):
            raise ConflictError(
                "an actioned resolution requires a sanction or ban issued on this case first"
            )
    updated = _update_by_id(
        db,
        "rsn_reports",
        report["id"],
        {
            "status": payload.outcome,
            "resolved_at": now,
            "resolution_note": payload.note,
        },
    )
    subject_id = report.get("subject_user_id")
    if subject_id:
        if payload.outcome == "actioned":
            _append_trust_event(
                db,
                subject_id,
                "report_substantiated",
                REPORT_SUBSTANTIATED_WEIGHT,
                ref_type="report",
                ref_id=report["id"],
                now=now,
            )
        elif payload.outcome == "unfounded":
            _append_trust_event(
                db,
                subject_id,
                "report_unfounded",
                REPORT_UNFOUNDED_WEIGHT,
                ref_type="report",
                ref_id=report["id"],
                now=now,
            )
    _audit(
        db,
        ctx.user_id,
        "report.resolved",
        f"report:{report['id']}",
        f"outcome={payload.outcome}",
        now=now,
    )
    return _report_out(updated or {**report, "status": payload.outcome})


_SANCTION_LEVELS = {
    "strike": "strike",
    "suspend": "suspension",
    "restrict": "restriction",
}


def _sanction_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "identity_anchor_hash": row.get("identity_anchor_hash"),
        "level": row["level"],
        "reason_category": row["reason_category"],
        "source_ref": row.get("source_ref"),
        "scope": row.get("scope"),
        "group_id": row.get("group_id"),
        "issued_by": row["issued_by"],
        "expires_at": _iso(row.get("expires_at")),
        "status": row["status"],
        "note": row.get("note"),
        "created_at": _iso(row.get("created_at")),
    }


def _case_subject(db, report: dict) -> tuple[str, MemberContext]:
    subject_id = report.get("subject_user_id")
    if not subject_id:
        raise ConflictError("report has no subject user to act on")
    subject_ctx = get_member_context(db, subject_id)
    if not subject_ctx.anchor_hash:
        raise ConflictError(
            "subject has no identity anchor; an unanchored sanction cannot be enforced"
        )
    return subject_id, subject_ctx


def issue_sanction(
    db, actor_id: UUID, report_id: UUID, payload: models.SanctionCreate, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id)
    report = _report_or_404(db, report_id)
    if report.get("status") != "triaged":
        raise ConflictError("sanctions issue from a claimed (triaged) case")
    _require_assignee(report, ctx.user_id)
    subject_id, subject_ctx = _case_subject(db, report)
    if subject_id == ctx.user_id:
        raise ForbiddenError("a reviewer cannot sanction themselves")
    level = _SANCTION_LEVELS[payload.action]
    now = _resolve_now(now)
    if level == "suspension" and not safety.suspension_within_cap(
        payload.expires_at, now
    ):
        raise ConflictError(
            "a suspension requires an expiry within 72 hours (Section 6.2 cap)"
        )
    if level == "strike" and payload.expires_at is not None:
        raise ConflictError("a strike does not carry an expiry")
    for existing in _linked_sanctions(db, report["id"]):
        if existing.get("status") == "active" and existing.get("level") == level:
            raise ConflictError(
                f"an active {level} sanction already exists on this case"
            )
    row = _insert(
        db,
        "rsn_sanctions",
        {
            "id": str(uuid4()),
            "identity_anchor_hash": subject_ctx.anchor_hash,
            "user_id": subject_id,
            "level": level,
            "reason_category": payload.reason_category,
            "source_ref": f"report:{report['id']}",
            "scope": "platform",
            "group_id": None,
            "issued_by": ctx.user_id,
            "expires_at": payload.expires_at,
            "status": "active",
            "note": payload.note,
            "created_at": now,
        },
    )
    if level == "strike":
        _append_trust_event(
            db,
            subject_id,
            "identity_strike",
            IDENTITY_STRIKE_WEIGHT,
            ref_type="sanction",
            ref_id=row["id"],
            now=now,
        )
    _audit(
        db,
        ctx.user_id,
        "sanction.issued",
        f"sanction:{row['id']}",
        f"level={level}",
        now=now,
    )
    return _sanction_out(row)


def _ban_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "identity_anchor_hash": row.get("identity_anchor_hash"),
        "scope": row["scope"],
        "group_id": row.get("group_id"),
        "reason_category": row["reason_category"],
        "evidence_packet_ref": row.get("evidence_packet_ref"),
        "issued_by": row["issued_by"],
        "issued_at": _iso(row.get("issued_at")),
        "appeal_status": row.get("appeal_status"),
        "lifted_at": _iso(row.get("lifted_at")),
        "source_report_id": row.get("source_report_id"),
        "note": row.get("note"),
    }


def issue_ban(
    db, actor_id: UUID, report_id: UUID, payload: models.BanCreate, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id, lead=True)
    report = _report_or_404(db, report_id)
    if report.get("status") != "triaged":
        raise ConflictError("bans issue from a claimed (triaged) case")
    _require_assignee(report, ctx.user_id)
    subject_id, subject_ctx = _case_subject(db, report)
    if subject_id == ctx.user_id:
        raise ForbiddenError("a reviewer cannot ban themselves")
    group_id = str(payload.group_id) if payload.group_id else None
    for existing in _fetch(
        db, "rsn_bans", identity_anchor_hash=subject_ctx.anchor_hash
    ):
        if existing.get("lifted_at") is not None:
            continue
        if existing.get("scope") == "platform":
            raise ConflictError(
                "an active platform ban already exists for this identity"
            )
        if (
            payload.scope == "group"
            and existing.get("scope") == "group"
            and existing.get("group_id") == group_id
        ):
            raise ConflictError(
                "an active group ban already exists for this identity in this group"
            )
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_bans",
        {
            "id": str(uuid4()),
            "identity_anchor_hash": subject_ctx.anchor_hash,
            "scope": payload.scope,
            "group_id": str(payload.group_id) if payload.group_id else None,
            "reason_category": payload.reason_category,
            "evidence_packet_ref": payload.evidence_packet_ref,
            "issued_by": ctx.user_id,
            "issued_at": now,
            "appeal_status": "none",
            "lifted_at": None,
            "source_report_id": report["id"],
            "note": payload.note,
        },
    )
    _audit(
        db,
        ctx.user_id,
        "ban.issued",
        f"ban:{row['id']}",
        f"scope={payload.scope}",
        now=now,
    )
    return _ban_out(row)


# ---------------------------------------------------------------------------
# Appeals (Section 6.6: ack within 24h, decision within 7 days, by a
# fresh reviewer). Intake is open to the sanctioned / banned member
# themselves even while banned or suspended: see the section header.
# ---------------------------------------------------------------------------


def _appeal_out(row: dict) -> dict:
    created = _parse_ts(row.get("created_at"))
    ack_due = created + safety.APPEAL_ACK_SLA if created else None
    return {
        "id": row["id"],
        "identity_anchor_hash": row.get("identity_anchor_hash"),
        "appellant_user_id": row["appellant_user_id"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "statement": row["statement"],
        "status": row["status"],
        "assigned_to": row.get("assigned_to"),
        "sla_due_at": _iso(row.get("sla_due_at")),
        "ack_due_at": ack_due.isoformat() if ack_due else None,
        "ack_at": _iso(row.get("ack_at")),
        "decided_at": _iso(row.get("decided_at")),
        "decided_by": row.get("decided_by"),
        "outcome_note": row.get("outcome_note"),
        "created_at": _iso(row.get("created_at")),
    }


def _appeal_or_404(db, appeal_id) -> dict:
    appeal = _fetch_one(db, "rsn_appeals", id=str(appeal_id))
    if appeal is None:
        raise NotFoundError()
    return appeal


def create_appeal(
    db, appellant_id: UUID, payload: models.AppealCreate, *, now=None
) -> dict:
    # Deliberately NOT _require_voice / _require_not_banned: a banned
    # or suspended member must still be able to appeal. This creates
    # no voice, no read access, and nothing public.
    appellant = get_member_context(db, appellant_id)
    anchor = None
    if payload.target_type == "sanction":
        target = _fetch_one(db, "rsn_sanctions", id=str(payload.target_id))
        if target is None:
            raise NotFoundError()
        if target["user_id"] != appellant.user_id:
            raise ForbiddenError("only the sanctioned member may appeal a sanction")
        if target.get("status") != "active":
            raise ConflictError(f"sanction is {target.get('status')}, not active")
        anchor = target.get("identity_anchor_hash")
    else:
        target = _fetch_one(db, "rsn_bans", id=str(payload.target_id))
        if target is None:
            raise NotFoundError()
        if (
            not appellant.anchor_hash
            or target.get("identity_anchor_hash") != appellant.anchor_hash
        ):
            raise ForbiddenError("only the banned member may appeal a ban")
        if target.get("lifted_at") is not None:
            raise ConflictError("ban is already lifted")
        anchor = target.get("identity_anchor_hash")
    for existing in _fetch(db, "rsn_appeals", target_id=str(payload.target_id)):
        if existing.get("status") in ("submitted", "under_review"):
            raise ConflictError("an appeal is already pending for this target")
    now = _resolve_now(now)
    sla = safety.appeal_sla(now)
    row = _insert(
        db,
        "rsn_appeals",
        {
            "id": str(uuid4()),
            "identity_anchor_hash": anchor,
            "appellant_user_id": appellant.user_id,
            "target_type": payload.target_type,
            "target_id": str(payload.target_id),
            "statement": payload.statement,
            "status": "submitted",
            "assigned_to": None,
            "sla_due_at": sla.decision_due_at,
            "decided_at": None,
            "outcome_note": None,
            "created_at": now,
            "ack_at": None,
            "decided_by": None,
        },
    )
    if payload.target_type == "ban":
        _update_by_id(db, "rsn_bans", target["id"], {"appeal_status": "pending"})
    _audit(
        db,
        appellant.user_id,
        "appeal.filed",
        f"appeal:{row['id']}",
        payload.target_type,
        now=now,
    )
    return _appeal_out(row)


def list_appeals(
    db, actor_id: UUID, *, status: str | None = None, now=None
) -> list[dict]:
    ctx, role, _row = _require_desk(db, actor_id)
    appeals = _fetch(db, "rsn_appeals")
    if status is not None:
        appeals = [a for a in appeals if a.get("status") == status]
    appeals.sort(key=lambda a: str(a.get("created_at") or ""))
    _audit(
        db,
        ctx.user_id,
        "appeal.listed",
        "appeals",
        f"role={role}",
        now=_resolve_now(now),
    )
    return [_appeal_out(a) for a in appeals]


def acknowledge_appeal(db, actor_id: UUID, appeal_id: UUID, *, now=None) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id)
    appeal = _appeal_or_404(db, appeal_id)
    if appeal.get("status") != "submitted":
        raise ConflictError(f"appeal is {appeal.get('status')}, not submitted")
    if appeal["appellant_user_id"] == ctx.user_id:
        raise ForbiddenError("the appellant cannot acknowledge their own appeal")
    table = "rsn_sanctions" if appeal["target_type"] == "sanction" else "rsn_bans"
    target = _fetch_one(db, table, id=appeal["target_id"])
    if target is None:
        raise NotFoundError()
    if target.get("issued_by") == ctx.user_id:
        # Acknowledge assigns the case to the acknowledger, so the
        # issuer acknowledging here would self-assign the appeal of
        # their own action (same conflict family as decide_appeal).
        raise ForbiddenError(
            "the issuer cannot acknowledge this appeal (fresh reviewer required)"
        )
    now = _resolve_now(now)
    updated = _update_by_id(
        db,
        "rsn_appeals",
        appeal["id"],
        {"status": "under_review", "assigned_to": ctx.user_id, "ack_at": now},
    )
    _audit(
        db, ctx.user_id, "appeal.acknowledged", f"appeal:{appeal['id']}", "", now=now
    )
    return _appeal_out(updated or appeal)


def decide_appeal(
    db, actor_id: UUID, appeal_id: UUID, payload: models.AppealDecision, *, now=None
) -> dict:
    # safety_lead only (TW-940 fix round 1): issuing a ban takes a
    # lead, so overturning one cannot take less. The section header,
    # the v3 migration header, and the test docstring all stated
    # this from the start; the gate now matches them.
    ctx, role, _row = _require_desk(db, actor_id, lead=True)
    appeal = _appeal_or_404(db, appeal_id)
    if appeal.get("status") not in ("submitted", "under_review"):
        raise ConflictError(f"appeal is already {appeal.get('status')}")
    if appeal["appellant_user_id"] == ctx.user_id:
        raise ForbiddenError("the appellant cannot decide their own appeal")
    assigned = appeal.get("assigned_to")
    if assigned is not None and assigned != ctx.user_id and role != _DESK_LEAD:
        raise ForbiddenError("appeal is assigned to another reviewer")
    table = "rsn_sanctions" if appeal["target_type"] == "sanction" else "rsn_bans"
    target = _fetch_one(db, table, id=appeal["target_id"])
    if target is None:
        raise NotFoundError()
    if target.get("issued_by") == ctx.user_id:
        # Fresh reviewer (Section 6.6): the issuer never decides
        # the appeal of their own sanction or ban.
        raise ForbiddenError(
            "the issuer cannot decide this appeal (fresh reviewer required)"
        )
    now = _resolve_now(now)
    if payload.decision == "overturned":
        if appeal["target_type"] == "sanction":
            _update_by_id(db, "rsn_sanctions", target["id"], {"status": "overturned"})
        else:
            _update_by_id(
                db,
                "rsn_bans",
                target["id"],
                {"lifted_at": now, "appeal_status": "overturned"},
            )
    elif payload.decision == "upheld":
        if appeal["target_type"] == "ban":
            _update_by_id(db, "rsn_bans", target["id"], {"appeal_status": "upheld"})
    else:  # modified
        if appeal["target_type"] != "sanction":
            raise ConflictError(
                "a ban cannot be modified on appeal; uphold or overturn it"
            )
        if payload.modified_level is None:
            raise ConflictError("a modified decision requires modified_level")
        patch = {"level": payload.modified_level}
        if payload.modified_level != "suspension":
            patch["expires_at"] = None
        elif not safety.suspension_within_cap(_parse_ts(target.get("expires_at")), now):
            # The modified outcome keeps the target's existing
            # expiry, so re-validate it against the Section 6.2
            # 72h cap at decision time: present, future, in cap.
            raise ConflictError(
                "modifying to a suspension requires an existing expiry "
                "within 72 hours (Section 6.2 cap)"
            )
        _update_by_id(db, "rsn_sanctions", target["id"], patch)
    updated = _update_by_id(
        db,
        "rsn_appeals",
        appeal["id"],
        {
            "status": payload.decision,
            "decided_at": now,
            "decided_by": ctx.user_id,
            "outcome_note": payload.outcome_note,
            "assigned_to": assigned or ctx.user_id,
        },
    )
    _audit(
        db,
        ctx.user_id,
        "appeal.decided",
        f"appeal:{appeal['id']}",
        f"decision={payload.decision}",
        now=now,
    )
    return _appeal_out(updated or appeal)


# ---------------------------------------------------------------------------
# Evidence packets (hash-chained manifests, spec Section 4) and the
# disclosure workflows. External disclosures are records only: no
# endpoint in this section sends anything anywhere.
# ---------------------------------------------------------------------------


def _packet_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "case_type": row["case_type"],
        "case_ref": row["case_ref"],
        "snapshot_manifest": row.get("snapshot_manifest"),
        "created_by": row["created_by"],
        "created_at": _iso(row.get("created_at")),
        "legal_hold": bool(row.get("legal_hold")),
        "retention_until": _iso(row.get("retention_until")),
    }


def _packet_or_404(db, packet_id) -> dict:
    packet = _fetch_one(db, "rsn_evidence_packets", id=str(packet_id))
    if packet is None:
        raise NotFoundError()
    return packet


def create_evidence_packet(
    db, actor_id: UUID, payload: models.EvidencePacketCreate, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id)
    case_table = "rsn_reports" if payload.case_type == "report" else "rsn_bans"
    if _fetch_one(db, case_table, id=payload.case_ref) is None:
        raise NotFoundError()
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_evidence_packets",
        {
            "id": str(uuid4()),
            "case_type": payload.case_type,
            "case_ref": payload.case_ref,
            "snapshot_manifest": {"items": [], "chain_head": None},
            "created_by": ctx.user_id,
            "created_at": now,
            "legal_hold": False,
            "retention_until": now + timedelta(days=payload.retention_days),
        },
    )
    _audit(
        db,
        ctx.user_id,
        "evidence.created",
        f"packet:{row['id']}",
        payload.case_type,
        now=now,
    )
    return _packet_out(row)


def get_evidence_packet(db, actor_id: UUID, packet_id: UUID, *, now=None) -> dict:
    ctx, role, _row = _require_desk(db, actor_id)
    packet = _packet_or_404(db, packet_id)
    _audit(
        db,
        ctx.user_id,
        "evidence.viewed",
        f"packet:{packet['id']}",
        f"role={role}",
        now=_resolve_now(now),
    )
    return _packet_out(packet)


def attach_evidence(
    db, actor_id: UUID, packet_id: UUID, payload: models.EvidenceItemIn, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id)
    packet = _packet_or_404(db, packet_id)
    if packet.get("legal_hold"):
        raise ConflictError("packet is on legal hold; its manifest is frozen")
    now = _resolve_now(now)
    manifest = dict(packet.get("snapshot_manifest") or {})
    items = list(manifest.get("items") or [])
    prev_hash = items[-1]["item_hash"] if items else None
    added_at = now.isoformat()
    item_hash = safety.evidence_item_hash(
        prev_hash,
        kind=payload.kind,
        ref=payload.ref,
        content_sha256=payload.content_sha256,
        actor=ctx.user_id,
        added_at=added_at,
    )
    items.append(
        {
            "kind": payload.kind,
            "ref": payload.ref,
            "content_sha256": payload.content_sha256,
            "note": payload.note,
            "added_by": ctx.user_id,
            "added_at": added_at,
            "item_hash": item_hash,
            "prev_hash": prev_hash,
        }
    )
    manifest = {"items": items, "chain_head": item_hash}
    updated = _update_by_id(
        db, "rsn_evidence_packets", packet["id"], {"snapshot_manifest": manifest}
    )
    _audit(
        db,
        ctx.user_id,
        "evidence.attached",
        f"packet:{packet['id']}",
        item_hash,
        now=now,
    )
    return _packet_out(updated or {**packet, "snapshot_manifest": manifest})


def set_evidence_legal_hold(
    db, actor_id: UUID, packet_id: UUID, payload: models.LegalHoldIn, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id, lead=True)
    packet = _packet_or_404(db, packet_id)
    now = _resolve_now(now)
    updated = _update_by_id(
        db, "rsn_evidence_packets", packet["id"], {"legal_hold": payload.legal_hold}
    )
    _audit(
        db,
        ctx.user_id,
        "evidence.legal_hold",
        f"packet:{packet['id']}",
        f"legal_hold={payload.legal_hold}",
        now=now,
    )
    return _packet_out(updated or {**packet, "legal_hold": payload.legal_hold})


def _sanction_out_subject(row: dict) -> dict:
    """Subject-safe sanction projection: what binds the member and
    when it ends. Never the staff-side note, never issued_by, never
    the identity anchor (staff internals; see the section header)."""
    return {
        "id": row["id"],
        "level": row["level"],
        "reason_category": row["reason_category"],
        "scope": row.get("scope"),
        "group_id": row.get("group_id"),
        "expires_at": _iso(row.get("expires_at")),
        "status": row["status"],
        "created_at": _iso(row.get("created_at")),
    }


def _ban_out_subject(row: dict) -> dict:
    """Subject-safe ban projection, same discipline as
    _sanction_out_subject."""
    return {
        "id": row["id"],
        "scope": row["scope"],
        "group_id": row.get("group_id"),
        "reason_category": row["reason_category"],
        "issued_at": _iso(row.get("issued_at")),
        "appeal_status": row.get("appeal_status"),
        "lifted_at": _iso(row.get("lifted_at")),
    }


def get_report_disclosure(db, viewer_id: UUID, report_id: UUID, *, now=None) -> dict:
    """What each audience may learn about a case, as data. Nothing
    is sent anywhere; the surfaces that render this decide how.

    Audience precedence (TW-940 fix round 1): the case SUBJECT is
    resolved first, so a staff member who is the subject of the
    case gets the subject view, never the staff view (the staff
    view carries the reporter's identity and the report text:
    reporter safety). The staff view itself is gated on a live,
    non-banned, non-suspended desk status; a banned or suspended
    staff member falls through to the party views below and, if
    they are not a party, to the restricted shell.

    - staff / lead (active, not banned, not suspended, and not the
      case subject): the full case (report, sanctions, bans,
      appeals);
    - reporter: outcome only (status, category, sanction levels,
      resolution note, dates). Never evidence, never staff internals;
    - subject: that a case exists against them, its category, the
      sanctions / bans that bind them (subject-safe projections:
      no staff-side note, no issued_by), and their appeal position.
      Never the reporter's identity or the report's description
      (reporter safety);
    - anyone else: the Section 5.9 restricted shell (NotFound).

    Every successful view writes an audit row (TW-801 lens): a
    disclosure read is exactly what the append-only log exists
    for."""
    now = _resolve_now(now)
    viewer = get_member_context(db, viewer_id, now=now)
    report = _report_or_404(db, report_id)
    sanctions = _linked_sanctions(db, report["id"])
    bans = _linked_bans(db, report["id"])
    is_subject = (
        report.get("subject_user_id") is not None
        and report["subject_user_id"] == viewer.user_id
    )
    role, _row = _desk_role(db, viewer_id)
    staff_view = (
        role in (_DESK_STAFF, _DESK_LEAD)
        and not is_subject
        and not viewer.banned
        and not viewer.suspended
    )
    if staff_view:
        target_ids = {s["id"] for s in sanctions} | {b["id"] for b in bans}
        appeals = [
            _appeal_out(a)
            for a in _fetch(db, "rsn_appeals")
            if a.get("target_id") in target_ids
        ]
        _audit(
            db,
            viewer.user_id,
            "report.disclosure_viewed",
            f"report:{report['id']}",
            "audience=staff",
            now=now,
        )
        return {
            "audience": "staff",
            "report": _report_out(report),
            "sanctions": [_sanction_out(s) for s in sanctions],
            "bans": [_ban_out(b) for b in bans],
            "appeals": appeals,
        }
    if is_subject:
        _audit(
            db,
            viewer.user_id,
            "report.disclosure_viewed",
            f"report:{report['id']}",
            "audience=subject",
            now=now,
        )
        return {
            "audience": "subject",
            "report_id": report["id"],
            "status": report["status"],
            "category": report["category"],
            "target_type": report["target_type"],
            "sanctions": [_sanction_out_subject(s) for s in sanctions],
            "bans": [_ban_out_subject(b) for b in bans],
            "can_appeal": any(s.get("status") == "active" for s in sanctions)
            or any(b.get("lifted_at") is None for b in bans),
            "created_at": _iso(report.get("created_at")),
            "resolved_at": _iso(report.get("resolved_at")),
        }
    if report["reporter_id"] == viewer.user_id:
        _audit(
            db,
            viewer.user_id,
            "report.disclosure_viewed",
            f"report:{report['id']}",
            "audience=reporter",
            now=now,
        )
        return {
            "audience": "reporter",
            "report_id": report["id"],
            "status": report["status"],
            "category": report["category"],
            "sanction_levels": [s["level"] for s in sanctions],
            "ban_scopes": [b["scope"] for b in bans if b.get("lifted_at") is None],
            "resolution_note": report.get("resolution_note"),
            "created_at": _iso(report.get("created_at")),
            "resolved_at": _iso(report.get("resolved_at")),
        }
    raise NotFoundError()


def _disclosure_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "packet_id": row["packet_id"],
        "request_type": row["request_type"],
        "requesting_agency": row["requesting_agency"],
        "legal_review_by": row.get("legal_review_by"),
        "disclosed_at": _iso(row.get("disclosed_at")),
        "scope_note": row.get("scope_note"),
        "status": row.get("status"),
        "created_at": _iso(row.get("created_at")),
    }


def create_disclosure(
    db, actor_id: UUID, payload: models.DisclosureCreate, *, now=None
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id)
    _packet_or_404(db, payload.packet_id)
    now = _resolve_now(now)
    row = _insert(
        db,
        "rsn_disclosures",
        {
            "id": str(uuid4()),
            "packet_id": str(payload.packet_id),
            "request_type": payload.request_type,
            "requesting_agency": payload.requesting_agency,
            "legal_review_by": None,
            "disclosed_at": None,
            "scope_note": payload.scope_note,
            "status": "pending_review",
            "created_at": now,
        },
    )
    _audit(
        db,
        ctx.user_id,
        "disclosure.created",
        f"disclosure:{row['id']}",
        payload.request_type,
        now=now,
    )
    return _disclosure_out(row)


def decide_disclosure(
    db,
    actor_id: UUID,
    disclosure_id: UUID,
    payload: models.DisclosureDecision,
    *,
    now=None,
) -> dict:
    ctx, _role, _row = _require_desk(db, actor_id, lead=True)
    row = _fetch_one(db, "rsn_disclosures", id=str(disclosure_id))
    if row is None:
        raise NotFoundError()
    if row.get("status") != "pending_review":
        raise ConflictError(f"disclosure is already {row.get('status')}")
    now = _resolve_now(now)
    if payload.decision == "disclose":
        patch = {
            "status": "disclosed",
            "disclosed_at": now,
            "legal_review_by": ctx.user_id,
            "scope_note": payload.scope_note or row.get("scope_note"),
        }
    else:
        patch = {
            "status": "denied",
            "legal_review_by": ctx.user_id,
            "scope_note": payload.scope_note or row.get("scope_note"),
        }
    updated = _update_by_id(db, "rsn_disclosures", row["id"], patch)
    _audit(
        db,
        ctx.user_id,
        "disclosure.decided",
        f"disclosure:{row['id']}",
        f"decision={payload.decision}",
        now=now,
    )
    return _disclosure_out(updated or {**row, **patch})

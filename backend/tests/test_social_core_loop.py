"""TW-660 Twistor Social core loop (Phase 4, first slice) — TDD.

Service-layer behavior over the merged rsn_ schema (TW-650) plus the
TW-651 gold index, with the TW-646 spec's cross-cutting rules enforced
on every path:

- groups: create (standing gate), join, membership roles, decisions;
- events: create (standing capacity caps), recurring series as
  first-class objects with materialized per-instance RSVP state;
- RSVP: per-instance, home events request + host approval with close
  friends auto-confirmed, capacity to waitlist, band-ordered promotion;
- location reveal at the query layer: 48h venue window, 3h home
  window, home addresses only to close friends + host-approved
  confirmed guests, every reveal logged, addresses never in payloads;
- age-band server-side invisibility (Section 5.9) and the ghost town
  (Section 5.11) on every read and write in this file.

The DB is an in-memory Supabase stand-in implementing exactly the
query vocabulary the service layer uses (select/eq/in_/is_/gte/lte/
order/limit/insert/update/delete). Timestamps ride as datetimes,
matching what the service writes; reads normalize the way the real
client's ISO strings require.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.social import models, service

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# In-memory Supabase stand-in
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "FakeDb", table: str):
        self._db = db
        self._table = table
        self._rows = [dict(r) for r in db.tables.get(table, [])]
        self._mode = "select"
        self._payload = None

    # -- filters -----------------------------------------------------
    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def in_(self, col, values):
        vals = set(values)
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def is_(self, col, value):
        if value == "null":
            self._rows = [r for r in self._rows if r.get(col) is None]
        elif value == "not.null":
            self._rows = [r for r in self._rows if r.get(col) is not None]
        else:  # pragma: no cover - the service never asks for this
            raise ValueError(f"unsupported is_ value {value!r}")
        return self

    def gte(self, col, value):
        self._rows = [
            r for r in self._rows if r.get(col) is not None and r.get(col) >= value
        ]
        return self

    def lte(self, col, value):
        self._rows = [
            r for r in self._rows if r.get(col) is not None and r.get(col) <= value
        ]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(
            self._rows,
            key=lambda r: (r.get(col) is None, r.get(col)),
            reverse=desc,
        )
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    # -- writes ------------------------------------------------------
    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def delete(self):
        self._mode = "delete"
        return self

    # -- execution ---------------------------------------------------
    def execute(self):
        store = self._db.tables.setdefault(self._table, [])
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", str(uuid4()))
            store.append(row)
            self._db.writes.append((self._table, dict(row)))
            return _Result([row])
        if self._mode == "update":
            ids = {r["id"] for r in self._rows}
            changed = []
            for row in store:
                if row.get("id") in ids:
                    row.update(self._payload)
                    changed.append(dict(row))
            return _Result(changed)
        if self._mode == "delete":
            ids = {r["id"] for r in self._rows}
            self._db.tables[self._table] = [
                r for r in store if r.get("id") not in ids
            ]
            return _Result([])
        return _Result([dict(r) for r in self._rows])


class FakeDb:
    """In-memory Supabase stand-in. `writes` records every insert."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.writes = []

    def table(self, name):
        return _Query(self, name)

    # test-side helpers (not part of the client surface)
    def rows(self, name):
        return self.tables.get(name, [])

    def seed(self, name, row):
        row = dict(row)
        row.setdefault("id", str(uuid4()))
        self.tables.setdefault(name, []).append(row)
        return row


# ---------------------------------------------------------------------------
# Seed helpers: member contexts ride the verify-service tables, exactly
# the way the real service resolves them (TW-658 store contract).
# ---------------------------------------------------------------------------


def add_member(
    db: FakeDb,
    *,
    tier=2,
    status="verified",
    band="age_21_plus",
    standing="verified",
    trust_band="good_standing",
    banned=False,
    trust_row=True,
):
    """Create one member: verification state + attestation + trust row.

    tier=None means the account has no verification state row at all
    (a ghost-town account). band=None means verified with no live
    attestation (fails closed to no band)."""
    user_id = str(uuid4())
    if tier is not None:
        anchor = f"anchor-{user_id}"
        db.seed(
            "vfy_identity_anchors",
            {"anchor_hash": anchor, "user_id": user_id, "is_banned": banned},
        )
        db.seed(
            "vfy_verification_states",
            {
                "identity_anchor_hash": anchor,
                "user_id": user_id,
                "status": status,
                "current_tier": tier,
            },
        )
        if band is not None:
            db.seed(
                "vfy_age_attestations",
                {
                    "identity_anchor_hash": anchor,
                    "band": band,
                    "band_as_of": NOW.date(),
                    "next_band_date": None,
                    "engine_version": "test",
                    "superseded_at": None,
                },
            )
    if trust_row:
        db.seed(
            "rsn_trust_scores",
            {
                "user_id": user_id,
                "score": 0,
                "band": trust_band,
                "standing_tier": standing,
            },
        )
    return user_id


def make_group(
    db: FakeDb,
    owner_id,
    *,
    min_age_band="age_18_plus",
    visibility="public",
    meetup_min_tier="verified",
    metro="denver",
    slug=None,
):
    return service.create_group(
        db,
        owner_id,
        models.GroupCreate(
            name="Denver Hikers",
            slug=slug or f"hikers-{uuid4().hex[:8]}",
            description="Weekly hikes around the metro.",
            category="outdoors",
            metro=metro,
            visibility=visibility,
            meetup_min_tier=meetup_min_tier,
            min_age_band=min_age_band,
        ),
        now=NOW,
    )


def join(db, group, user_id):
    return service.join_group(db, user_id, group["id"], now=NOW)


def make_event(
    db: FakeDb,
    host_id,
    group,
    *,
    starts_at=None,
    capacity=None,
    venue_type="venue",
    min_age_band="age_18_plus",
    min_tier_view="verified",
    min_tier_rsvp="verified",
    audience_scope="group",
    title="Saturday hike",
):
    return service.create_event(
        db,
        host_id,
        models.EventCreate(
            group_id=group["id"],
            title=title,
            description="A walk in the hills.",
            starts_at=starts_at or (NOW + timedelta(days=7)),
            ends_at=(starts_at or (NOW + timedelta(days=7))) + timedelta(hours=2),
            capacity=capacity,
            min_tier_view=min_tier_view,
            min_tier_rsvp=min_tier_rsvp,
            min_age_band=min_age_band,
            venue_type=venue_type,
            audience_scope=audience_scope,
        ),
        now=NOW,
    )


def set_location(db, host_id, event, *, address="1234 Trailhead Rd, Denver, CO"):
    return service.set_event_location(
        db,
        host_id,
        event["id"],
        models.EventLocationIn(
            venue_name="Trailhead",
            street_address=address,
            latitude=39.7392,
            longitude=-104.9903,
            arrival_instructions="Meet at the gate.",
        ),
        now=NOW,
    )


def rsvp(db, user_id, event, **kwargs):
    return service.request_rsvp(
        db, user_id, models.RsvpCreate(event_id=event["id"], **kwargs), now=NOW
    )


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    # A Regular-standing host: may create groups (Regular+) and events
    # up to the below-Fixture capacity cap of 12.
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    return make_group(db, host)


# ---------------------------------------------------------------------------
# Member context + ghost town enforcement
# ---------------------------------------------------------------------------


class TestMemberContext:
    def test_verified_t2_member_has_voice_and_band(self, db):
        uid = add_member(db, band="age_18_20", standing="fixture")
        ctx = service.get_member_context(db, uid)
        assert ctx.verified is True
        assert ctx.tier == 2
        assert ctx.band == "age_18_20"
        assert ctx.band_rank == 2
        assert ctx.standing_tier == "fixture"
        assert ctx.can_write is True

    def test_ghost_account_has_no_voice_and_lowest_band(self, db):
        uid = add_member(db, tier=None, trust_row=False)
        ctx = service.get_member_context(db, uid)
        assert ctx.verified is False
        assert ctx.band is None
        assert ctx.band_rank == 0
        assert ctx.can_write is False

    def test_t1_vouching_never_grants_voice(self, db):
        uid = add_member(db, tier=1)
        ctx = service.get_member_context(db, uid)
        assert ctx.can_write is False

    def test_dropped_verification_loses_voice(self, db):
        uid = add_member(db, status="dropped")
        ctx = service.get_member_context(db, uid)
        assert ctx.can_write is False

    def test_superseded_attestation_reads_as_no_band(self, db):
        uid = add_member(db)
        db.rows("vfy_age_attestations")[0]["superseded_at"] = NOW
        ctx = service.get_member_context(db, uid)
        assert ctx.band is None
        assert ctx.band_rank == 0

    def test_banned_anchor_is_refused_everywhere(self, db, host, group):
        banned = add_member(db, banned=True)
        with pytest.raises(service.ForbiddenError):
            service.join_group(db, banned, group["id"], now=NOW)
        with pytest.raises(service.ForbiddenError):
            service.list_groups(db, banned, now=NOW)


# ---------------------------------------------------------------------------
# Groups: creation gates, join, roles
# ---------------------------------------------------------------------------


class TestGroupCreation:
    def test_regular_standing_creates_group_and_owns_it(self, db, host, group):
        assert group["created_by"] == host
        assert group["min_age_band"] == "age_18_plus"
        memberships = db.rows("rsn_group_memberships")
        assert len(memberships) == 1
        assert memberships[0]["user_id"] == host
        assert memberships[0]["role"] == "owner"
        assert memberships[0]["status"] == "active"

    def test_ghost_cannot_create_group(self, db):
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            make_group(db, ghost)

    def test_verified_tier_alone_cannot_create_group(self, db):
        # Group creation requires Regular standing or better (7.7).
        uid = add_member(db, standing="verified")
        with pytest.raises(service.ForbiddenError):
            make_group(db, uid)

    def test_watch_band_cannot_create_group(self, db):
        uid = add_member(db, standing="regular", trust_band="watch")
        with pytest.raises(service.ForbiddenError):
            make_group(db, uid)

    def test_duplicate_slug_conflicts(self, db, host):
        first = make_group(db, host, slug="same-slug")
        assert first["slug"] == "same-slug"
        with pytest.raises(service.ConflictError):
            make_group(db, host, slug="same-slug")


class TestGroupJoin:
    def test_verified_member_joins_public_group_active(self, db, group):
        member = add_member(db)
        result = join(db, group, member)
        assert result["status"] == "active"
        assert result["role"] == "member"

    def test_ghost_join_is_refused(self, db, group):
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            join(db, group, ghost)

    def test_under_band_join_is_invisible_not_refused(self, db, host):
        adults = make_group(db, host, min_age_band="age_21_plus", slug="adults")
        young = add_member(db, band="age_18_20")
        with pytest.raises(service.NotFoundError):
            join(db, adults, young)

    def test_private_group_join_waits_for_decision(self, db, host):
        private = make_group(db, host, visibility="private", slug="priv")
        member = add_member(db)
        result = join(db, private, member)
        assert result["status"] == "invited"
        decided = service.decide_membership(
            db,
            host,
            private["id"],
            member,
            models.MembershipDecision(decision="approve"),
            now=NOW,
        )
        assert decided["status"] == "active"

    def test_host_approved_group_join_waits_for_decision(self, db, host):
        gated = make_group(db, host, meetup_min_tier="host_approved", slug="gated")
        member = add_member(db)
        assert join(db, gated, member)["status"] == "invited"

    def test_membership_decision_requires_owner_or_admin(self, db, host):
        private = make_group(db, host, visibility="private", slug="priv2")
        member = add_member(db)
        join(db, private, member)
        outsider = add_member(db)
        with pytest.raises(service.ForbiddenError):
            service.decide_membership(
                db,
                outsider,
                private["id"],
                member,
                models.MembershipDecision(decision="approve"),
                now=NOW,
            )

    def test_declined_join_request_leaves_no_membership(self, db, host):
        private = make_group(db, host, visibility="private", slug="priv3")
        member = add_member(db)
        join(db, private, member)
        service.decide_membership(
            db,
            host,
            private["id"],
            member,
            models.MembershipDecision(decision="decline"),
            now=NOW,
        )
        rows = [
            r for r in db.rows("rsn_group_memberships") if r["user_id"] == member
        ]
        assert rows == []

    def test_join_is_idempotent_for_active_member(self, db, group):
        member = add_member(db)
        first = join(db, group, member)
        second = join(db, group, member)
        assert first["id"] == second["id"]

    def test_removed_member_cannot_rejoin(self, db, host, group):
        member = add_member(db)
        join(db, group, member)
        row = [
            r for r in db.rows("rsn_group_memberships") if r["user_id"] == member
        ][0]
        row["status"] = "removed"
        with pytest.raises(service.ForbiddenError):
            join(db, group, member)


class TestGroupRoles:
    def test_owner_promotes_member_to_admin(self, db, host, group):
        member = add_member(db)
        join(db, group, member)
        updated = service.set_member_role(
            db,
            host,
            group["id"],
            member,
            models.MemberRoleUpdate(role="admin"),
            now=NOW,
        )
        assert updated["role"] == "admin"

    def test_non_owner_cannot_set_roles(self, db, host, group):
        member = add_member(db)
        other = add_member(db)
        join(db, group, member)
        join(db, group, other)
        with pytest.raises(service.ForbiddenError):
            service.set_member_role(
                db,
                other,
                group["id"],
                member,
                models.MemberRoleUpdate(role="admin"),
                now=NOW,
            )

    def test_owner_role_cannot_be_reassigned(self, db, host, group):
        with pytest.raises(service.ConflictError):
            service.set_member_role(
                db,
                host,
                group["id"],
                host,
                models.MemberRoleUpdate(role="member"),
                now=NOW,
            )


# ---------------------------------------------------------------------------
# Directory + group detail: Section 5.9 invisibility, ghost skeleton
# ---------------------------------------------------------------------------


class TestDirectoryVisibility:
    def test_under_band_groups_are_absent_and_uncounted(self, db, host):
        make_group(db, host, min_age_band="age_21_plus", slug="g21")
        make_group(db, host, min_age_band="age_18_plus", slug="g18")
        young = add_member(db, band="age_18_20")
        cards = service.list_groups(db, young, now=NOW)
        slugs = {c["slug"] for c in cards}
        assert slugs == {"g18"}

    def test_hidden_group_absent_for_non_member_present_for_member(
        self, db, host, group
    ):
        hidden = make_group(db, host, visibility="hidden", slug="hid")
        member = add_member(db)
        service.join_group(db, member, hidden["id"], now=NOW)
        outsider = add_member(db)
        outsider_slugs = {c["slug"] for c in service.list_groups(db, outsider, now=NOW)}
        member_slugs = {c["slug"] for c in service.list_groups(db, member, now=NOW)}
        assert "hid" not in outsider_slugs
        assert "hid" in member_slugs

    def test_ghost_directory_is_preview_skeleton_only(self, db, host, group):
        # The skeleton serves public age_16_plus groups only (5.11):
        # the 18+ fixture group and the 21+ group are both absent.
        all_ages = make_group(db, host, min_age_band="age_16_plus", slug="allages")
        make_group(db, host, min_age_band="age_21_plus", slug="g21b")
        ghost = add_member(db, tier=None, trust_row=False)
        cards = service.list_groups(db, ghost, now=NOW)
        assert {c["slug"] for c in cards} == {all_ages["slug"]}
        card = cards[0]
        assert "description" not in card
        assert "created_by" not in card
        assert isinstance(card["member_count_band"], str)
        assert card["host_verified"] is True

    def test_blocked_creator_group_is_invisible(self, db, host, group):
        viewer = add_member(db)
        db.seed("rsn_blocks", {"blocker_id": host, "blocked_id": viewer})
        assert service.list_groups(db, viewer, now=NOW) == []
        with pytest.raises(service.NotFoundError):
            service.get_group(db, viewer, group["id"], now=NOW)


class TestGetGroup:
    def test_member_gets_full_group(self, db, host, group):
        member = add_member(db)
        join(db, group, member)
        detail = service.get_group(db, member, group["id"], now=NOW)
        assert detail["description"] == "Weekly hikes around the metro."
        assert detail["member_count"] == 2
        assert detail["viewer_role"] == "member"

    def test_under_band_get_group_matches_nonexistent(self, db, host):
        adults = make_group(db, host, min_age_band="age_21_plus", slug="ad2")
        young = add_member(db, band="age_18_20")
        with pytest.raises(service.NotFoundError):
            service.get_group(db, young, adults["id"], now=NOW)
        with pytest.raises(service.NotFoundError):
            service.get_group(db, young, str(uuid4()), now=NOW)

    def test_private_group_detail_is_members_only(self, db, host):
        private = make_group(db, host, visibility="private", slug="privd")
        outsider = add_member(db)
        with pytest.raises(service.NotFoundError):
            service.get_group(db, outsider, private["id"], now=NOW)

    def test_ghost_gets_landing_shell_without_host_identity(self, db, host):
        all_ages = make_group(db, host, min_age_band="age_16_plus", slug="shells")
        event = make_event(db, host, all_ages, min_age_band="age_16_plus")
        ghost = add_member(db, tier=None, trust_row=False)
        shell = service.get_group(db, ghost, all_ages["id"], now=NOW)
        assert shell["shell"] == "preview"
        assert "created_by" not in shell
        assert shell["next_gathering"]["starts_at"] is not None
        assert shell["next_gathering"]["title"] == event["title"]

    def test_ghost_shell_refuses_above_lowest_band(self, db, host, group):
        # The 18+ fixture group has no ghost shell at all (5.11).
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.NotFoundError):
            service.get_group(db, ghost, group["id"], now=NOW)


# ---------------------------------------------------------------------------
# Events: creation gates + payload hygiene
# ---------------------------------------------------------------------------


class TestEventCreation:
    def test_member_creates_event_in_group(self, db, host, group):
        member = add_member(db)
        join(db, group, member)
        event = make_event(db, member, group)
        assert event["host_user_id"] == member
        assert event["status"] == "published"
        assert event["series_id"] is None

    def test_ghost_cannot_create_event(self, db, group):
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            make_event(db, ghost, group)

    def test_non_member_cannot_create_event(self, db, group):
        outsider = add_member(db)
        with pytest.raises(service.ForbiddenError):
            make_event(db, outsider, group)

    def test_capacity_above_standing_cap_is_refused(self, db, host, group):
        # Host is Regular standing: below Fixture, the cap is 12 (7.7).
        with pytest.raises(service.ForbiddenError):
            make_event(db, host, group, capacity=13)
        ok = make_event(db, host, group, capacity=12)
        assert ok["capacity"] == 12

    def test_fixture_host_cap_is_25(self, db):
        fixture_host = add_member(db, standing="fixture")
        grp = make_group(db, fixture_host)
        ok = make_event(db, fixture_host, grp, capacity=25)
        assert ok["capacity"] == 25
        with pytest.raises(service.ForbiddenError):
            make_event(db, fixture_host, grp, capacity=26)

    def test_omitted_capacity_defaults_to_standing_cap(self, db, host, group):
        event = make_event(db, host, group, capacity=None)
        assert event["capacity"] == 12

    def test_watch_band_member_cannot_host(self, db, group):
        watcher = add_member(db, trust_band="watch")
        join(db, group, watcher)
        with pytest.raises(service.ForbiddenError):
            make_event(db, watcher, group)

    def test_host_cannot_rate_event_above_own_band(self, db, group):
        young_host = add_member(db, band="age_18_20")
        join(db, group, young_host)
        with pytest.raises(service.ForbiddenError):
            make_event(db, young_host, group, min_age_band="age_21_plus")


class TestEventPayloadHygiene:
    def test_detail_never_carries_exact_location(self, db, host, group):
        event = make_event(db, host, group)
        set_location(db, host, event)
        member = add_member(db)
        join(db, group, member)
        detail = service.get_event(db, member, event["id"], now=NOW)

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in {
                        "street_address",
                        "latitude",
                        "longitude",
                        "arrival_instructions",
                    }, f"exact-location field {key} leaked into event detail"
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(detail)
        # A public venue's name is a public fact; the address is not.
        assert detail["venue_name"] == "Trailhead"
        assert detail["coarse_area"] == "denver"

    def test_home_event_detail_hides_venue_name_too(self, db, host, group):
        home = make_event(db, host, group, venue_type="home")
        set_location(db, host, home, address="742 Evergreen Terrace, Denver, CO")
        friend = add_member(db)
        db.seed(
            "rsn_close_friends", {"owner_id": host, "friend_id": friend}
        )
        join(db, group, friend)
        detail = service.get_event(db, friend, home["id"], now=NOW)
        assert "venue_name" not in detail
        assert "Evergreen" not in str(detail)

    def test_event_invisible_to_under_band_viewer(self, db, host, group):
        event = make_event(db, host, group, min_age_band="age_21_plus")
        young = add_member(db, band="age_18_20")
        join(db, group, young)
        with pytest.raises(service.NotFoundError):
            service.get_event(db, young, event["id"], now=NOW)

    def test_event_rating_inherits_stricter_group_rating(self, db, host):
        adults = make_group(db, host, min_age_band="age_21_plus", slug="ad3")
        event = make_event(db, host, adults, min_age_band="age_16_plus")
        young = add_member(db, band="age_16_17")
        # The teen cannot join the 21+ group, so the event cannot exist
        # for them even though the event's own rating is 16+.
        with pytest.raises(service.NotFoundError):
            service.get_event(db, young, event["id"], now=NOW)

    def test_only_host_sets_location(self, db, host, group):
        event = make_event(db, host, group)
        member = add_member(db)
        join(db, group, member)
        with pytest.raises(service.ForbiddenError):
            set_location(db, member, event)


# ---------------------------------------------------------------------------
# Series: first-class recurring objects
# ---------------------------------------------------------------------------


def make_series(db, host, group, **overrides):
    payload = dict(
        group_id=group["id"],
        title="Tuesday League Night",
        description_template="Weekly league.",
        recurrence_rule="DTSTART:20261027T190000Z\nRRULE:FREQ=WEEKLY",
        first_starts_at=NOW + timedelta(days=6),
        default_capacity=10,
        default_min_age_band="age_18_plus",
    )
    payload.update(overrides)
    return service.create_series(db, host, models.SeriesCreate(**payload), now=NOW)


class TestSeries:
    def test_series_creation_requires_fixture_standing(self, db, host, group):
        # Host fixture is Regular: series creation is Fixture+ (7.7).
        with pytest.raises(service.ForbiddenError):
            make_series(db, host, group)

    def test_fixture_host_creates_series_and_instances(self, db):
        fixture_host = add_member(db, standing="fixture")
        grp = make_group(db, fixture_host)
        series = make_series(db, fixture_host, grp)
        assert series["status"] == "active"
        instances = [
            r for r in db.rows("rsn_events") if r.get("series_id") == series["id"]
        ]
        # Weekly from Oct 27 inside a 4-week horizon: 4 instances.
        assert len(instances) == 4
        assert all(i["status"] == "published" for i in instances)
        assert all(i["min_age_band"] == "age_18_plus" for i in instances)
        assert all(i["capacity"] == 10 for i in instances)

    def test_materialize_is_idempotent(self, db):
        fixture_host = add_member(db, standing="fixture")
        grp = make_group(db, fixture_host)
        series = make_series(db, fixture_host, grp)
        again = service.materialize_series(db, series["id"], now=NOW)
        assert again["created"] == 0
        instances = [
            r for r in db.rows("rsn_events") if r.get("series_id") == series["id"]
        ]
        assert len(instances) == 4

    def test_series_instances_carry_their_own_locations(self, db):
        fixture_host = add_member(db, standing="fixture")
        grp = make_group(db, fixture_host)
        series = make_series(db, fixture_host, grp)
        service.set_series_location(
            db,
            fixture_host,
            series["id"],
            models.EventLocationIn(
                venue_name="League Hall",
                street_address="55 League Way, Denver, CO",
                latitude=39.75,
                longitude=-104.99,
                arrival_instructions=None,
            ),
            now=NOW,
        )
        created = service.materialize_series(
            db, series["id"], now=NOW + timedelta(days=29)
        )
        assert created["created"] >= 1
        located = [
            r
            for r in db.rows("rsn_event_locations")
            if r.get("event_id") is not None
        ]
        assert located and all(
            r["street_address"] == "55 League Way, Denver, CO" for r in located
        )

    def test_series_detail_invisible_to_under_band_viewer(self, db):
        fixture_host = add_member(db, standing="fixture")
        grp = make_group(db, fixture_host, min_age_band="age_21_plus")
        series = make_series(
            db, fixture_host, grp, default_min_age_band="age_21_plus"
        )
        young = add_member(db, band="age_18_20")
        with pytest.raises(service.NotFoundError):
            service.get_series(db, young, series["id"], now=NOW)


# ---------------------------------------------------------------------------
# RSVP: per-instance state, capacity, host approval, promotion
# ---------------------------------------------------------------------------


class TestRsvp:
    def test_venue_rsvp_goes_straight_to_going(self, db, host, group):
        event = make_event(db, host, group)
        member = add_member(db)
        join(db, group, member)
        result = rsvp(db, member, event)
        assert result["status"] == "going"
        assert result["rsvp_window"] == "general"

    def test_ghost_cannot_rsvp(self, db, host, group):
        event = make_event(db, host, group)
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            rsvp(db, ghost, event)

    def test_under_band_rsvp_is_invisible(self, db, host, group):
        event = make_event(db, host, group, min_age_band="age_21_plus")
        young = add_member(db, band="age_18_20")
        join(db, group, young)
        with pytest.raises(service.NotFoundError):
            rsvp(db, young, event)

    def test_capacity_overflow_waitlists(self, db, host, group):
        event = make_event(db, host, group, capacity=1)
        first = add_member(db)
        second = add_member(db)
        join(db, group, first)
        join(db, group, second)
        assert rsvp(db, first, event)["status"] == "going"
        assert rsvp(db, second, event)["status"] == "waitlist"

    def test_duplicate_rsvp_returns_existing_row(self, db, host, group):
        event = make_event(db, host, group)
        member = add_member(db)
        join(db, group, member)
        assert rsvp(db, member, event)["id"] == rsvp(db, member, event)["id"]

    def test_rsvp_to_cancelled_event_conflicts(self, db, host, group):
        event = make_event(db, host, group)
        row = [r for r in db.rows("rsn_events") if r["id"] == event["id"]][0]
        row["status"] = "cancelled"
        member = add_member(db)
        join(db, group, member)
        with pytest.raises(service.ConflictError):
            rsvp(db, member, event)

    def test_restricted_band_cannot_rsvp(self, db, host, group):
        event = make_event(db, host, group)
        restricted = add_member(db, trust_band="restricted")
        join(db, group, restricted)
        with pytest.raises(service.ForbiddenError):
            rsvp(db, restricted, event)

    def test_blocked_member_rsvp_is_invisible(self, db, host, group):
        event = make_event(db, host, group)
        member = add_member(db)
        join(db, group, member)
        db.seed("rsn_blocks", {"blocker_id": host, "blocked_id": member})
        with pytest.raises(service.NotFoundError):
            rsvp(db, member, event)


class TestHomeRsvpApproval:
    @pytest.fixture()
    def home_event(self, db, host, group):
        return make_event(db, host, group, venue_type="home")

    def test_home_rsvp_enters_requested(self, db, group, home_event):
        member = add_member(db)
        join(db, group, member)
        assert rsvp(db, member, home_event)["status"] == "requested"

    def test_close_friend_is_auto_confirmed(self, db, host, group, home_event):
        friend = add_member(db)
        db.seed("rsn_close_friends", {"owner_id": host, "friend_id": friend})
        join(db, group, friend)
        assert rsvp(db, friend, home_event)["status"] == "going"

    def test_host_approves_requested_rsvp(self, db, host, group, home_event):
        member = add_member(db)
        join(db, group, member)
        pending = rsvp(db, member, home_event)
        decided = service.decide_rsvp(
            db,
            host,
            pending["id"],
            models.RsvpDecision(decision="approve"),
            now=NOW,
        )
        assert decided["status"] == "going"
        assert decided["host_decided_by"] == host

    def test_host_declines_requested_rsvp(self, db, host, group, home_event):
        member = add_member(db)
        join(db, group, member)
        pending = rsvp(db, member, home_event)
        decided = service.decide_rsvp(
            db,
            host,
            pending["id"],
            models.RsvpDecision(decision="decline"),
            now=NOW,
        )
        assert decided["status"] == "declined"

    def test_non_host_cannot_decide(self, db, host, group, home_event):
        member = add_member(db)
        other = add_member(db)
        join(db, group, member)
        join(db, group, other)
        pending = rsvp(db, member, home_event)
        with pytest.raises(service.ForbiddenError):
            service.decide_rsvp(
                db,
                other,
                pending["id"],
                models.RsvpDecision(decision="approve"),
                now=NOW,
            )

    def test_approval_into_full_event_waitlists(self, db, host, group):
        home = make_event(db, host, group, venue_type="home", capacity=1)
        friend = add_member(db)
        db.seed("rsn_close_friends", {"owner_id": host, "friend_id": friend})
        join(db, group, friend)
        assert rsvp(db, friend, home)["status"] == "going"
        member = add_member(db)
        join(db, group, member)
        pending = rsvp(db, member, home)
        decided = service.decide_rsvp(
            db,
            host,
            pending["id"],
            models.RsvpDecision(decision="approve"),
            now=NOW,
        )
        assert decided["status"] == "waitlist"
        assert decided["host_decided_by"] == host

    def test_host_approved_tier_event_also_requests(self, db, host, group):
        gated = make_event(db, host, group, min_tier_rsvp="host_approved")
        member = add_member(db)
        join(db, group, member)
        assert rsvp(db, member, gated)["status"] == "requested"


class TestWaitlistPromotion:
    def test_withdraw_promotes_in_band_order_not_arrival_order(
        self, db, host, group
    ):
        event = make_event(db, host, group, capacity=1)
        holder = add_member(db)
        early_watch = add_member(db, trust_band="watch")
        later_trusted = add_member(db, trust_band="trusted")
        for member in (holder, early_watch, later_trusted):
            join(db, group, member)
        rsvp(db, holder, event)
        assert rsvp(db, early_watch, event)["status"] == "waitlist"
        assert rsvp(db, later_trusted, event)["status"] == "waitlist"
        holder_row = [
            r for r in db.rows("rsn_rsvps") if r["user_id"] == holder
        ][0]
        service.withdraw_rsvp(db, holder, holder_row["id"], now=NOW)
        promoted = [
            r for r in db.rows("rsn_rsvps") if r["user_id"] == later_trusted
        ][0]
        still_waiting = [
            r for r in db.rows("rsn_rsvps") if r["user_id"] == early_watch
        ][0]
        assert promoted["status"] == "going"
        assert promoted["rsvp_window"] == "waitlist_promotion"
        assert still_waiting["status"] == "waitlist"


class TestCoupleRsvp:
    def _link(self, db, a, b):
        low, high = sorted([a, b])
        return db.seed(
            "rsn_couple_links",
            {
                "user_low_id": low,
                "user_high_id": high,
                "status": "active",
                "initiated_by": a,
            },
        )

    def test_couple_rsvp_creates_two_first_class_rows(self, db, host, group):
        event = make_event(db, host, group)
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        link = self._link(db, a, b)
        result = rsvp(db, a, event, couple_link_id=link["id"])
        rows = [r for r in db.rows("rsn_rsvps") if r["event_id"] == event["id"]]
        assert {r["user_id"] for r in rows} == {a, b}
        assert all(r["couple_link_id"] == link["id"] for r in rows)
        assert result["user_id"] == a

    def test_couple_rsvp_refused_atomically_when_partner_under_band(
        self, db, host, group
    ):
        event = make_event(db, host, group, min_age_band="age_21_plus")
        a = add_member(db, band="age_21_plus")
        b = add_member(db, band="age_18_20")
        join(db, group, a)
        # b cannot join the group's events (band), link them directly.
        link = self._link(db, a, b)
        with pytest.raises(service.NotFoundError):
            rsvp(db, a, event, couple_link_id=link["id"])
        assert db.rows("rsn_rsvps") == []


# ---------------------------------------------------------------------------
# Location reveal: windows, eligible sets, logging
# ---------------------------------------------------------------------------


class TestReveal:
    @pytest.fixture()
    def venue_event(self, db, host, group):
        event = make_event(
            db, host, group, starts_at=NOW + timedelta(days=2)
        )
        set_location(db, host, event)
        return event

    def _going_member(self, db, group, event):
        member = add_member(db)
        join(db, group, member)
        rsvp(db, member, event)
        return member

    def test_reveal_inside_48h_window_logs_and_returns(self, db, host, group, venue_event):
        member = self._going_member(db, group, venue_event)
        out = service.reveal_event_location(
            db, member, venue_event["id"], now=NOW + timedelta(hours=1)
        )
        assert out["street_address"] == "1234 Trailhead Rd, Denver, CO"
        reveals = db.rows("rsn_location_reveals")
        assert len(reveals) == 1
        assert reveals[0]["user_id"] == member
        assert reveals[0]["event_id"] == venue_event["id"]

    def test_reveal_before_window_is_refused_and_unlogged(
        self, db, host, group, venue_event
    ):
        member = self._going_member(db, group, venue_event)
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, member, venue_event["id"], now=NOW - timedelta(hours=2)
            )
        assert db.rows("rsn_location_reveals") == []

    def test_reveal_after_event_end_is_refused(self, db, host, group, venue_event):
        member = self._going_member(db, group, venue_event)
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, member, venue_event["id"], now=NOW + timedelta(days=3)
            )

    def test_reveal_requires_going_rsvp(self, db, host, group, venue_event):
        member = add_member(db)
        join(db, group, member)
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, member, venue_event["id"], now=NOW + timedelta(hours=1)
            )

    def test_reveal_requires_group_membership(self, db, host, group, venue_event):
        outsider = add_member(db)
        with pytest.raises(service.NotFoundError):
            service.reveal_event_location(
                db, outsider, venue_event["id"], now=NOW + timedelta(hours=1)
            )

    def test_restricted_band_loses_reveal(self, db, host, group, venue_event):
        restricted = add_member(db, trust_band="restricted")
        join(db, group, restricted)
        # Restricted members cannot RSVP, so seed the going row the way
        # a band change after RSVP would leave it: the gate re-evaluates
        # at action time (Section 5.9).
        db.seed(
            "rsn_rsvps",
            {
                "event_id": venue_event["id"],
                "user_id": restricted,
                "status": "going",
                "rsvp_window": "general",
                "created_at": NOW,
            },
        )
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, restricted, venue_event["id"], now=NOW + timedelta(hours=1)
            )

    def test_host_approved_view_tier_needs_a_host_decision(
        self, db, host, group
    ):
        event = make_event(
            db,
            host,
            group,
            starts_at=NOW + timedelta(days=2),
            min_tier_view="host_approved",
        )
        set_location(db, host, event)
        member = add_member(db)
        join(db, group, member)
        rsvp(db, member, event)  # direct going, never host-decided
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, member, event["id"], now=NOW + timedelta(hours=1)
            )


class TestHomeReveal:
    @pytest.fixture()
    def home_event(self, db, host, group):
        event = make_event(
            db,
            host,
            group,
            venue_type="home",
            starts_at=NOW + timedelta(hours=5),
        )
        set_location(db, host, event, address="742 Evergreen Terrace, Denver, CO")
        return event

    def test_close_friend_reveals_inside_3h_window(self, db, host, group, home_event):
        friend = add_member(db)
        db.seed("rsn_close_friends", {"owner_id": host, "friend_id": friend})
        join(db, group, friend)
        rsvp(db, friend, home_event)
        out = service.reveal_event_location(
            db, friend, home_event["id"], now=NOW + timedelta(hours=3)
        )
        assert out["street_address"] == "742 Evergreen Terrace, Denver, CO"

    def test_close_friend_too_early_at_4h_out(self, db, host, group, home_event):
        friend = add_member(db)
        db.seed("rsn_close_friends", {"owner_id": host, "friend_id": friend})
        join(db, group, friend)
        rsvp(db, friend, home_event)
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, friend, home_event["id"], now=NOW + timedelta(minutes=30)
            )

    def test_approved_guest_reveals_inside_window(self, db, host, group, home_event):
        member = add_member(db)
        join(db, group, member)
        pending = rsvp(db, member, home_event)
        service.decide_rsvp(
            db,
            host,
            pending["id"],
            models.RsvpDecision(decision="approve"),
            now=NOW,
        )
        out = service.reveal_event_location(
            db, member, home_event["id"], now=NOW + timedelta(hours=4)
        )
        assert out["street_address"] == "742 Evergreen Terrace, Denver, CO"

    def test_unapproved_member_never_reveals_home_address(
        self, db, host, group, home_event
    ):
        member = add_member(db)
        join(db, group, member)
        rsvp(db, member, home_event)  # stays requested
        with pytest.raises(service.ForbiddenError):
            service.reveal_event_location(
                db, member, home_event["id"], now=NOW + timedelta(hours=4)
            )
        assert db.rows("rsn_location_reveals") == []


# ---------------------------------------------------------------------------
# Gold listings on the member surface: read-only, band-filtered
# ---------------------------------------------------------------------------


def seed_listing(db, *, band="age_18_plus", title="Library Story Time"):
    return db.seed(
        "evt_gold_listings",
        {
            "listing_id": str(uuid4()),
            "title": title,
            "starts_at": NOW + timedelta(days=1),
            "ends_at": NOW + timedelta(days=1, hours=1),
            "venue_name": "Denver Public Library",
            "street_address": "10 W 14th Ave, Denver, CO",
            "city": "Denver",
            "neighborhood": "Golden Triangle",
            "latitude": 39.7378,
            "longitude": -104.9876,
            "category": "library",
            "min_age_band": band,
            "attribution_text": "Listed from the Denver Public Library calendar",
            "primary_source_url": "https://www.denverlibrary.org/event/example",
            "last_seen_at": NOW,
            "published_at": NOW,
        },
    )


class TestListings:
    def test_member_sees_rated_listings_at_or_below_band(self, db):
        seed_listing(db, band="age_16_plus", title="All Ages")
        seed_listing(db, band="age_18_plus", title="Adults")
        seed_listing(db, band="age_21_plus", title="Bar Night")
        seed_listing(db, band=None, title="Unrated")
        member = add_member(db, band="age_18_20")
        titles = {c["title"] for c in service.list_listings(db, member, now=NOW)}
        assert titles == {"All Ages", "Adults"}

    def test_top_band_sees_unrated_listings(self, db):
        seed_listing(db, band=None, title="Unrated")
        member = add_member(db, band="age_21_plus")
        titles = {c["title"] for c in service.list_listings(db, member, now=NOW)}
        assert titles == {"Unrated"}

    def test_ghost_sees_lowest_band_only(self, db):
        seed_listing(db, band="age_16_plus", title="All Ages")
        seed_listing(db, band="age_18_plus", title="Adults")
        seed_listing(db, band=None, title="Unrated")
        ghost = add_member(db, tier=None, trust_row=False)
        titles = {c["title"] for c in service.list_listings(db, ghost, now=NOW)}
        assert titles == {"All Ages"}

    def test_listing_detail_fails_closed_for_under_band(self, db):
        row = seed_listing(db, band="age_21_plus")
        young = add_member(db, band="age_18_20")
        with pytest.raises(service.NotFoundError):
            service.get_listing(db, young, row["listing_id"], now=NOW)

    def test_listing_payload_is_facts_without_exact_address(self, db):
        row = seed_listing(db)
        member = add_member(db)
        card = service.get_listing(db, member, row["listing_id"], now=NOW)
        assert "street_address" not in card
        assert "latitude" not in card
        assert "longitude" not in card
        assert card["attribution_text"].startswith("Listed from")
        assert card["primary_source_url"].startswith("https://")
        assert card["min_age_band"] == "age_18_plus"


# ---------------------------------------------------------------------------
# HTTP surface: the same rules at the router (status codes + shells)
# ---------------------------------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from app.database import get_db  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.social import router as social_router  # noqa: E402


def _http_client(db: FakeDb, user_id: str) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(social_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[social_router._verified_claims] = lambda: {
        "sub": user_id
    }
    return TestClient(app, raise_server_exceptions=False)


class TestRouterCoreLoop:
    def test_new_routes_registered(self):
        found = {
            (m, r.path)
            for r in social_router.router.routes
            if getattr(r, "methods", None)
            for m in r.methods
        }
        expected = {
            ("GET", "/social/groups"),
            ("POST", "/social/groups/{group_id}/join"),
            ("POST", "/social/groups/{group_id}/memberships/{user_id}/decision"),
            ("POST", "/social/groups/{group_id}/memberships/{user_id}/role"),
            ("POST", "/social/series"),
            ("GET", "/social/series/{series_id}"),
            ("POST", "/social/series/{series_id}/location"),
            ("POST", "/social/events/{event_id}/location"),
            ("POST", "/social/rsvps/{rsvp_id}/withdraw"),
            ("GET", "/social/listings"),
            ("GET", "/social/listings/{listing_id}"),
        }
        assert expected - found == set()

    def test_ghost_write_is_403(self, db):
        ghost = add_member(db, tier=None, trust_row=False)
        client = _http_client(db, ghost)
        resp = client.post(
            "/api/v1/social/groups",
            json={
                "name": "Ghost Group",
                "slug": "ghost-group",
                "category": "outdoors",
                "metro": "denver",
                "min_age_band": "age_18_plus",
            },
        )
        assert resp.status_code == 403

    def test_under_band_shared_link_matches_nonexistent_shape(self, db, host, group):
        event = make_event(db, host, group, min_age_band="age_21_plus")
        young = add_member(db, band="age_18_20")
        join(db, group, young)
        client = _http_client(db, young)
        gated = client.get(f"/api/v1/social/events/{event['id']}")
        missing = client.get(f"/api/v1/social/events/{uuid4()}")
        assert gated.status_code == 404
        assert missing.status_code == 404
        assert gated.json() == missing.json()
        assert event["title"] not in gated.text

    def test_reveal_over_http_logs_and_returns(self, db, host, group, monkeypatch):
        # The router calls the service on the real clock; pin the
        # service clock inside the 48h venue window for this test.
        monkeypatch.setattr(
            service, "_utcnow", lambda: NOW + timedelta(hours=1)
        )
        event = make_event(db, host, group, starts_at=NOW + timedelta(days=2))
        set_location(db, host, event)
        member = add_member(db)
        join(db, group, member)
        rsvp(db, member, event)
        client = _http_client(db, member)
        resp = client.post(f"/api/v1/social/events/{event['id']}/reveal")
        assert resp.status_code == 200
        assert resp.json()["street_address"] == "1234 Trailhead Rd, Denver, CO"
        assert len(db.rows("rsn_location_reveals")) == 1

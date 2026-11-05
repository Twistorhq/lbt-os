"""TW-941 Twistor Social feed backend (Phase 4 slice): TDD.

The feed over rsn_posts / rsn_comments / rsn_reactions (TW-650
schema, tables only until this ticket):

- posts are member voice: the ghost town (spec Section 5.11) has
  no feed surface at all, on reads as well as writes;
- a post carries no rating of its own: visibility inherits its
  container's rating (Section 5.9 fail-closed), and audience scope
  (group / connections / close_friends) composes with the band
  filter, never substitutes for it; blocked members are mutually
  invisible; a metro post with no container fails closed on the
  author's own band, and the teen wall holds in both directions;
- celebrations are system-generated: the member API refuses them,
  and the system path honors the member's standing-display toggle;
- comments: held/removed rows are invisible to everyone but their
  author, who sees a tombstone, never the removed body;
- reports filed from a feed item land in rsn_reports (the safety
  queue the Phase 6 desk consumes), with the subject resolved
  from the reported item, never trusted from the request body.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from test_social_core_loop import NOW, FakeDb, add_member, join, make_group

from app.social import models, service


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    return make_group(db, host)


@pytest.fixture()
def member(db, group, host):
    uid = add_member(db)
    join(db, group, uid)
    return uid


def make_post(db, author, group, **kwargs):
    payload = models.PostCreate(
        group_id=group["id"] if group else None,
        body=kwargs.pop("body", "Trail day moved to Sunday, same trailhead."),
        **kwargs,
    )
    return service.create_post(db, author, payload, now=NOW)


# ---------------------------------------------------------------------------
# Creation + voice (ghost town)
# ---------------------------------------------------------------------------


class TestCreatePost:
    def test_member_posts_to_their_group(self, db, host, group):
        out = make_post(db, host, group)
        assert out["author_id"] == host
        assert out["group_id"] == group["id"]
        assert out["post_type"] == "update"
        assert out["reaction_counts"] == {"like": 0, "congrats": 0, "celebrate": 0}
        assert out["comment_count"] == 0
        rows = db.rows("rsn_posts")
        assert len(rows) == 1 and rows[0]["author_id"] == host

    def test_ghost_account_cannot_post(self, db, group):
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            make_post(db, ghost, group)
        assert db.rows("rsn_posts") == []

    def test_t1_account_cannot_post(self, db, group):
        t1 = add_member(db, tier=1)
        with pytest.raises(service.ForbiddenError):
            make_post(db, t1, group)

    def test_non_member_cannot_post_to_group(self, db, group):
        outsider = add_member(db)
        with pytest.raises(service.ForbiddenError):
            make_post(db, outsider, group)

    def test_post_to_missing_group_is_not_found(self, db, host):
        with pytest.raises(service.NotFoundError):
            service.create_post(
                db,
                host,
                models.PostCreate(group_id=uuid4(), body="hello room"),
                now=NOW,
            )

    def test_member_cannot_forge_a_celebration(self, db, host, group):
        with pytest.raises(service.ForbiddenError):
            make_post(db, host, group, post_type="celebration")
        assert db.rows("rsn_posts") == []

    def test_metro_post_needs_no_group(self, db, host):
        out = service.create_post(
            db,
            host,
            models.PostCreate(body="Denver, we are outside.", visibility="metro"),
            now=NOW,
        )
        assert out["group_id"] is None
        assert out["visibility"] == "metro"


# ---------------------------------------------------------------------------
# Feed reads: band inheritance, audience scope, blocks, ghost town
# ---------------------------------------------------------------------------


class TestFeedVisibility:
    def test_group_member_reads_group_feed(self, db, host, group, member):
        make_post(db, host, group)
        feed = service.list_feed(db, member, now=NOW)
        assert len(feed) == 1
        assert feed[0]["author_id"] == host

    def test_ghost_account_has_no_feed_surface(self, db, host, group):
        make_post(db, host, group)
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            service.list_feed(db, ghost, now=NOW)
        with pytest.raises(service.NotFoundError):
            service.get_post(db, ghost, db.rows("rsn_posts")[0]["id"])

    def test_non_member_cannot_read_group_scoped_post(self, db, host, group):
        post = make_post(db, host, group)
        outsider = add_member(db)
        assert service.list_feed(db, outsider, now=NOW) == []
        with pytest.raises(service.NotFoundError):
            service.get_post(db, outsider, post["id"])

    def test_under_band_member_cannot_see_post_inheriting_group_rating(
        self, db, host
    ):
        adults = make_group(db, host, min_age_band="age_21_plus", slug="adults-21")
        post = make_post(db, host, adults)
        younger = add_member(db, band="age_18_20")
        # Seeded membership: even holding a row, the band wall wins
        # (fail-closed beats the membership table).
        db.seed(
            "rsn_group_memberships",
            {"group_id": adults["id"], "user_id": younger, "role": "member",
             "status": "active"},
        )
        assert service.list_feed(db, younger, now=NOW) == []
        with pytest.raises(service.NotFoundError):
            service.get_post(db, younger, post["id"])

    def test_blocked_member_is_invisible_both_ways(self, db, host, group, member):
        post = make_post(db, member, group)
        db.seed("rsn_blocks", {"blocker_id": host, "blocked_id": member})
        assert service.list_feed(db, host, now=NOW) == []
        with pytest.raises(service.NotFoundError):
            service.get_post(db, host, post["id"])
        # The author still sees their own post.
        assert len(service.list_feed(db, member, now=NOW)) == 1

    def test_close_friends_scope_reaches_only_close_friends(
        self, db, host, group, member
    ):
        friend = add_member(db)
        join(db, group, friend)
        db.seed(
            "rsn_close_friends", {"owner_id": host, "friend_id": friend}
        )
        post = make_post(db, host, group, audience_scope="close_friends")
        assert service.list_feed(db, member, now=NOW) == []
        feed = service.list_feed(db, friend, now=NOW)
        assert [p["id"] for p in feed] == [post["id"]]

    def test_metro_post_fails_closed_on_author_band(self, db, host):
        service.create_post(
            db,
            host,
            models.PostCreate(body="21 plus night market.", visibility="metro"),
            now=NOW,
        )
        younger = add_member(db, band="age_18_20")
        assert service.list_feed(db, younger, now=NOW) == []
        adult = add_member(db)
        assert len(service.list_feed(db, adult, now=NOW)) == 1

    def test_feed_is_chronological_newest_first(self, db, host, group, member):
        first = make_post(db, host, group, body="first")
        later = NOW + timedelta(hours=1)
        second = service.create_post(
            db,
            member,
            models.PostCreate(group_id=group["id"], body="second"),
            now=later,
        )
        feed = service.list_feed(db, member, now=NOW)
        assert [p["id"] for p in feed] == [second["id"], first["id"]]

    def test_group_filter_scopes_the_feed(self, db, host, group, member):
        other_group = make_group(db, host, slug="other-room")
        join(db, other_group, member)
        make_post(db, host, group, body="in hikers")
        make_post(db, host, other_group, body="in other")
        feed = service.list_feed(db, member, group_id=group["id"], now=NOW)
        assert len(feed) == 1 and feed[0]["body"] == "in hikers"


# ---------------------------------------------------------------------------
# Celebrations: system-generated only
# ---------------------------------------------------------------------------


class TestCelebrations:
    def test_system_celebration_posts_to_group(self, db, host, group, member):
        out = service.create_celebration_post(
            db,
            member,
            "Fixture status: the room expects them.",
            group_id=group["id"],
            now=NOW,
        )
        assert out is not None and out["post_type"] == "celebration"
        feed = service.list_feed(db, host, now=NOW)
        assert [p["id"] for p in feed] == [out["id"]]

    def test_celebration_honors_standing_display_toggle(self, db, host, group, member):
        db.seed(
            "rsn_profiles",
            {
                "user_id": member,
                "display_name": "Quiet One",
                "show_attendance_default": True,
                "show_standing_default": False,
            },
        )
        out = service.create_celebration_post(
            db, member, "A milestone.", group_id=group["id"], now=NOW
        )
        assert out is None
        assert db.rows("rsn_posts") == []


# ---------------------------------------------------------------------------
# Comments + reactions
# ---------------------------------------------------------------------------


class TestComments:
    def test_member_comments_and_counts(self, db, host, group, member):
        post = make_post(db, host, group)
        out = service.create_comment(
            db, member, post["id"], models.CommentCreate(body="See you there."),
            now=NOW,
        )
        assert out["status"] == "published"
        comments = service.list_comments(db, host, post["id"])
        assert len(comments) == 1 and comments[0]["body"] == "See you there."
        assert service.get_post(db, host, post["id"])["comment_count"] == 1

    def test_ghost_cannot_comment(self, db, host, group):
        post = make_post(db, host, group)
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            service.create_comment(
                db, ghost, post["id"], models.CommentCreate(body="boo"), now=NOW
            )

    def test_comment_on_invisible_post_is_not_found(self, db, host, group):
        post = make_post(db, host, group)
        outsider = add_member(db)
        with pytest.raises(service.NotFoundError):
            service.create_comment(
                db, outsider, post["id"],
                models.CommentCreate(body="let me in"), now=NOW,
            )

    def test_held_comment_hidden_from_others_tombstone_for_author(
        self, db, host, group, member
    ):
        post = make_post(db, host, group)
        comment = service.create_comment(
            db, member, post["id"], models.CommentCreate(body="borderline"),
            now=NOW,
        )
        row = db.rows("rsn_comments")[0]
        row["status"] = "held"
        assert service.list_comments(db, host, post["id"]) == []
        mine = service.list_comments(db, member, post["id"])
        assert len(mine) == 1
        assert mine[0]["id"] == comment["id"]
        assert mine[0]["body"] is None and mine[0]["status"] == "held"


class TestReactions:
    def test_react_is_idempotent_and_removable(self, db, host, group, member):
        post = make_post(db, host, group)
        service.add_reaction(db, member, post["id"], "congrats", now=NOW)
        out = service.add_reaction(db, member, post["id"], "congrats", now=NOW)
        assert out["reaction_counts"]["congrats"] == 1
        assert out["viewer_reactions"] == ["congrats"]
        assert len(db.rows("rsn_reactions")) == 1
        out = service.remove_reaction(db, member, post["id"], "congrats")
        assert out["reaction_counts"]["congrats"] == 0
        assert db.rows("rsn_reactions") == []

    def test_ghost_cannot_react(self, db, host, group):
        post = make_post(db, host, group)
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            service.add_reaction(db, ghost, post["id"], "like", now=NOW)


# ---------------------------------------------------------------------------
# Reports from feed items flow into the safety queue
# ---------------------------------------------------------------------------


class TestFeedReports:
    def test_report_on_post_lands_in_safety_queue(self, db, host, group, member):
        post = make_post(db, host, group)
        out = service.create_report(
            db,
            member,
            models.ReportCreate(
                target_type="post",
                target_id=post["id"],
                category="harassment",
                description="Targeted insults in this post.",
            ),
        )
        assert out["status"] == "open"
        # The subject is resolved from the post, never the body.
        assert out["subject_user_id"] == host
        rows = db.rows("rsn_reports")
        assert len(rows) == 1
        assert rows[0]["target_id"] == post["id"]
        assert rows[0]["reporter_id"] == member

    def test_serious_category_bypasses_ambassadors_to_staff(
        self, db, host, group, member
    ):
        post = make_post(db, host, group)
        out = service.create_report(
            db,
            member,
            models.ReportCreate(
                target_type="post",
                target_id=post["id"],
                category="threats",
                description="A threat was posted.",
            ),
        )
        assert out["triage_queue"] == "staff"

    def test_report_on_comment_resolves_comment_author(
        self, db, host, group, member
    ):
        post = make_post(db, host, group)
        comment = service.create_comment(
            db, member, post["id"], models.CommentCreate(body="spam links"),
            now=NOW,
        )
        out = service.create_report(
            db,
            host,
            models.ReportCreate(
                target_type="comment",
                target_id=comment["id"],
                category="fraud",
                description="Scam links.",
            ),
        )
        assert out["subject_user_id"] == member

    def test_report_on_invisible_post_is_not_found(self, db, host, group):
        post = make_post(db, host, group)
        outsider = add_member(db)
        with pytest.raises(service.NotFoundError):
            service.create_report(
                db,
                outsider,
                models.ReportCreate(
                    target_type="post",
                    target_id=post["id"],
                    category="fraud",
                    description="I cannot even see this.",
                ),
            )
        assert db.rows("rsn_reports") == []

    def test_ghost_cannot_report(self, db, host, group):
        post = make_post(db, host, group)
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            service.create_report(
                db,
                ghost,
                models.ReportCreate(
                    target_type="post",
                    target_id=post["id"],
                    category="fraud",
                    description="ghost report",
                ),
            )

# TW-941 Evidence: Twistor Social feed backend + rewards-loop wiring (backend half)

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-941 (reporter Justynn, high): from the TW-937 plan. The Social
feed had tables only (rsn_posts, rsn_comments, rsn_reactions in
migration_social_v1.sql, TW-650), no endpoint or service function. Rewards
loop: the catalog ladder (twistor-lab/brand/twistor-social-reward-catalog.md:
Regular, Fixture, Pillar, Keyholder activity rungs; thresholds are planning
values set by Justynn) computed from the existing check-in/standing
machinery, exposed as a service + read API for Sofia's profile display.
Branch `zeke/tw-941-feed-rewards`, own worktree, off main @ 45a845c
(verified before worktree creation). No migration: the existing schema was
sufficient for every behavior in this slice; nothing was applied, merged,
or deployed.

Scope notes: ghost-town read scopes and band-invisibility follow the
existing service code exactly (MemberContext.can_write / is_skeleton,
_group_visible_to_member, block rules); nothing was weakened. Reports
integrate against the report mechanism on main today (the POST /reports
route + rsn_reports table + safety.py triage routing), not against
TW-940's parallel Safety Desk branch. Report intake replaces the honest
501 stub in service.create_report; TW-940's branch will also touch that
surface, so a merge-time reconciliation on create_report is expected and
flagged on the ticket.

## 2. Task report

**Feed posts.** `service.create_post` (voice required; group posts need
active membership; celebrations/recaps refused as system-generated) and
reads `list_feed` / `get_post` behind `_can_view_post`, the fail-closed
predicate: skeleton viewers see no feed at all (Section 5.11 closed
list), blocks are mutual invisibility, group posts inherit the group's
band rating and private/hidden membership wall, audience scope
(group / connections / close_friends) composes with the band filter,
and a container-less metro post fails closed on the author's own band
with the teen wall in both directions. Chronological, newest first, no
ranking. Validation: RED 29 failed in tests/test_social_feed.py before
the service existed; GREEN 41/41 new tests after (feed + rewards files).

**Comments and reactions.** `create_comment` / `list_comments`
(published visible to viewers of the post; held/removed rows are a
body-less tombstone for their author only, pattern 03) and
`add_reaction` / `remove_reaction` (idempotent per post/user/kind;
counts and viewer state ride the post payload).

**Celebrations.** `service.create_celebration_post` is the system path
(no member route): it posts a ledger fact to the member's group (or
metro), and returns None without writing when the member's profile has
show_standing_default off. Auto-triggering on tier promotion inside
recompute is deliberately NOT wired in this slice (see gaps).

**Report intake.** `service.create_report` implemented: voice required,
target must exist, feed targets must be visible to the reporter (else
the standard NotFound shell), post/comment subjects are resolved from
the item, never the body, and the row lands in rsn_reports with status
"open" (the safety queue) plus a triage_queue of "staff" for the
safety.py bypass categories (threats, sexual_misconduct,
identity_fraud, and unknown categories fail closed to staff) or
"ambassador_triage" otherwise. The router test that pinned the old
501 was updated to the new 201/403 mapping at the service seam.

**Rewards loop.** `service.get_my_rewards` projects the member's loop
records through `_project_member_stats` (extracted verbatim from
recompute_member_standing, which now calls it; recompute behavior is
unchanged and its suite stays green) and `rewards.progress`, a pure
projection over trust.py's own constants (no second ladder, no
restated numbers). Returns tier, progress toward the next rung as
per-requirement current/required/met/remaining facts, counts, show
rate with the catalog's 5-decided-RSVP display gate, and the full
ladder. The Verified rung returns phase "future_verification" with
earned=False in every state (parked verification phase). While the
band is watch/restricted the display tier is null and suspended is
true; earned_tier still reports the member's private position.

**Routes added** (all under /api/v1/social): POST /posts,
GET /feed, GET /posts/{post_id}, POST /posts/{post_id}/comments,
GET /posts/{post_id}/comments, POST /posts/{post_id}/reactions,
DELETE /posts/{post_id}/reactions/{kind},
GET /me/standing (canonical), GET /profiles/me/standing (alias).

**Contract reconciliation with Sofia's display half.** Sofia's
parallel branch (sofia/tw-941-rewards-display @ 73958f6) built her
client against a documented contract at GET /social/me/standing
with a fail-closed normalizer (frontend/src/lib/socialStanding.js).
The first GREEN payload did not match it (different path, different
field vocabulary), so the backend reconciled to her documented
contract in a follow-up commit: the standing payload is now a
superset carrying both shapes (see the contract section below),
and the reconciliation is proven against her actual code, not a
re-reading of it: a real get_my_rewards payload (5 seeded nights,
Regular) was piped through her normalizeStanding under node; it
accepted the payload and mapped activityTier=regular,
nextTier=fixture, with the corroboration either/or parts intact
("SOFIA NORMALIZER ACCEPTS BACKEND PAYLOAD").

### Rosa-confirmed contract: GET /api/v1/social/me/standing

Rosa's gate on the display half confirmed this exact shape and
tightened semantics, and the backend holds to them (gate tests in
TestDisplayContract, RED 57a4370 then GREEN):

```
{ standing_tier, band, suspended, age_band,
  stats { completed_nights, decided_rsvps, show_rate,
          reviews_received, would_meet_again_rate, hosted_clean,
          best_series_streak, distinct_vouches, tenure_days },
  next_tier, progress [ {key, current, target, met} ] | null,
  last_movement }
```

- band is always one of trusted / good_standing / watch /
  restricted (trust.compute_band's closed vocabulary), never null.
- next_tier is always the immediate successor of standing_tier,
  null at the top tier or while suspended (gate test:
  test_suspended_next_tier_is_null).
- Teen accounts (band age_16_17) cap at Pillar: standing_tier is
  clamped at Pillar and next_tier is never Keyholder for a teen
  (catalog Section 7; gate test:
  test_teen_caps_at_pillar_never_keyholder_next).
- Keyholder progress includes the sustained show-rate condition
  at target 0.90 (catalog Section 1, chained through Pillar in
  trust.py; gate test:
  test_keyholder_progress_carries_sustained_show_rate).
- stats projects decided_rsvps (= completed nights + no-shows)
  and show_rate explicitly; show_rate_display (canonical extra)
  carries the 5+ decided honesty gate.
- standing_tier "verified" is the base state only; the ladder's
  Verified rung is never marked earned (future_verification).

### Display contract detail

The display half consumes these fields (fail-closed normalizer;
extra fields are ignored): standing_tier (the member's tier,
never null; masking is the normalizer's job via suspended/band),
band, suspended, age_band ("21+" | "18-20" | "teen" | null),
stats {completed_nights, decided_rsvps, show_rate,
reviews_received, would_meet_again_rate, hosted_clean,
best_series_streak, distinct_vouches, tenure_days}, next_tier,
progress [{key, current, target, met}] in the display vocabulary
(nights, showRate, wmaRate, wmaReviews, hostedClean, streak,
tenureDays, plus a single corroboration entry {key:
"corroboration", met, parts: [{key: "streak", current, target},
{key: "vouches", current, target}]} for the Fixture either/or;
progress is null while suspended), and last_movement
("dormancy" | "conduct" | null: conduct when the band suspends
the tier, dormancy when decay stepped it below the raw record
tier). The canonical rewards shape below rides in the same
payload, so both consumers read one response.

### Canonical rewards shape (same payload)

```json
{
  "user_id": "uuid",
  "tier": "regular | fixture | pillar | keyholder | verified | null",
  "earned_tier": "verified | regular | fixture | pillar | keyholder",
  "band": "trusted | good_standing | watch | restricted",
  "suspended": false,
  "show_rate": 1.0,
  "show_rate_display": true,
  "would_meet_again_rate": null,
  "counts": {
    "completed_nights": 5, "no_shows": 0, "rsvps_decided": 5,
    "reviews_received": 0, "would_meet_again_positive": 0,
    "hosted_clean": 0, "best_series_streak": 0,
    "distinct_vouches": 0, "tenure_days": 0, "dormant_days": 0
  },
  "next_tier": "fixture",
  "next_requirements": [
    {"key": "completed_nights", "current": 5, "required": 20,
     "met": false, "remaining": 15},
    {"key": "show_rate", "current": 1.0, "required": 0.85,
     "met": true, "remaining": 0},
    {"key": "best_series_streak", "current": 0, "required": 4,
     "met": false, "remaining": 4, "any_of": "corroboration"},
    {"key": "distinct_vouches", "current": 0, "required": 3,
     "met": false, "remaining": 3, "any_of": "corroboration"}
  ],
  "ladder": [
    {"tier": "verified", "phase": "future_verification",
     "earned": false, "is_current": false, "requirements": []},
    {"tier": "regular", "phase": "activity", "earned": true,
     "is_current": true, "requirements": ["...same req shape..."]},
    {"tier": "fixture", "phase": "activity", "earned": false,
     "is_current": false, "requirements": ["..."]},
    {"tier": "pillar", "phase": "activity", "earned": false,
     "is_current": false, "requirements": ["..."]},
    {"tier": "keyholder", "phase": "activity", "earned": false,
     "is_current": false, "requirements": ["..."]}
  ]
}
```

Contract notes: `tier` is the display tier (null while suspended;
render no tier then, per pattern 06). Requirement keys:
completed_nights, show_rate, best_series_streak, distinct_vouches,
reviews_received, would_meet_again_rate, hosted_clean, tenure_days.
The two Fixture requirements carrying "any_of": "corroboration" are
an either/or pair: the rung requirement is met when either is met,
and each req's `met` already reflects the pair outcome. Rates carry
current as a float or null (null = no record yet); `remaining` is
null for rate requirements with no current value. Feed post payload:
{id, author_id, group_id, post_type, body, media_refs, visibility,
audience_scope, created_at, reaction_counts {like, congrats,
celebrate}, viewer_reactions [kinds], comment_count}.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Ghost/T1 accounts cannot post, comment, react, report, or read the feed | test_social_feed.py (TestCreatePost, TestFeedVisibility, TestComments, TestReactions, TestFeedReports) | RED | PASS | RED run: 29 failed pre-implementation; GREEN run below |
| 2 | Group posts inherit the container band rating fail-closed, even with a seeded membership row | test_social_feed.py::TestFeedVisibility::test_under_band_member_cannot_see_post_inheriting_group_rating | RED | PASS | 41/41 new tests |
| 3 | Blocks are mutual invisibility on the feed; close_friends scope reaches only close friends; metro posts fail closed on author band | test_social_feed.py::TestFeedVisibility | RED | PASS | 41/41 new tests |
| 4 | Celebrations are system-only and honor show_standing_default off (no row written) | test_social_feed.py::TestCelebrations | RED | PASS | 41/41 new tests |
| 5 | Held comments are invisible to others and a body-less tombstone for the author | test_social_feed.py::TestComments | RED | PASS | 41/41 new tests |
| 6 | Reports on posts/comments land in rsn_reports as open, subject resolved from the item, serious categories routed to staff | test_social_feed.py::TestFeedReports | RED | PASS | 41/41 new tests |
| 7 | Catalog thresholds project to tier + next-rung progress; Fixture corroboration is either/or | test_social_rewards.py::TestPureProgress | RED | PASS | rewards module missing at RED (collection error) |
| 8 | Verified rung is future-phase and never earned, even on a Keyholder record | test_social_rewards.py::TestPureProgress::test_verified_rung_is_future_phase_never_earned | RED | PASS | 41/41 new tests |
| 9 | Watch band suspends the display tier while earned_tier stays visible to the member | test_social_rewards.py (pure + service strike test) | RED | PASS | 41/41 new tests |
| 10 | Standing recompute behavior is unchanged by the projection extraction | test_social_checkin_loop.py TestStandingRecompute + full Social suite | unit | PASS | 405/405 Social tests (baseline 363 + 41 new + 1 net router) |
| 11 | Ruff clean on every authored file | python3 -m ruff check + ruff format --check (8 files) | CI | PASS | "All checks passed!" / "8 files already formatted" |

Commands actually run (backend/):
`python3 -m pytest tests/test_social_feed.py tests/test_social_rewards.py -q`
RED: 29 failed + 1 collection error. GREEN: 41 passed.
Full Social set (12 files, listed in the ticket comment): 405 passed
at GREEN, 407 passed after the display-contract reconciliation
(2 added contract tests), 411 passed after Rosa's gate alignment
(4 added gate tests). Baseline on main before the change: 363
passed on the same set minus the two new files and the updated
router test. Cross-contract check: node running Sofia's
normalizeStanding against a real backend payload, accepted (see
Section 2).

## 4. Coverage and known gaps

- No coverage plugin run; the Social suites are the coverage vehicle
  in this repo (in-memory FakeDb service tests + router seam tests).
- NOT wired: auto-posting celebrations at the moment a tier promotion
  lands (create_celebration_post is the system entry point; the
  recognition job / recompute hook is a follow-up). Event recaps have
  no member create path (no event link column on rsn_posts).
- NOT wired: comment screening/moderation actions (held/removed are
  honored on reads; setting them is the TW-940 Safety Desk's write
  side), teen voice quota (rsn_voice_usage untouched: the teen tier
  is parked, phase 2, and no quota number exists in the codebase to
  enforce), media ingest choke point (media_refs stored as given;
  Phase 6 item 27), feed pagination cursors (limit only, newest
  first), collectibles derivation and Year Out / standing card
  (tasks.md item 23, not this ticket).
- Pre-existing, unrelated: tests/test_tradeview_leaks.py and other
  non-Social files error at collection on an xmlsec/lxml environment
  issue; reproduced on unmodified main, not caused by this branch.

## 5. Plan-safety notes

No untrusted plan content was followed. The brief, the catalog, and
the spec patterns were treated as data and checked against the code
on main: where the brief said the report endpoint exists, the route
existed but the service was an honest 501 stub, so intake was
implemented against the rsn_reports table on main as instructed,
and the divergence is flagged above and on the ticket for the
TW-940 merge. No embedded instructions were found in any file read.

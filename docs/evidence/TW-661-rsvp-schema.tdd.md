# TW-661 Evidence: RSVP opens-at, held-seat accounting, couple-RSVP RPC

Prepared by Twistor Holdings LLC

## Source

Ticket TW-661 (filed by Rosa during the TW-660 review; reporter
Justynn; builder Dre Coleman). Branch `dre/tw-661-rsvp-schema` in
worktree `~/workspace/worktrees/dre-tw661-rsvp`, based on lbt-os main
@ 0beb8e2 (TW-660 merged). Checkpoints: RED `1c1e33a`, GREEN
`4c3f788`, this evidence commit follows.

Rosa's filing rationale, addressed point by point: the merged
schema had (1) no opens-at column for the rsvp_window enum to
anchor to, (2) no held-seat accounting, so host-held seat pools
could not be built lawfully, and (3) couple-RSVP atomicity only at
the service layer.

## What landed

Migration `supabase/migration_social_v2.sql` (new file; v1 untouched):

1. `rsn_events.rsvp_opens_at` + `rsn_event_series.default_rsvp_opens_at`
   (template copied onto materialized instances), partial index on
   set values. NULL means the general opening is the publish
   moment, the Section 7.7 default TW-660 recorded truthfully.
2. `rsn_event_seat_holds` (event, holder, seats > 0, status
   held/released/expired/consumed, release_at, released_at) with a
   CHECK that a hold is either live (held, no released_at) or
   settled (released_at stamped). Release rules: explicit host
   release, expiry at release_at, host conversion (consumed).
   Accounting rule: a seat counts exactly once. Going RSVPs count
   in rsn_rsvps; active held seats count in this table
   (`rsn_active_held_seats`); the two never overlap because a hold
   is not an RSVP. `rsn_release_expired_seat_holds` settles expired
   holds (per-event or global sweep shape). RLS deny-all, same
   posture as every rsn_ table. One cross-table rule cannot be a
   column CHECK and is enforced at the service write layer:
   release_at is never after the event start.
3. `rsn_rsvp_couple(...)` RPC: advisory-locked per event
   (`pg_advisory_xact_lock`), inserts both first-class rows and
   returns them; any failure aborts the transaction, both rows or
   neither.

Service wiring (`backend/app/social/service.py`, `models.py`):

- Standing waves: `RSVP_WAVE_EARLY_ACCESS` (Verified 0h, Regular
  12h, Fixture 24h, Pillar 48h, Keyholder 72h before the general
  opening, per the reward catalog). Before a member's window,
  `request_rsvp` refuses with a ConflictError; inside an early
  window the row records `rsvp_window` = the standing tier; at or
  after the general opening it records 'general'. No opens-at
  means TW-660 behavior exactly.
- Held seats: `_seats_taken` = going + active held, with lazy
  expiry settled on every capacity consult. Used by initial RSVP
  status, host approval, and waitlist promotion, so a hold counts
  against capacity in every decision and never twice.
- Couple RSVP: all TW-660 validations still run first (both
  partners independently gated before any write); the write
  prefers the RPC and falls back to the two service inserts when
  the RPC is unavailable (client without rpc / function not yet
  deployed), so behavior is identical in both modes.
- Payloads: event payloads carry `rsvp_opens_at`; series payloads
  carry `default_rsvp_opens_at`; `EventCreate.rsvp_opens_at` and
  `SeriesCreate.default_rsvp_opens_at` are the write inputs.

API contract for Sofia (TW-652 surfaces): `POST /events` accepts
optional `rsvp_opens_at`; event detail adds `rsvp_opens_at`
(ISO or null); a too-early RSVP answers the existing 409 shape
with detail "RSVP has not opened for your standing tier yet".
No route signatures changed.

## Task report

RED (command: `pytest tests/test_social_migration_v2.py
tests/test_social_rsvp_windows.py` at `1c1e33a`): 11 failed,
1 passed, 12 errors. The 1 pass was deliberate: the TW-660
no-opens-at behavior guard. The 12 errors were the missing-file
fixture for the migration contract.

GREEN (same command at `4c3f788`): 24 passed. Breakdown:
migration contract 12, service behavior 12 (waves 6, held seats 4,
couple RPC 2).

## Guarantees

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | v2 DDL contract: opens-at columns, seat-holds table/CHECKs, RLS deny-all, 3 functions, scope guard | tests/test_social_migration_v2.py | RED | 12 passed | RED run above, GREEN rerun |
| 2 | Waves refuse early, admit per standing, label window truthfully | tests/test_social_rsvp_windows.py TestRsvpWaves | RED | 6 passed | same |
| 3 | Held seats count once in RSVP, host-approval, promotion paths; expired/released holds never count; expiry settles lazily | TestHeldSeats | RED | 4 passed | same |
| 4 | Couple RSVP prefers RPC (one call, both rows), falls back identically | TestCoupleRpc | RED | 2 passed | same |
| 5 | TW-660/TW-650 behavior unchanged: social + verify + events suites | pytest tests/test_social_*.py tests/test_verify_*.py tests/test_events_*.py | unit | 547 passed | run 2026-10-21 |
| 6 | Zero new failures vs same-env clean-main baseline | full pytest, node-set diff | CI | branch 1321/20/7 vs baseline 1297/20/7, failed+error node sets byte-identical (27 nodes, diff empty) | /tmp captures at run time; counts in ticket comment |
| 7 | Migration executes on real PostgreSQL, idempotent (v2 run twice clean) | pgserver harness, psql -f v1 + v2 x2 | integration | clean | live run log summary below |
| 8 | Couple RPC is atomic on a real DB: duplicate partner aborts BOTH rows | live probe 2 | integration | user row count 0 after failed pair call | below |
| 9 | Hold accounting on a real DB: active = 2 (expired excluded), sweep settles 1, CHECK rejects inconsistent hold, RLS on + deny policy present | live probes 3-5 | integration | as designed | below |

Live harness disclosure (same pattern as TW-650/TW-658): the
TW-109 spike venv's pgserver build (PostgreSQL 16.2) copied to
/tmp, run as user `nobody`, stub `idn_users` as the only
prerequisite table, no-op pgcrypto extension shim registered in
the copied tree only (the build ships no pgcrypto;
gen_random_uuid is server core since PG13; Supabase ships real
pgcrypto). verify v1 + social v1 applied first, as production
order requires. Scratch deleted after the run.

## Coverage and known gaps

- The wave offsets live in the service layer (one constant),
  not in SQL; a future tier change touches one dict and these
  tests.
- `_try_couple_rpc` treats a non-2-row RPC result as unavailable
  and falls back to service inserts. PostgREST returns both rows
  or an error, so that path is defensive only; if it ever fired
  after a partial write it could double-write, which the
  UNIQUE(event_id, user_id) constraint would reject loudly
  rather than silently corrupt. Named here so Rosa can rule on it.
- Hold creation/conversion endpoints are NOT in this slice: the
  ticket asked for accounting + release rules in schema, wired
  into capacity decisions. The host-facing hold/release API is a
  follow-up slice (the `host_held` rsvp_window already exists in
  the v1 enum for the conversion path).
- No scheduler wiring for the global expiry sweep; the service
  settles lazily per event, and the function accepts a NULL event
  id for the future sweep job.

## Fix round (Rosa REQUEST CHANGES, 2026-10-21)

Rosa's review (docs/reviews/TW-661-review.md) reproduced every
claim above and returned one major plus two minors. All addressed;
checkpoints RED `28ec3c1`, GREEN `5ce73d1`, this section follows.

**M1: the couple path never checked the partner's wave window,
and labeled the partner's row with the requester's wave.** Her
probe: event opens at NOW + 24h, Keyholder requester, Verified
partner; solo the partner is refused at NOW, yet through the
couple path the partner's row landed as ('going', 'keyholder').
Fix, exactly her shape:
- `request_rsvp` computes the partner's opening from `partner_ctx`
  through the same `_rsvp_opening`; while `now` is before the
  partner's opening the pair is refused (409 ConflictError) with
  zero rows written. Both partners eligible or neither row: the
  eligibility gate is now atomic with the write, like the write
  itself.
- Each row records its own admitting wave (Section 7.7) in both
  paths: the RPC takes a new `p_partner_rsvp_window` parameter
  spent on the partner's row only, and the service fallback passes
  `partner_window` to the partner's `_create_rsvp_row`.
- Signature change handled as Rosa required: the migration drops
  the original 9-argument overload explicitly
  (`DROP FUNCTION IF EXISTS rsn_rsvp_couple(UUID, UUID, UUID,
  UUID, TEXT, TEXT, TEXT, UUID, TIMESTAMPTZ)`) before CREATE OR
  REPLACE, so the old overload cannot linger callable.

**m1: `_try_couple_rpc` swallowed every exception silently.** It
now logs a warning (with traceback) on exception, and a warning
on a non-2-row result, before the fallback runs; the fallback is
unchanged. A `log = logging.getLogger(__name__)` was added to the
service module, the codebase's dominant convention.

**m2: the migration header overclaimed the advisory lock.** The
header and the in-function comment now state precisely what the
lock does (serializes pair writes per event so two couples'
inserts cannot interleave) and what it does not do (the rows'
capacity statuses are computed at the service layer before the
RPC is called, so two racing couples' capacity decisions are not
made atomic; capacity stays service-advisory, consistent with the
rest of the core loop).

Regression tests (RED first, quoting Rosa's probe; the
`_RpcDb` double was updated to the two-window contract, resolving
both windows before any write so the double stays atomic like the
real RPC):
- Pair refused before the partner's window, zero rows, fallback
  path; same refusal on the RPC path with the RPC never invoked.
- Solo-refusal parity is asserted inside the fallback test (the
  partner refused alone at the same moment).
- Per-row labels: Keyholder requester + Regular partner at +13h
  land 'keyholder' / 'regular' in both paths; the RPC test also
  pins the two window parameters on the recorded call.
- Rosa's probe pair after the general opening: both rows land,
  both labeled 'general'.
- m1: a broken-RPC double still lands the pair via the fallback
  AND emits a warning naming rsn_rsvp_couple (caplog-pinned).
- Migration contract: `p_partner_rsvp_window` present and the
  explicit DROP of the 9-argument overload present.

RED (fix-round tests at `28ec3c1`, before the fix): 6 failed /
25 passed. The 6 failures are exactly the new behavior tests (5)
and the new migration contract test (1); the general-opening
labels test passed pre-fix by design (behavior pin for the
post-window half of the probe), as did all 24 original tests.

GREEN: TW-661 test files 31/31. Scoped suites (social + verify +
events) 554 passed (547 before, +7 new). Full suite: branch
1361 passed / 20 failed / 7 errors vs a same-environment
clean-main control (0beb8e2, temporary worktree) at 1330 / 20 / 7:
failed-plus-error node sets byte-identical (27 nodes, diff
empty), zero new failures; the passed delta is exactly +31
(24 TW-661 originals + 7 fix-round). Ruff check and format clean
on every touched file.

Live PostgreSQL 16.2 probes (same harness discipline as above:
pgserver tree copied to /tmp with its psutil/fasteners imports so
it runs under the system python as `nobody`, pgcrypto no-op shim
in the copied tree only, stub `idn_users`, scratch deleted after;
service-level probes drive the real `app.social.service` through
a psql adapter implementing the client's query vocabulary with
row_to_json reads):
- Fresh path: verify v1 + social v1 + new v2 apply clean; new v2
  applied a second time clean; exactly one rsn_rsvp_couple
  overload exists, 10 args, including p_partner_rsvp_window.
- Upgrade path: old v2 (at `4c3f788`) leaves the 9-arg overload;
  applying new v2 replaces it: exactly one overload (10 args),
  9-arg count 0; re-applying new v2 on the upgraded database is
  clean and leaves one overload.
- Service level, 10/10 checks: Rosa's probe on the real DB (solo
  partner refused at NOW; pair refused; zero rows in rsn_rsvps);
  Keyholder + Regular pair at +13h admitted through the real RPC
  with rows labeled 'keyholder' / 'regular' in the table itself;
  Pillar + Fixture pair through the service fallback on the real
  DB labeled 'pillar' / 'fixture' (the Fixture boundary instant,
  opening exactly at NOW, admits); the new RPC's atomicity
  re-checked (pairing a fresh user with an already-RSVP'd partner
  errors inside the RPC and leaves the requester with 0 rows).

Recorded requirements, confirmed already on TW-661 (Rosa's review
comment, 2026-10-21 16:25 UTC), not duplicated: the G2 follow-up
(host hold create/convert API) must carry Section 7.7's
20-percent-of-capacity cap and 24h-before-start auto-release; and
the future event-cancel slice must settle an event's holds when
an event is cancelled (the holds table's ON DELETE CASCADE covers
hard deletes only). Neither is a fix item in this round.

## Plan-safety notes

None. No untrusted plan content was encountered; all direction
came from the ticket and the merged TW-660 code.

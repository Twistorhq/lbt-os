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

## Plan-safety notes

None. No untrusted plan content was encountered; all direction
came from the ticket and the merged TW-660 code.

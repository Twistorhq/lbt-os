<!-- Prepared by Twistor Holdings LLC -->

# TW-650 TDD evidence: Twistor Social schema + service skeletons

Branch `dre/tw-650-social-schema` off lbt-os main @ `1e5b67a`.
Scope: OpenSpec tasks.md items 9, 13, 14 (spec Section 4 data model).

## Deliverables

| Artifact | Contents |
|---|---|
| `supabase/migration_verify_v1.sql` | 8 `vfy_` tables: engine_versions, consents, sessions, documents, biometrics, signals, identity_anchors, age_attestations |
| `supabase/migration_social_v1.sql` | 36 `rsn_` tables incl. fix-round deltas: `rsn_close_friends`, `rsn_ambassadors`, audience-scope columns on posts/events, `rsn_rsvps` `requested` sub-state + `host_decided_by`/`host_decided_at` |
| `backend/app/social/` | `models.py` (fail-loud Pydantic), `trust.py` / `geo.py` / `safety.py` (pure functions, implemented), `service.py` / `sessions.py` (honest stubs), `router.py` (11 routes, slowapi buckets, Identity JWT auth, 501 until feature phases) |
| `backend/app/main.py` | social router registered under `/api/v1` (2 lines) |
| `backend/tests/test_social_*.py` | 6 files, 145 tests |

Run order matters and is stated in both file headers: verify first
(`rsn_bans` / `rsn_sanctions` / `rsn_appeals` hold FKs into
`vfy_identity_anchors`).

## RED → GREEN

- RED checkpoint `5bfd397`: all 6 test files failing (missing SQL
  files; `app.social` package absent).
- GREEN: **145/145 social tests pass** (74 migration contract, 30
  models, 23 trust, 14 geo, 10 safety, 7 router; counts overlap the
  parametrized migration cases).

## Full-suite comparison (same venv, `--continue-on-collection-errors`)

| | Baseline main @ 1e5b67a | TW-650 branch |
|---|---|---|
| passed | 706 | **851** (+145, all new) |
| failed | 21 | 21 |
| collection errors | 12 | 12 |

The failed and errored node sets are **byte-identical** to baseline
(diff-verified): failures live in `test_bi_whitelabel.py`,
`test_missed_call.py`, `test_pilot_blockers.py`,
`test_usage_metering.py`; collection errors are the pre-existing
sandbox environment defects (missing argon2/sqlalchemy, broken
supabase-py auth client). Zero regressions, zero new failures
attributable to TW-650.

Ruff (CI invocation, repo `ruff.toml`): all new files clean and
format-clean. `app/main.py` carries exactly the one pre-existing
I001 import-order error it had at baseline (also noted in TW-649's
evidence); the social import was placed in sorter order so TW-650
adds no new violation.

## Design decisions taken (flagged for Rosa)

1. **Migration testing is static-parse, not live-DB.** This matches
   how the repo treats Supabase SQL (no live Postgres in the sandbox
   suite; Telemetry's migration SQL is likewise unexecuted here).
   The contract tests pin table presence, idempotency, RLS coverage,
   and the PII invariants in DDL form. Live execution against the
   real Supabase project remains a launch-gate step, same as every
   prior module.
2. **`rsn_event_locations` surrogate id.** Spec Section 4 keys
   locations by `event_id` and also gives series a `venue_ref`
   pointing at "the series' usual location record" — a series has
   no event, so a location row needs a stable id independent of any
   event. The table carries `id` PK, `event_id` UNIQUE, `series_id`
   UNIQUE, and an exactly-one-owner CHECK; the series FK is closed
   by a guarded `ALTER TABLE` after both tables exist. Event
   ownership semantics are unchanged.
3. **Delete behavior split.** Owned/ephemeral rows CASCADE with the
   account; evidentiary rows (RSVPs, check-ins, confirms, welfare
   checks, reviews, reveals, reports, sanctions, appeals, trust
   ledger, collectibles, guest passes, posts, comments) use NO
   ACTION so account closure must pass through the retention job
   (item 10a) instead of vaporizing the Section 6.3 evidence trail.
4. **Report categories extended by 3.** Spec Section 4 lists six;
   the TW-648 design's serious categories (threats,
   sexual_misconduct, identity_fraud) bypass ambassadors at intake
   and need to exist as categories. Decisions-first precedence
   (design.md) governs; `safety.ambassador_may_triage` fails closed
   to staff on anything unknown.
5. **`venue_type` on `rsn_events` now, not in Phase 4.** Home
   hosting (item 17b) keys off it and the geo reveal windows differ
   by it (48h venue / 3h home); adding it in the ground-up migration
   avoids a follow-up ALTER for a decided feature.
6. **Plan catalog seeded, prices NULL.** `organizer_free` at 0 and
   `organizer_plus` with `price_cents NULL` until Justynn sets
   launch prices (spec Section 4), inserted `ON CONFLICT DO NOTHING`.
7. **Trust thresholds are the catalog's planning values**, encoded
   as named constants in `trust.py` (cumulative tiers; WMA sample
   floors 15/30; dormancy 90-day grace + one step per 60 days,
   tier-only). Band mapping (1 strike/report = watch, 2 = restricted,
   trusted at 10 clean nights) is a documented planning default for
   Phase 6 to tune with case data.

## Not in scope (by ticket)

- `evt_` tables (TW-651), `dps_` tables (TW-653, held at concept).
- `vfy_school_registry` and the school-ID flow (phase 2, spec
  Section 5.12); `vfy_door_photos` (TW-653's Angela-gated artifact).
- The `idn_users` verification pointer (tasks.md item 11, engine
  build phase) and ghost-town write-scope minting (item 17).
- Engine pipeline stages (Zeke's TW-649 lane); this ticket is the
  schema the engine writes to, not the engine.

## API contract for Sofia (TW-652)

Routes (all under `/api/v1/social`, Identity JWT required, 501 until
feature phases): `GET|PUT /profiles/me`, `POST /groups`,
`GET /groups/{id}`, `POST /events`, `GET /events/{id}`,
`POST /events/{id}/rsvps`, `POST /rsvps/{id}/decision`,
`POST /events/{id}/reveal` (5/min, fail-closed),
`POST /events/{id}/checkins`, `POST /reports` (10/hour).
Request/response shapes are the Pydantic models in
`backend/app/social/models.py`; enum vocabularies there are kept in
lockstep with the SQL CHECK constraints.

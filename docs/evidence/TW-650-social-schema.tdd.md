<!-- Prepared by Twistor Holdings LLC -->

# TW-650 TDD evidence: Twistor Social schema + service skeletons

Branch `dre/tw-650-social-schema` off lbt-os main @ `1e5b67a`.
Scope: OpenSpec tasks.md items 9, 13, 14 (spec Section 4 data model).

## Deliverables

| Artifact | Contents |
|---|---|
| `supabase/migration_verify_v1.sql` | 10 `vfy_` tables: engine_versions, consents, sessions, documents, biometrics, signals, identity_anchors, age_attestations, verification_states, retention_actions (the last two added in Rosa's fix round, M1) |
| `supabase/migration_social_v1.sql` | 36 `rsn_` tables incl. fix-round deltas: `rsn_close_friends`, `rsn_ambassadors`, audience-scope columns on posts/events, `rsn_rsvps` `requested` sub-state + `host_decided_by`/`host_decided_at` |
| `backend/app/social/` | `models.py` (fail-loud Pydantic), `trust.py` / `geo.py` / `safety.py` (pure functions, implemented), `service.py` / `sessions.py` (honest stubs), `router.py` (11 routes, slowapi buckets, Identity JWT auth, 501 until feature phases) |
| `backend/app/main.py` | social router registered under `/api/v1` (2 lines) |
| `backend/tests/test_social_*.py` | 6 files, 182 tests |

Run order matters and is stated in both file headers: verify first
(`rsn_bans` / `rsn_sanctions` / `rsn_appeals` hold FKs into
`vfy_identity_anchors`).

## RED → GREEN

- RED checkpoint `5bfd397`: all 6 test files failing (missing SQL
  files; `app.social` package absent).
- GREEN: **145/145 social tests pass** (74 migration contract, 23
  models, 21 trust, 12 geo, 9 safety, 6 router; counts are per-file
  collections, parametrized migration cases included. Corrected in
  the fix round: the original printing of this line misstated the
  per-file split while the total was right, Rosa m3.)
- Fix-round RED checkpoint `e4a600c`: 63 tests failing against the
  reviewed implementation, one per behavior Rosa's review required.
- Fix-round GREEN: **182/182 social tests pass** (90 migration
  contract, 28 models, 25 trust, 12 geo, 19 safety, 8 router).

## Full-suite comparison (same venv, `--continue-on-collection-errors`)

| | Baseline main @ 1e5b67a | TW-650 branch |
|---|---|---|
| passed | 706 | **888** (+182, all new) |
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
2. **`rsn_event_locations` surrogate id, single series link.**
   Spec Section 4 keys locations by `event_id` and also gives series
   a `venue_ref` pointing at the series' usual location record: a
   series has no event, so a location row needs a stable id
   independent of any event. The table carries `id` PK and
   `event_id` UNIQUE. As first shipped it also carried a
   `series_id` back-pointer, which Rosa rejected (M2): two links
   storing one relationship can diverge, and for a table holding
   home addresses, ambiguity is a safety defect. The fix round
   drops `series_id`; `venue_ref` is the only link, and a partial
   unique index on it keeps the relationship one-to-one. The series
   FK is closed by a guarded `ALTER TABLE` after both tables exist.
3. **Delete behavior split.** Owned/ephemeral rows CASCADE with the
   account; evidentiary rows (RSVPs, check-ins, confirms, welfare
   checks, reviews, reveals, reports, sanctions, appeals, trust
   ledger, collectibles, guest passes, posts, comments) use NO
   ACTION so account closure must pass through the retention job
   (item 10a) instead of vaporizing the Section 6.3 evidence trail.
   The fix round extends the same rule to the whole `vfy_` side
   (Rosa M1): nothing in `migration_verify_v1.sql` cascades, because
   consents, sessions, documents, biometrics, and signals are the
   Section 6.3 audit record on that side too.
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
8. **Retention is a state machine, not an outcome log (fix round,
   M1).** `vfy_retention_actions` records determination, deadline
   (plus the one TW-654 complexity extension), counsel hold with
   reason, dormancy notice, tier drop, and execution per subject;
   `vfy_verification_states` is the current-state row the dormancy
   drop acts on, with a CHECK that tier and status can never
   disagree. Destruction is tombstoning in place (payload emptied,
   row and history kept), enforced by CHECK constraints, so the
   legal record survives the data it governed.
9. **Consent-type match enforced in DDL (fix round, m4).** The
   composite FK settles ownership (a biometric can only reference
   its own member's consent). The type match (school ID sessions
   ride on school-ID consent) is a deferred constraint trigger
   rather than a service-contract rule: a consent is a legal
   instrument, capture order inside a session is not fixed, and a
   rule that lives only in one service's code can be bypassed by
   the next writer. The trigger checks at commit from both sides,
   whichever of document or biometric lands second.
10. **`dob_hash` nullable, required by class (fix round, m2).**
    Rather than dropping `school_id` from the v1 vocabulary until
    Phase 2, the column is nullable and a CHECK requires the hash
    for the government document classes, exactly as spec Section 4
    words it. A fabricated hash to satisfy a NOT NULL would be
    worse than the gap it hid.
11. **Sanction helpers split by doctrine (fix round, M3).** The
    DDL keeps `purge` as an `rsn_sanctions` level (spec Section 4),
    but `safety.py` no longer has one ladder function: misconduct
    (`next_misconduct_outcome`), severe routing
    (`severe_offense_route`), and the integrity pipeline
    (`next_purge_stage`) are separate functions with separate
    vocabularies, and the misconduct function rejects `purge` as an
    input so the doctrines cannot be cross-wired by a caller.

## Rosa fix round (review of 2026-10-20, REQUEST CHANGES)

| Finding | Fix |
|---|---|
| M1 retention/dormancy outcome-only; vfy cascades erase the audit record | `vfy_retention_actions` + `vfy_verification_states` (decision 8); attestation `superseded_at`; payload/template tombstone CHECKs; every `vfy_` FK is NO ACTION (live catalog: 0 vfy cascades) |
| M2 series location linked twice | `series_id` dropped from `rsn_event_locations`; `venue_ref` sole link + 1:1 partial unique index (decision 2) |
| M3 sanction helper encodes the wrong ladder | Helpers split per Sections 6.6/6.7/6.2 (decision 11); ladder tests rewritten |
| M4 Fixture drops the vouch path | `MemberStats.distinct_vouches`; Fixture corroboration is streak >= 4 OR distinct vouches >= 3; vouches never substitute for nights or show rate (tests pin both) |
| m1 read contracts discard the viewer; check-in model under-specced | `get_group`/`get_event` take `viewer_user_id` end to end (router passes the token subject); `CheckinCreate` carries the Section 7.1 payload with per-proof-type validation bound to `geo.validate_arrival_payload` |
| m2 `dob_hash` NOT NULL vs school ID | Nullable + class CHECK (decision 10) |
| m3 evidence per-file breakdown misstated | Corrected above, for the original 145 and this round's 182 |
| m4 biometric can borrow another member's consent | Composite FK `(consent_id, user_id)` to `vfy_consents(id, user_id)`; consent-type match in DDL (decision 9) |
| m5 collectibles uniqueness does not bind for ref-less rules | Partial unique index over `(user_id, rule_key, ref_type, value) WHERE ref_id IS NULL` |
| n1 em dash in this document | Removed; the document now greps clean for em and en dashes |

**Live execution, this round** (supersedes design call 1's limit
for these two files): both migrations were executed verbatim
against a throwaway PostgreSQL 16.2 cluster (the pgserver build in
the TW-109 spike venv, copied to scratch space, run as an
unprivileged user, stub `idn_users` as the only prerequisite).
Fresh run clean; second run clean (idempotency by execution).
Catalog: 46 tables, 46 RLS-enabled, 46 deny-all policies, every
policy qual `false`; plan seed live (`organizer_free` 0,
`organizer_plus` NULL). Behavioral probes, 18 of 18 as designed:
school-ID document without a birthdate accepted, government
document without one rejected; cleared-with-live-payload and
destroyed-with-live-template rows rejected, proper tombstones
accepted; another member's consent rejected by the composite FK;
consent/document class mismatch rejected by the deferred trigger
at commit, matched pair accepted; retention hold without a reason
rejected; verification state with tier/status disagreement
rejected, dormancy drop (verified tier 2 to dropped tier 0)
accepted; account delete blocked by NO ACTION while vfy rows
exist; second series on one location row rejected by the 1:1
index; duplicate ref-less collectible rejected by the partial
index; a non-owner role with explicit grants sees 0 rows and its
insert is rejected by RLS. Harness disclosure: the copied pgserver
tree ships no pgcrypto, and the migrations' only pgcrypto-adjacent
call is `gen_random_uuid()` (server core since PostgreSQL 13), so
the harness registered a no-op pgcrypto extension shim in the
copied tree only. No repository file was touched for the harness;
Supabase ships real pgcrypto, so the result carries. Harness
scratch was deleted after the run.

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

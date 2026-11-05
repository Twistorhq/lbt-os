# TW-949 Evidence: Tori Persistence Foundation (TW-948-B1)

Prepared by Twistor Holdings LLC

Author: Dre Coleman, Senior Backend Engineer and DBA
Date: 2026-11-05
Ticket: TW-949 (TW-948-B1, from the TW-948 build map Section 4)
Spec source: tori-product-models.md Section 8.1 (durable session
continuity, scheduled jobs, staged writes)

## 1. Architecture record (the decision this ticket settles)

**lbt-os is the persistence home for Tori.** The agent stack that
Tori actually runs (identity, permissions, durable turns, toll
ledger) already lives in `~/workspace/repos/lbt-os`, merged to
main. All new persistence schema and services for Tori land in
lbt-os, in its Postgres and Supabase shape, behind its existing
(org, agent) scoping discipline.

**Twistor Core is a pattern source only.** Patterns were ported,
code was not imported and Core is not a runtime dependency:

- Core workforce migration 001 (`001_workforce.up.sql`): the
  schedule table shape (per-job timezone, tenant scoping) and the
  GIN full-text index shape.
- Core docsearch (`docsearch/indexer.py`): the full-text design
  (documents as the unit of management, chunks as the unit of
  retrieval, tenant scoping on every row). Here the session is
  the unit of management and the message is the unit of
  retrieval.
- lbt-os house patterns, reused directly: the toll ledger
  migration (unique idempotency key shape), the TW-768 agent
  permissions migration (agt_ scoping, RLS deny-all plus guarded
  policy creation, vocabularies pinned in both layers), and the
  durable turns engine (write and acknowledge before answering;
  the append service is the journal write for a session message).

## 2. What landed (files only; nothing applied, nothing deployed)

Migration: `supabase/migration_persistence_tw949.sql`

- `agt_sessions`: org, agent, and workspace scoping;
  `parent_session_id` lineage link to the parent session;
  channel, status vocabulary, timestamps.
- `agt_session_messages`: session reference, per-session `seq`
  with `UNIQUE (session_id, seq)`; raw body in the bronze column
  set (`body_bronze`), the ingestion hash (`body_hash`), and the
  masked retrieval projection (`body_masked`, `search_text`).
  The GIN full-text index is built on `search_text`, the masked
  projection, never on the bronze column.
- `agt_jobs`: self-contained prompt, ordered `skill_keys`,
  cron expression, per-job IANA timezone, status vocabulary,
  `UNIQUE (org_id, agent_identity_id, name)`.
- `agt_job_fires`: durable fire identity `fire_key` as a UNIQUE
  idempotency key, plus `UNIQUE (job_id, scheduled_for)` for the
  same-minute no-double-fire contract.
- `agt_job_continuity`: one record per job (`UNIQUE (job_id)`),
  last fire reference, last run summary, surfaced item ids for
  dedupe against the job's own history.
- `agt_staged_writes`: staging shape for memory and skill
  writes (kind, target key, pinned target hash, masked proposed
  body and its hash, gate status vocabulary, receipt id column).
  This table has no bronze column by design.
- Per-agent workspace scoping: every table above carries
  `workspace_id` (the per-agent workspace key). TW-948-B12 builds
  the contained file area on this key; B1 lands the column so no
  later ticket retrofits scoping.
- RLS: enabled on all six tables with a deny-all policy on each,
  guarded for re-runs, following the agt_ house pattern. No org
  policy is added: no anon or authenticated client reads these
  tables, and the backend service role is the only reader and
  writer, filtering org, agent, and workspace manually.
- Reversibility: a DOWN section at the foot of the migration
  documents the reversal in reverse dependency order. Since the
  migration is never applied by this ticket, rollback before
  application is deleting the file.

Services: `backend/app/persistence/`

- `models.py`: pinned vocabularies shared with the migration
  CHECK constraints, validation that raises `PersistenceError`
  raw, `DuplicateFireError`, the keyed ingestion hash, the
  masking projection builder, and the workspace key helper.
- `service.py`: scoped read and write functions for sessions,
  messages, lineage walks, jobs, fires, continuity, and staged
  writes. Every function takes (db, org, agent) and filters on
  all three scope keys (org, agent, and the workspace key
  derived from them) for every query; a caller-supplied
  workspace_id that differs from the derived key is refused.
  Message retrieval returns masked projection dicts only, the
  append return is the same projection, and retrieval queries
  on the messages table select projection columns only, so the
  bronze column is never loaded outside the explicitly named
  `get_message_bronze` audit path. Duplicate fire identity is
  refused in the service before insert, via targeted queries
  only; the migration UNIQUE constraints are the second layer
  for any writer that bypasses the service.

## 3. PII posture (for Rex review)

Raw transcript body exists in exactly one column, `body_bronze`,
marked bronze in the migration header and in code. Every
retrieval shape above bronze is masked and carries the keyed
ingestion hash for audit matching. Full-text search runs on the
masked projection only. Staged writes carry no raw body at all.
This follows the medallion doctrine: hashed at ingestion, raw
only in bronze, masked in every layer above it.

Keyed-hash standard (recorded against TW-552, fix round):
hashes of PII bodies in this schema are HMAC-SHA256, keyed by a
server-environment secret (`TWISTOR_PERSISTENCE_HASH_KEY`) that
is never stored in any row, domain-separated by (org, agent)
scope. A bare unsalted SHA-256 is never used for a PII body:
for a short body the bare hash is the hash of the identifier
itself and is dictionary-reversible. Production hashing fails
closed when the key is unset. The standard applies to
`agt_session_messages.body_hash` and to
`agt_staged_writes.proposed_body_hash`.

Masking coverage (fix round): `mask_text` covers every
identifier category the doctrine names: email addresses
including obfuscated shapes, street addresses and PO boxes,
dates including birth dates, SSNs, EINs, phone numbers in full
and 7-digit shapes, ZIP codes, SAM.gov UEI values, and other
business identifier shapes (long digit runs, CAGE-style codes).
Personal names are not regex-maskable reliably; that needs NER
and stays an open hardening item under Rex's review. The same
pipeline masks the non-message free text that shares it:
session titles and metadata, continuity summaries and surfaced
item ids, and staged write bodies plus their target key and
target reference fields.

## 4. Tests

- `backend/tests/test_persistence_migration.py`: migration shape
  tests in the existing migration test style (tables and scope
  guard, org plus agent plus workspace columns, RLS deny-all,
  lineage column, bronze plus masked shape, FTS target, fire
  uniqueness shapes, DOWN section).
- `backend/tests/test_persistence_service.py`: service behavior
  against a stateful in-memory fake client in the house pattern:
  scoping isolation, lineage chain walk returning the full order
  root first, cross-scope parent refusal, append hashing and
  masking, duplicate seq refusal, search over the projection,
  duplicate fire id rejected at the service layer, same job and
  same scheduled time rejected, continuity merge without
  duplicates, staged writes landing pending in masked shape.
  Fix round additions: the Rosa workspace probe as a regression
  (foreign override refused on session, job, and staged write
  creates; a row stamped with a foreign workspace is invisible
  to scoped reads), the append return pinned as the projection
  with bronze storage proven via `get_message_bronze`, a
  short-message hash pinned as not dictionary-reversible, one
  masking pin per TW-552 category, staged body and target
  masking, and session and continuity free-text masking.

Test counts and commands are recorded on ticket TW-949 with the
final commit.

## 5. Fix round record (2026-11-05, Rosa gate + Rex PII review)

Rosa Delgado returned REQUEST CHANGES with three must-fixes;
Rex Ryder's PII review returned FAIL with findings 1, 3, 4, and
5 in B1 scope. All of the following landed in this round:

- Rosa 1 / Rex F7 (service half): workspace is now scoped, not
  just stamped. Every query filters on the derived workspace
  key, and create paths refuse a workspace_id override that
  differs from it. Regression tests pin Rosa's probe.
- Rosa 2 / Rex F8 (scan half): `record_fire` no longer loads
  the fires table unscoped. Duplicate checks are targeted: an
  eq on fire_key, and a scoped eq on job_id + scheduled_for.
- Rosa 3 / Rex F1: `append_message` returns the masked
  projection. Retrieval queries on the messages table select
  projection columns only, so body_bronze is not loaded into
  retrieval-path memory; bronze exits only via
  `get_message_bronze`.
- Rex F3: masking extended to every doctrine category (see
  Section 3), with a test pin per category.
- Rex F4: keyed HMAC-SHA256 ingestion hashes in both hash
  columns, standard recorded in Section 3.
- Rex F5: staged writes mask the body and the target key and
  reference before storage. B2's approve path must re-run the
  same masking as defense in depth before applying a write.
- Rex F6 (same-pipeline half): session title and metadata,
  and continuity summary and surfaced item ids, are masked on
  write. The continuity update is also scope-filtered on its
  UPDATE, closing Rex F11 inside this round.
- Note for the B4 builder: the global `fire_key` UNIQUE
  matches the toll-ledger idempotency precedent and stays;
  B4 must namespace fire keys per job when it mints them.

Ticketed now, gated before any migration application or any
router ticket (Rex disposition; assigned to Dre Coleman):

- TW-955: Rex F2, an enforceable bronze-access boundary
  (TW-768 permission check plus a mandatory audit event on
  every bronze access).
- TW-956: Rex F7 remainder, composite (org, agent, workspace)
  foreign keys or equivalent, plus last_fire_id validation.
- TW-957: Rex F9, column-level protection for body_bronze and
  migration tests pinning more than the deny-all string.
- TW-958: Rex F8 remainder (non-leaking duplicate semantics),
  Rex F10 (no database-level tie between bronze and the
  projection), and the Rex F6 remainder. The F6 remainder
  reasoning: `agt_jobs.prompt` and name are owner-authored
  instructions, so masking on write would corrupt job
  semantics and needs a classification decision rather than
  silent masking; `agt_job_fires.summary` and `error` have no
  writer in B1, so their masking lands with the B4
  fire-completion writer that creates them.

## 6. Explicitly not done

- No migration was applied to any Supabase project.
- No deploy, no merge. This branch waits for Rosa review and
  Justynn's merge word.
- No scheduler behavior (TW-948-B4), no memory gate logic
  (TW-948-B2), no session continuity service (TW-948-B6), no UI.
  B1 lands the schema and the thin scoped services those
  tickets build on.

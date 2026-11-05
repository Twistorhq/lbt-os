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
  raw, `DuplicateFireError`, the ingestion hash, the masking
  projection builder, and the workspace key helper.
- `service.py`: scoped read and write functions for sessions,
  messages, lineage walks, jobs, fires, continuity, and staged
  writes. Every function takes (db, org, agent) and filters on
  scope for every query. Message retrieval returns masked
  projection dicts only; the bronze column is reachable only
  through the explicitly named `get_message_bronze` audit path.
  Duplicate fire identity is refused in the service before
  insert; the migration UNIQUE constraints are the second layer
  for any writer that bypasses the service.

## 3. PII posture (for Rex review)

Raw transcript body exists in exactly one column, `body_bronze`,
marked bronze in the migration header and in code. Every
retrieval shape above bronze is masked (email addresses, phone
shaped digit runs, and long digit runs replaced) and carries the
SHA-256 ingestion hash for audit matching. Full-text search runs
on the masked projection only. Staged writes carry no raw body
at all. This follows the medallion doctrine: hashed at
ingestion, raw only in bronze, masked in every layer above it.

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

Test counts and commands are recorded on ticket TW-949 with the
final commit.

## 5. Explicitly not done

- No migration was applied to any Supabase project.
- No deploy, no merge. This branch waits for Rosa review and
  Justynn's merge word.
- No scheduler behavior (TW-948-B4), no memory gate logic
  (TW-948-B2), no session continuity service (TW-948-B6), no UI.
  B1 lands the schema and the thin scoped services those
  tickets build on.

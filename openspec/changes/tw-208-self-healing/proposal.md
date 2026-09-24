# TW-208: Self-healing pipeline standard (leak engine + CSV ingestion slice)

**Prepared by Twistor Holdings LLC**

- Ticket: TW-208 · Reporter: Justynn · Owner: Zeke Okafor (Lead Build Engineer)
- Branch: `feature/tw-208-self-healing` · Base: `main` @ `410d3f4`
- Status: implementation complete, awaiting Rosa review before merge

## Problem

Justynn's standing rule: *"I want my pipelines to be self healing so one
error doesn't crush the full process."* Today a single poison row in a
pilot CSV import aborts the entire import with a 400, a single detector
exception can break the morning brief, and a transient DB blip during
benchmark recording 500s the request. One bad record must never sink the
batch.

## Scope (this slice)

Leak engine (`backend/app/leak_engine/engine.py`), CSV ingestion
(`backend/app/services/manual_import.py`), and benchmark recording
(`backend/app/services/benchmarks.py`). Notification paths and the
repo-wide rollout follow in a later ticket, after Rosa reviews this slice.

## Proposed change

A shared resilience primitive (`backend/app/self_healing.py`) plus
per-path retrofits:

1. **Per-item fault isolation** — rows, detectors, and metric batches are
   processed independently; a poison item is skipped loudly, never aborts
   its batch.
2. **Transient retries with exponential backoff + jitter** — DB blips heal
   themselves; permanent errors (validation bugs, bad contracts) raise
   immediately into the error bucket with no pointless retries.
3. **Dead-letter capture + loud structured logging** — every skipped item
   records index, a truncated preview, the error, and a UTC timestamp;
   summaries are JSON-serializable for import logs and briefs.
4. **Graceful partial completion** — imports return exact skipped-row counts
   with row numbers + reasons; the brief carries an honest
   `data_status.partial` flag whenever anything is skipped or errored.
5. **Health checks + cheap automatic recovery** — a `health_check(db, tables)`
   probe backs the engine's table-availability check; chunked inserts retry,
   then fall back to per-row inserts.

## Non-goals

- Notification retry/DLQ (deferred — separate ticket after this review).
- Repo-wide rollout to every pipeline (deferred — after this slice is green
  and Rosa-approved).
- Changing whole-file failure semantics: oversized files, invalid UTF-8,
  missing headers, and unsupported entity types remain honest 400s.

## Acceptance criteria

- 20 new tests prove: poison-item isolation, backoff retries, DLQ capture,
  partial reporting (RED first, then GREEN).
- Full backend suite green (92 tests + 8 subtests) under CI's exact dummy
  environment.
- `ruff check app tests` clean.
- TDD evidence report in `docs/evidence/TW-208-self-healing.tdd.md`.
- Rosa review before merge; nothing merges without Justynn's exact approval.

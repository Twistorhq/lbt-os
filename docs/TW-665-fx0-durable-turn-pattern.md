# TW-665: Durable Agent Turn Pattern (fx0 implant)

Prepared by Twistor Holdings LLC

## Source and license

Pattern-mined from vercel-labs/fx0 (the libfx agent inside it), Apache-2.0,
verified live in the 2026-10-21 GitHub sweep (score 72) and read at
https://github.com/vercel-labs/fx0 at commit 9e8e7ae. The upstream LICENSE
(Apache License, Version 2.0) was read before any work. This is a clean-room
pattern implant: the design ideas below are adopted, no upstream code is
copied, and the implementation is ours (`backend/app/durable_turns/`).

## What fx0 does (the pattern)

One `createFxAgent()` call gives fx0 a model loop, streaming, tool calls,
retries, and durable sessions. Durability rests on three pieces: the prompt
is saved before it is answered; every step of the turn is saved as it runs
(Workflow World upstream); and a backup wake-up (Vercel Queues upstream)
restarts the turn in a new process, which continues from the last saved
step. A turn stops after 64 model steps, subagents included. A tool call
cut off by a crash re-runs only if the tool declared itself safe to repeat;
the interrupted turn is told what may have happened.

## What was adopted

| fx0 idea | Twistor implant |
|---|---|
| Save the message before answering; a retried request reaches the same turn | `submit()` journals the prompt before execution and dedupes on `message_id` (deterministic turn id) |
| Per-step journal in a durable store | `durable_steps` table: started, then completed with the result, journaled before/after the handler runs |
| Turn survives the process dying mid-reply | `run_turn()` is resumable by any new process on the same journal; completed steps are read back, never re-run |
| Tools declare whether they may re-run | `StepDef.idempotent` is mandatory; an interrupted non-idempotent step is marked `interrupted_unknown_outcome` and never re-run; the turn's `interrupted` flag tells the caller |
| Side effects in the crash window | `ctx.apply_effect(key, fn)` lands an effect exactly once per turn, even when the crash hit after the effect but before the completion write |
| One worker holds a session; others are fenced out | Lease with owner + expiry on the turn row; a live lease refuses a second worker (`TurnFenced`); a dead worker's lease expires and the resumer takes over |
| 64-step turn cap | `DEFAULT_STEP_CAP = 64`; a capped turn ends in status `step_cap` with the results it had |
| Stream copied with a cursor so each line lands once | `read_events(turn_id, cursor=...)` returns journal events after the cursor; re-reading from the new cursor returns nothing twice |
| Retries inside the loop | TW-208 self-healing `retry_with_backoff` / `is_transient`: transient failures retry, permanent failures fail the turn at once |

## What was deliberately NOT adopted

- **Vercel Workflow World** (the upstream step store): vendor-gated.
  Replaced by our own journal tables. The prototype uses a SQLite file so a
  turn survives across real OS processes with zero services; the tables map
  1:1 onto Postgres for production, which is our store of record.
- **Vercel Queues** (the upstream wake-up): vendor-gated. Replaced by a
  plain resumable call; in production the wake-up is our own Flows
  scheduler / TW-208 primitives, which we already run.
- **Vercel AI Gateway** (upstream model billing path): a paid path,
  FOSS-first rule says no. The engine is model-agnostic: steps are plain
  callables, so the caller keeps whatever model path it already owns.
- fx0's app surface (design agent, HTML artifact, sign-in, trial billing):
  out of scope. TW-665 is the durable-turn pattern only.

## Immediate use (TW-274)

`backend/scripts/tw665_crash_demo.py` runs a real 6-step dispatch turn
(load job, assign tech, reserve slot, notify customer, write invoice
draft, close job) in a child process and SIGKILLs it mid-turn, after the
notify effect landed but before its completion was journaled.

- Before (volatile in-memory turn, today's behavior): process killed with
  SIGKILL, fresh process recovers 0 of 6 steps, turn lost, 4 landed side
  effects orphaned.
- After (this engine): journal after the kill shows 3 steps completed and
  4 effects recorded; a fresh process waits out the dead worker's lease,
  resumes, and completes 6 of 6 steps with 0 duplicate side effects.
  Verdict PASS, reproduced in the evidence doc.

## Where the pattern goes next in the fleet

1. Flows (TW-625) executions: wrap each workflow run in a durable turn so a
   worker death mid-run resumes instead of restarting paid steps.
2. Outreach / Notify sends: sends are the canonical non-idempotent step;
   the ambiguous-outcome rule stops a crash from double-sending.
3. Events ingest (TW-651) and science ingestors (TW-611): long multi-step
   ingests gain resume instead of full re-runs.
4. Any future agent loop (Diego sweeps, call-intel batches): submit-first
   journaling plus the 64-step cap is the default shape.

Production hardening still owed before any of that ships: move the journal
to Postgres with row-level fencing in one transaction, add a heartbeat so
long steps extend their lease, and Rosa review (this branch is unmerged).

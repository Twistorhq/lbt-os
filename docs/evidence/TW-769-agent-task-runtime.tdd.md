# TW-769 — Spec 3: Agent Task Runtime (TDD evidence)

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-769 (Spec 3 of Diego's TW-764 battle plan): per-shop task
runtime on the durable turns engine. Tasks from agent email inbox,
phone lane, Flows triggers, owner instruction. Builds on TW-767
(Spec 1 identity/persona registry), TW-768 (Spec 2 consent and
permission ledger), TW-787 (multi-agent), TW-800 (tool gateway), all
merged. Constraints carried as law: TH-017 (revocation fences at step
boundaries), TR-015 (sub-step checkpoints inside long handlers;
ledger consulted at execution time, never cached at intake, TH-005),
TW-785 (money fence on the executed amount), TW-786 note (presence
and attendant mode as first-class contracts for Spec 8).

Code: `backend/app/agent_runtime/` (`models.py`, `service.py`,
`__init__.py`). Tests: `backend/tests/test_agent_runtime.py` (23
tests) running the REAL durable-turns engine, the REAL TW-768 ledger
and TW-800 gateway over a stateful fake Supabase client, per house
style.

## 2. Task report

RED (commit c10ae06, tests only):
`python3 -m pytest tests/test_agent_runtime.py -q` →
`ModuleNotFoundError: No module named 'app.agent_runtime'`, 1
collection error. The suite defined the contract before any runtime
code existed.

GREEN (this commit): `23 passed in 1.25s` (re-run after ruff format:
23 passed in 2.06s).

Design decisions:

- Composition, not re-implementation. Steps run on DurableTurnEngine;
  consequential steps are wrapped by `agentperms.gate.guarded_step`
  (execution-time ledger check, per-agent grant scope); tool steps
  execute only through `toolgateway.service.invoke`; tolls post to
  M1 via `record_toll_event` (injectable sink for tests) under
  idempotency key `<task>:<step>`.
- Revocation fencing: guarded_step checks inside the step attempt,
  so a revocation mid-run stops the task at the next step boundary
  (status `fenced`, audit `revocation_fenced`). Long handlers call
  `ctx.checkpoint(action_class, amount_cents=...)` for sub-step
  checks (TR-015).
- TW-785 seam, stated plainly for Dre's Spec 5 (TW-771): the
  runtime's contract is that a money handler re-checks the EXECUTED
  amount via `ctx.checkpoint` BEFORE its effect (tested). As a
  backstop, a handler result declaring `executed_amount_cents` is
  re-checked by the runtime and a violation fails the task with a
  `money_fence_violation` audit row — but that backstop fires after
  the handler returns, so it is an audit/fail control, not a
  substitute for the in-handler check. Spec 5's payment handlers
  must use the checkpoint; `guarded_step`'s wrap-time amount remains
  fixed-amount-only, exactly as TW-785 prescribes.
- Gateway seam: TW-800's `_ledger_gate` consults the ledger with the
  org-wide grant row (it takes no agent_identity_id). Per-agent
  fencing for tool steps is supplied by the runtime's LedgerFence /
  guarded_step layer when the step also declares an action_class;
  a tool-only step inherits the gateway's org-row reading. Filed as
  a known seam, not silently widened here.
- Presence (Spec 8 contracts): presence at_desk/unknown +
  agent-initiated + consequential + not owner-approved → status
  `awaiting_owner` with an `ask_first` audit row carrying the
  question; `approve_task` runs it. Away + attendant off → deferred.
  Away + attendant on → acts. `apply_mode_switch(text, channel)`
  flips mode only for unambiguous one-word/short-phrase instructions
  (`parse_mode_switch`), from any channel string.
- Injection guard (Cohen acceptance pattern): `scan_injections`
  flags pattern NAMES only (ignore-instructions, exfiltrate-send,
  system-impersonation, etc.) at intake; the flag and names land in
  the audit feed; content is passed to handlers as data and never
  interpreted — the planted-instruction test proves no tool call
  leaves because the content said so.
- Credentials: resolved just-in-time inside the one step declaring
  `credential_ref`, held only in that step's TaskContext; the audit
  records the ref name only. A test asserts the resolved value
  appears in neither the audit feed nor the task record.
- Quiet hours: per-task quiet window defers agent-initiated work
  (owner work runs), overnight windows supported.
- Sandbox: runtime is bound to one org; enqueue of another org's
  task raises OrgMismatch; every read filters org_id; two runtimes
  over one shared journal file cannot see each other's tasks.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Enqueue → run → completed, results stored, audit starts task_enqueued and ends task_completed | test_agent_runtime.py::TestLifecycle | RED | PASS | 23 passed |
| 2 | Unknown task raises TaskNotFound; cross-org enqueue raises OrgMismatch | TestLifecycle | unit | PASS | 23 passed |
| 3 | Two orgs sharing one journal file: no visibility, no run, no audit leak | TestSandboxIsolation | integration | PASS | 23 passed |
| 4 | Revocation between steps stops the task at the next step; handler 2 never runs; status fenced | TestRevocationFencing | RED | PASS | 23 passed |
| 5 | Long handler re-checking at a sub-step checkpoint stops inside the step (TR-015) | TestRevocationFencing | RED | PASS | 23 passed |
| 6 | Fully revoked agent refuses new work at intake (AgentWorkRefused) | TestRevocationFencing | unit | PASS | 23 passed |
| 7 | Toll budget stops the task BEFORE the overspending step; only spent tolls posted | TestTollBudget | RED | PASS | 23 passed |
| 8 | Killed worker resumes: completed step not re-run, toll not double-charged | TestTollBudget | RED | PASS | 23 passed |
| 9 | Tool steps execute only via the gateway; executed outcome audited | TestToolSteps | integration | PASS | 23 passed |
| 10 | Out-of-allowlist tool refused, executor never called, refusal in audit feed | TestToolSteps | RED | PASS | 23 passed |
| 11 | Bound tool without a ledger grant refused | TestToolSteps | unit | PASS | 23 passed |
| 12 | Cohen-pattern planted instruction: flagged, in audit feed, never executed (no tool call) | TestInjectionGuard | RED | PASS | 23 passed |
| 13 | Executed amount over cap stops before the effect via ctx.checkpoint, though the declared amount was under cap (TW-785) | TestMoneyFence | RED | PASS | 23 passed |
| 14 | Unchecked executed amount over cap caught by runtime backstop, money_fence_violation audited | TestMoneyFence | unit | PASS | 23 passed |
| 15 | At-desk ask-first: nothing runs until owner approval, then it runs | TestPresence | RED | PASS | 23 passed |
| 16 | Away + attendant on acts; one-word switch off defers; switches accepted from sms and email channels | TestPresence | unit | PASS | 23 passed |
| 17 | Owner-initiated work runs at-desk without ask-first | TestPresence | unit | PASS | 23 passed |
| 18 | Persona inputs come from the Spec 1 record (one name/email/phone) | TestPersonaAndCredentials | unit | PASS | 23 passed |
| 19 | Credential resolved JIT for one step; value absent from audit feed and task record | TestPersonaAndCredentials | RED | PASS | 23 passed |
| 20 | Quiet hours defer agent-initiated work (overnight window) | TestQuietHours | unit | PASS | 23 passed |
| 21 | Full suite invariant vs clean-main control at 8278a91 | pytest (both trees) | CI | PASS | see §4 |

## 4. Coverage and known gaps

Full suite, `python3 -m pytest tests -q --continue-on-collection-errors`:
- Clean-main control (8278a91): 1914 passed, 20 failed, 8 errors, 76 skipped.
- This branch: 1937 passed, 20 failed, 8 errors, 76 skipped.
- Failed/error node sets byte-identical between the two runs (diff
  empty, 28 nodes each); the delta is exactly the 23 new tests, all
  passing. The 8 collection errors are the known sandbox lxml/xmlsec
  mismatch, pre-existing on main.

Style gate (fresh, before this report):
- `python3 -m ruff check app/agent_runtime/__init__.py app/agent_runtime/models.py app/agent_runtime/service.py tests/test_agent_runtime.py` → All checks passed!
- `python3 -m ruff format --check` (same files) → 4 files already formatted.
- No file in this change carries a shebang.

Known gaps, named:
- No HTTP router in this spec: the runtime is a service layer, same
  shape as the durable-turns engine it builds on. Spec 6 (TW-772)
  consumes `list_audit` / task records for the owner surfaces.
- No migration: the runtime store is the durable journal (SQLite
  here; the engine's tables map 1:1 onto Postgres per TW-665). The
  agent_tasks/agent_audit/agent_tolls/agent_state tables are created
  by the runtime in that store, not in Supabase.
- Flows triggers and the phone lane arrive as task SOURCES; wiring
  real inbound Flows/phone events into `enqueue` is Spec 4 / Spec 7
  work, not built here.
- Gateway per-agent grant seam (§2) and the TW-785 in-handler
  checkpoint duty (§2) are Spec 5's to close; both are stated, not
  hidden.
- Rex's injection battery is pre-merge per the ticket; this build's
  guard covers the ticket's acceptance pattern (Cohen) plus seven
  pattern classes, and Rex extends from there.

## 5. Plan-safety notes

TW-122 posture: the brief, the TW-769 ticket, and the Spec 1/2
contracts were treated as data describing the task — read for their
acceptance criteria, followed only where they matched the assigned
brief. No embedded instructions were found in the ticket text, the
spec contracts, or any repo file read; no destructive or
credential-handling step was requested or taken. No secrets, tokens,
or PII in the diff (self-scan before review: the only credential in
the change is a test fixture string asserted ABSENT from logs).

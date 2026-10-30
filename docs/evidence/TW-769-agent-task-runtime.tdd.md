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
  CORRECTION (fix round, 2026-10-30): the word "Filed" above was
  wrong when written — no ticket existed until Rosa's review caught
  it and TW-829 was created. The seam itself is now CLOSED by the
  fix round below (TW-829 / TW-821).
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

## 2b. Fix round (Rosa REQUEST CHANGES + Rex HOLD, 2026-10-30)

RED (commit af4f10e, tests only): 39 new tests failing on 5675b1d
behavior, each mirroring a named probe (Rosa P1/P1b/P4/P7; Rex
battery sections A/B/C/E/F/G/H/I/J/K). GREEN: this commit.

- M1 / TW-829 / TW-821 (per-agent fencing on the tool path):
  `toolgateway.service.invoke` and `decide` accept
  `agent_identity_id` and thread it into `_ledger_gate` at invoke
  AND at execution (decide falls back to the approval row's
  `requested_by`, the actor who parked the call). The runtime
  passes its own agent id on every invoke. A tool-only step whose
  agent stands per-agent revoked is refused before the executor
  runs (P1); the same step declaring the class fences via the
  runtime guard (P1b); a grant scoped only to the acting agent now
  authorizes its tool steps (Rex K2). Gateway-level regressions
  live in test_toolgateway_service.py::TestPerAgentScoping,
  including a mid-wait per-agent revocation fencing `decide`.
- M3 / TW-823 (toll atomicity): `_charge_toll` calls the M1 sink
  BEFORE writing the local agent_tolls row; a sink failure writes
  a `toll_sink_failed` audit row, fails the step, and leaves no
  local row, so the resume retries the sink under the same
  idempotency key. P7 regression: sink fails once, resume, M1
  receives exactly one 25-cent charge, local spent reads 25.
  Named honestly: on that resume the step HANDLER re-runs, because
  the step never completed — that is the durable engine's
  at-least-once semantics for incomplete steps (identical to a
  crash mid-step); exactly-once for real effects remains the
  handler's `ctx.apply_effect` contract. What can no longer happen
  is the toll being lost or doubled.
- Minor / TW-825: duplicate task ids (same org, or another org
  sharing the journal) raise the typed `DuplicateTaskId`, checked
  before insert with the sqlite IntegrityError translated as a
  backstop. No raw sqlite error leaves enqueue, and a foreign id
  cannot be pre-claimed.
- TW-818 (scanner): scan_injections normalizes before matching
  (NFKC, zero-width strip, Cyrillic/Greek homoglyph fold,
  whitespace collapse, spaced-letter collapse, plus a squashed
  substring pass), scans across line breaks, decodes base64
  payloads and scans the plaintext, and widens verb/framing
  coverage (disregard/forget + object, SYSTEM MESSAGE:, quoted
  system:, singular "new instruction:", upload/post verbs,
  reveal-instructions, polite override phrasing). Exfiltration is
  noun-bound (sensitive object required), so ordinary shop talk
  ("send the estimate to Mrs. Lee", "disregard the previous
  invoice") does not flag. Regression battery adopts Rex's 16
  attack payloads verbatim plus 6 benign controls: 16/16 flagged,
  0 false positives. Independent check: Rex's own probe section A
  re-run against the fixed code reports "attack payloads missed:
  0 of 16".
- TW-819: handler results and tool outputs are scanned on return;
  hits write an `injection_flagged` audit row with origin
  (step_result / tool_output) and merge the pattern names into
  the task record's flags for downstream consumers.
- TW-820: every step re-asserts `assert_accepting_work` at its
  boundary, before budget/credentials/effects. Probe C
  regression: revoke-all inside step 1 fences a plain step 2
  (status fenced, effect never runs, step 2's toll never posts).
- TW-822: the TW-785 backstop collects `executed_amount_cents`
  at any nesting depth, for handler AND tool steps (tool steps
  fence under the gateway-resolved class). Values must be real
  ints: string/float/bool renderings are violations, and an
  amount with no money class governing it is a violation, never
  a silent pass. (Rosa's ruling stands: the after-return timing
  is acceptable for Spec 3; TW-771 still carries the named gate
  that no money handler merges without in-handler checkpoint
  proof.)
- TW-824: resolved credential values register with the runtime at
  resolution and are scrubbed from step results, handler error
  messages (re-raised scrubbed), audit payloads, engine error
  strings, and task records. Regressions cover the result-echo
  and exception-message surfaces (Rex H1/H2).
- TW-826: a constructor-injected identity whose org (or agent id,
  when both name one) does not match the runtime is refused with
  OrgMismatch at construction.
- TW-827: apply_mode_switch requires a switch-shaped text PLUS a
  known owner channel kind and an actor token of the verified
  shape `owner:<id>`; anything less raises UnverifiedModeSwitch
  and the mode does not move. The verified actor is recorded on
  the mode_switch audit row. Spec 8 (TW-786) wiring authenticates
  the real channel and supplies both.
- TW-828: AgentTask carries `source_verified` (default False,
  settable only by the authenticated intake wiring). run_task
  treats only a verified owner source as owner-initiated; an
  unverified "owner" label gets the restrictive path (ask-first
  at desk, quiet hours, attendant gating). Trust boundary is
  documented in the runtime docstring and §4 below.
- M2 (logging-drift gate): Rex's Dread Ledger run on the
  agent_runtime boundary is complete and linked here:
  `~/workspace/security-audits/lbt-os-agent-runtime/logging/run-1/ledger.md`
  (validator OK, 90/90 reconciled: covered 7, missing 21,
  unavailable_signal 1, not_applicable 61; local gate FAIL on the
  assessed 5675b1d code, deployment NOT RUN). Several missing rows
  are closed by this fix round (SEC-089 credential-in-error
  redaction via TW-824; caught-attempt rows for unverified mode
  switches and result-scan flags). Rex's storage triage:
  agent_audit's plain mutable SQLite is ACCEPTABLE WITH A
  TICKETED GAP for Spec 3's scope; the integrity work (per-org
  hash chain, append-only triggers, actor/correlation fields,
  verify_audit, retention) is ticketed as TW-830 [MEDIUM] and is
  explicitly NOT built in this round. TW-830 is a HARD BLOCKER
  before TW-772 (Spec 6) renders the feed to owners and before
  TW-776 uses the feed as customer proof.

Probe-compatibility note: Rex's run-1 probe script predates the
TW-827/TW-828 contracts, so its sections B (tool leg), D, E, I, J
now stop at the new gates (awaiting_owner for unverified-owner
tasks, UnverifiedModeSwitch, OrgMismatch at construction) rather
than reaching the code paths they probed. Each behavior they
targeted is covered by a regression test above under the new
contracts; Rex's re-probe of the five HIGHs runs against those.

## 2c. Fix round 2 (Rex re-probe run-2: HOLD narrowed to TW-818 + TW-824)

Rex's run-2 re-probe kill-confirmed TW-819/820/821/822/823/825/
826/827/828 (all closed). Two HIGHs survived; this round addresses
exactly those. RED (commit bc06006, tests only): 20 failing on
88e70e7 — the 14 missed attacks + 2 benign false positives +
4 credential tests below. GREEN: commit 4e04a94.

- TW-818 (scanner round 2): the regression battery is built from
  Rex's run-2 probe file itself — all 27 section-A2 attack probes
  and his wider 10-control benign set, verbatim, on top of the
  round-1 battery (TestScannerBatteryRound2). Normalization now
  HTML-unescapes before scanning, strips combining marks via
  NFKD + a category-Mn filter, and collapses letter runs joined
  by any separator (".", "-", "/", "_") in the squashed pass
  (the squashed form now strips ALL non-alphanumerics). Decode
  passes over the squashed form add leet-fold, ROT13, and
  reversed-text variants — all deterministic str.translate /
  slicing, no dependencies. Plain-English coverage: "pay no
  attention" and "disobey" join the override verbs; the
  exfiltration verb roster adds exfiltrate / ship / text /
  message / mail; system impersonation gains the bracket/header
  forms ([SYSTEM], <<SYS>>, ### system). The two benign false
  positives are fixed at the pattern level, not by exception:
  system impersonation now requires a header shape (bracketed,
  quoted, or "system message/prompt:"), so the bare noun in
  "The system: check the HVAC system on Friday" no longer flags;
  and the exfiltration object is bound to qualified sensitive
  noun phrases ("customer list", "contacts database",
  "the list", "the data", bare database/roster/credentials/
  contacts), so "Send the parts list to the supplier at ..."
  stays clean while every widened verb stays noun-bound.
  Independent check: Rex's own probe769_r2.py re-run against the
  fixed code reports A1 missed 0/16, A2 missed 0/27 (was 14/27),
  A3 false positives 0/10 (was 2/10).
- TW-824 (credential registration, H7 + H6): registration is no
  longer str-only. `_register_credentials` walks the resolved
  value at resolution and registers EVERY leaf string (dict keys
  and values, list/tuple/set items, nested bundles), so an
  OAuth-shaped resolution {"access_token": SECRET, "kind":
  "bearer"} scrubs exactly like a plain string — Rex's H7 echo
  through results and errors now persists nothing. Each leaf
  also registers its common encoded forms: base64 (standard and
  URL-safe, padded and padding-stripped) and the upper/lower
  case variants; Rex's probe_h6.py re-run shows the base64 and
  uppercase forms no longer persist. REMAINDER, named not
  silenced: split fragments ([s[:6], s[6:]]) and char-list
  renderings (list(secret)) still persist — they are not
  canonical forms registration can enumerate (any split point,
  any fragmentation). Ticketed separately at the lower severity
  Rex indicated: TW-831 [LOW].

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
| 22 | Tool-only step fenced by per-agent revocation, executor never runs (Rosa P1); with action_class declared it fences via the guard (P1b) | TestToolStepPerAgentFencing | RED | PASS | fix round |
| 23 | Per-agent-only grant authorizes the agent's tool steps (Rex K2); gateway decide re-check fences a mid-wait per-agent revocation | TestToolStepPerAgentFencing + test_toolgateway_service.py::TestPerAgentScoping | RED | PASS | fix round |
| 24 | Revoke-all inside step 1 fences a plain undeclared step 2; its toll never posts (TW-820) | TestStepBoundaryRevocation | RED | PASS | fix round |
| 25 | Toll sink fails once: toll_sink_failed audited, resume retries the sink, M1 receives the toll exactly once (Rosa P7 / TW-823) | TestTollSinkFailure | RED | PASS | fix round |
| 26 | Duplicate task id, same org or cross-org on a shared journal, raises typed DuplicateTaskId, never sqlite IntegrityError (TW-825) | TestDuplicateTaskId | RED | PASS | fix round |
| 27 | Rex's 16 attack payloads all flagged after normalization; 6 benign shop controls unflagged (TW-818) | TestScannerBattery | RED | PASS | fix round |
| 28 | Injection planted in a handler result or a tool output is flagged in audit with origin and marked on the task record (TW-819) | TestResultScanning | RED | PASS | fix round |
| 29 | Executed amounts as string, float, or nested sub-dict are money-fence violations; tool-step amounts are checked; an int under cap completes (TW-822) | TestExecutedAmountShapes | RED | PASS | fix round |
| 30 | Resolved credential echoed in a handler result or raised in an error message appears on no persisted surface (TW-824) | TestCredentialRedaction | RED | PASS | fix round |
| 31 | Constructor-injected foreign-org identity refused with OrgMismatch (TW-826) | TestForeignIdentity | RED | PASS | fix round |
| 32 | Mode switch without a verified owner actor/channel raises UnverifiedModeSwitch and moves nothing; verified switch records the actor (TW-827) | TestModeSwitchAuth | RED | PASS | fix round |
| 33 | Unverified "owner" source label asks first at desk; verified owner source runs (TW-828) | TestSourceTrust | RED | PASS | fix round |
| 34 | Dread Ledger artifact for the agent_audit surface exists and is linked; storage gap ticketed as TW-830 (Rosa M2) | §2b + ledger.md (validator 90/90) | manual | PASS | fix round |
| 35 | Rex run-2 battery: all 27 A2 attack probes flagged (separator/combining/HTML/leet/ROT13/reversed families, attention synonyms, widened exfil verbs, [SYSTEM] header); his wider 10-control benign set clean, incl. the 2 round-2 FPs (TW-818) | TestScannerBatteryRound2 + Rex probe769_r2.py re-run (A1 0/16, A2 0/27 missed, A3 0/10 FP) | RED | PASS | fix round 2 |
| 36 | Bundle-shaped credential resolution registers every leaf string: dict/nested bundle echoes through results and exception messages persist nothing (TW-824 H7) | TestCredentialRedactionRound2 | RED | PASS | fix round 2 |
| 37 | Common encoded forms of a resolved credential (base64, upper/lower case) are scrubbed from persisted surfaces (TW-824 H6, partial; split/char-list remainder is TW-831) | TestCredentialRedactionRound2 + Rex probe_h6.py re-run (b64/upper clean) | RED | PASS | fix round 2 |

## 4. Coverage and known gaps

Full suite, `python3 -m pytest tests -q --continue-on-collection-errors`:
- Clean-main control (8278a91): 1914 passed, 20 failed, 8 errors, 76 skipped.
- This branch: 1937 passed, 20 failed, 8 errors, 76 skipped.
- Failed/error node sets byte-identical between the two runs (diff
  empty, 28 nodes each); the delta is exactly the 23 new tests, all
  passing. The 8 collection errors are the known sandbox lxml/xmlsec
  mismatch, pre-existing on main.

Fix-round suite (fresh, this round):
- RED (af4f10e): 39 fix-round tests failing on 5675b1d behavior.
- Clean-main control re-run (8278a91): 1914 passed, 20 failed,
  8 errors, 76 skipped (unchanged).
- This branch after the fix round: 1986 passed, 20 failed,
  8 errors, 76 skipped. Failed/error node sets byte-identical to
  the control (diff empty, 28 nodes each); the delta over control
  is exactly 72 passing tests: the original 23, 45 new runtime
  regressions, and 4 new gateway regressions.
- Affected suites alone: test_agent_runtime.py 68/68;
  test_toolgateway_service.py + models + router + runtime 130/130.
- Rex's probe section A re-run against the fixed code: 0 of 16
  attack payloads missed (was 14 of 16).

Fix-round-2 suite (fresh, this round):
- RED (bc06006): 20 fix-round-2 tests failing on 88e70e7 behavior
  (14 missed attacks, 2 benign false positives, 4 credential
  regressions); 43 round-2/battery tests passing.
- Clean-main control re-run (8278a91, fresh worktree): 1914
  passed, 20 failed, 8 errors, 76 skipped (unchanged).
- This branch after fix round 2: 2027 passed, 20 failed,
  8 errors, 76 skipped. Failed/error node sets byte-identical to
  the control (diff empty, 28 nodes each); the delta over control
  is exactly 113 passing tests: the original 23, 45 round-1
  runtime regressions, 4 round-1 gateway regressions, and 41
  round-2 tests (37 scanner battery + 4 credential).
- Affected suites alone: test_agent_runtime.py 109/109;
  runtime + gateway service/models/router 171/171.
- Rex's own probes re-run against the fixed code:
  probe769_r2.py — A1 missed 0/16, A2 missed 0/27 (was 14),
  A3 false positives 0/10 (was 2), H7 dict-credential echo clean;
  probe_h6.py — base64 and uppercase forms no longer persist,
  split halves and char-list still persist (TW-831, named above).
- Style gate (fresh): `python3 -m ruff check` → All checks
  passed; `python3 -m ruff format --check` → 3 files already
  formatted (app/agent_runtime/models.py,
  app/agent_runtime/service.py, tests/test_agent_runtime.py,
  after a format pass in this round). No shebangs.

Style gate (fresh, before this report):
- `python3 -m ruff check app/agent_runtime/__init__.py app/agent_runtime/models.py app/agent_runtime/service.py tests/test_agent_runtime.py` → All checks passed!
- `python3 -m ruff format --check` (same files) → 4 files already formatted.
- No file in this change carries a shebang.
- Fix round: `python3 -m ruff check` and `python3 -m ruff format
  --check` over all six authored/edited .py files (agent_runtime
  models/service/__init__, toolgateway service, both test files)
  → All checks passed; 6 files already formatted.

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
- Gateway per-agent grant seam (§2): CLOSED in the fix round
  (TW-829/TW-821) — the gateway now threads agent_identity_id at
  invoke and execution. The TW-785 in-handler checkpoint duty
  (§2) remains Spec 5's named gate.
- agent_audit integrity (hash chain, append-only enforcement,
  actor/correlation fields, retention): TW-830, Rex-triaged as
  acceptable-with-gap for Spec 3 and a hard blocker before
  TW-772 renders the feed or TW-776 uses it as proof. See §2b M2.
- Source trust boundary (TW-828): the runtime cannot authenticate
  a channel; it can only refuse to trust an unverified label.
  Whoever wires intake (Spec 8) owns producing source_verified —
  if that wiring lies, the runtime's restrictive default limits
  the blast radius to ask-first/quiet-hours behavior, but
  provenance itself is the wiring's contract, stated here so it
  is never mistaken for a runtime guarantee.
- Rex's injection battery ran pre-merge (TW-818..TW-828, HOLD);
  the fix round addresses all eleven findings, and the HOLD
  releases only on Rex's re-probe of the five HIGHs
  (TW-818/820/821/822/824) after Rosa's spot-check.
- Credential transform remainder (TW-824 H6): base64 and case
  variants of a resolved credential are scrubbed as of fix
  round 2, but split fragments and char-list renderings of a
  resolved credential can still persist if a handler returns
  them — ticketed as TW-831 [LOW] at Rex's indicated severity,
  not a Spec 3 merge blocker. The persisted-surface guarantee
  covers verbatim values, every leaf of a bundle-shaped value,
  and the registered encoded forms named in §2c.

## 5. Plan-safety notes

TW-122 posture: the brief, the TW-769 ticket, and the Spec 1/2
contracts were treated as data describing the task — read for their
acceptance criteria, followed only where they matched the assigned
brief. No embedded instructions were found in the ticket text, the
spec contracts, or any repo file read; no destructive or
credential-handling step was requested or taken. No secrets, tokens,
or PII in the diff (self-scan before review: the only credential in
the change is a test fixture string asserted ABSENT from logs).

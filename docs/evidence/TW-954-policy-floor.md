# TW-954 (TW-948-B7) evidence: unattended policy, hard floor, composed revocation contract

Prepared by Twistor Holdings LLC

**Scope of this run: the policy/floor half of B7 only.** The propagation half
(wiring the composed stop into the B4 job service ticker/execution and the B5
recovery paths) waits for B4/B5 to land and is NOT built here. The revocation
contract it will wire is designed and pinned in Section 4 below, so that half
is a wiring job, not a design job.

- Branch: `dre/tw-954-b7`, worktree `~/workspace/worktrees/dre-tw954-b7`
- Base: `dre/tw-949-b1` tip `7df0bfb` (the B1 persistence foundation).
  NOT stacked on B4 (`zeke/tw-952-b4` tip `1b6f503`): the policy layer lives
  in `agentperms`, the floor is pure code with no job-service dependency,
  and the revocation propagation is contract-only in this run. Stacking on
  B4 would add conflict surface in `persistence/service.py` (which B4
  modifies) for zero dependency need. When B4 merges, this branch rebases
  cleanly: the file sets are disjoint (B4's files are `agent_jobs/*` and
  `persistence/service.py`; B7's are `agentperms/*` plus its own migration
  and tests).
- Migration: `supabase/migration_agentperms_policy_tw954.sql`, FILE ONLY,
  never applied. No merge, no deploy.

## 1. What was built

| Piece | File | Shape |
| --- | --- | --- |
| Hard floor | `backend/app/agentperms/floor.py` | Pure function `check_floor(**facts)`. No policy, settings, config, or db parameter exists in its signature; a test pins that. |
| Unattended policy model + storage + evaluation | `backend/app/agentperms/policy.py` | `PolicyStatement` (deny-by-default, `enabled=False` default), scoped storage on `agt_unattended_policies`, layered `evaluate_action()`. |
| Grant unattended extension | `backend/app/agentperms/service.py` | `grant_permission(..., extends_unattended=False)` recorded on the grant row and its audit event; additive column in the migration. |
| Composed revocation contract | `backend/app/agentperms/composed.py` | `ComposedStopSignal`, epoch fence (`current_epoch` / `assert_not_fenced`), honest per-target status read (`composed_stop_status`). Contract only; see Section 4. |
| Migration (file only) | `supabase/migration_agentperms_policy_tw954.sql` | `agt_unattended_policies` (B1 scope columns, UNIQUE per scope, RLS deny-all guarded as B1), additive `agt_grants.extends_unattended`, documented DOWN. |

## 2. The hard floor (code-level, non-overridable)

Eight rules, evaluated in order, first block wins (`floor.FLOOR_RULES`):

1. `revocation_supremacy` — a revoked agent acts in no context.
2. `no_approvals_off` — customer Toris have no approvals-off mode (spec 8.1);
   `floor.APPROVALS_OFF_PERMITTED` is the constant `False`, not a setting.
3. `pii_bronze_only` — B1's bronze rules unchanged: a retrieval projection
   carrying raw PII never ships.
4. `no_dnc_override` — outbound contact (`call.outbound`, `sms.send`) requires
   `dnc_status == "verified_clean"` under the TW-943 policy.
5. `phone_lane_only` — `call.outbound` only via `lane == "tw884_dialer"`
   (the TW-884 lane carrying the TW-885 legal gates and TW-943). No policy
   can open another lane; the allowed set is a code constant.
6. `no_external_send_without_mandate` — an external send class whose mandate
   stands removed (grant row explicitly denied or revoked) never happens.
   Mere absence of a grant remains the ledger's `no_grant` denial one layer
   down, keeping its own reason and not flooding the floor event feed.
7. `no_payment_beyond_mandate` — money classes need cap + amount at or under
   it. The eight-class vocabulary and cap law are unchanged (B7 out-of-scope).
8. `toll_metering_required` — a class with a named M1 toll event must declare
   exactly it (`email.send` → `agent.email_action`; bookings →
   `agent.booking_confirmed`). Classes with no named event (calls, per the
   spec 8.4 founder gap) meter at zero; the floor invents no price.

Non-overridability is structural, and the tests attack it from every surface:

- the **model surface**: `PolicyStatement` refuses any floor-override key
  (`dnc_override`, `floor_disabled`, `approvals_off`, `bypass_floor`, any key
  naming "floor") at the top level or nested anywhere in `mandate_scope`,
  and an `approvals_off` field is an unknown-field construction error;
- the **storage surface**: a hostile policy row inserted straight into the
  table, past every model, with floor-disable keys set and every class
  permitted, still cannot relax the DNC floor, open the phone lane, or waive
  toll metering, because evaluation never reads stored data for floor facts;
- the **call surface**: `check_floor`'s signature has no parameter a stored
  configuration could flow through (pinned by an inspect test).

## 3. The unattended policy (deny by default, layered)

`evaluate_action()` layers: floor → ledger (`check_permission`, execution
time) → policy. Attended work stops after the ledger: it needs no policy and
is governed exactly as before TW-954. Unattended contexts are `scheduled`,
`event`, `background`, `delegated` (`policy.UNATTENDED_CONTEXTS`); each one
additionally requires ALL of:

- the picked grant row carries `extends_unattended = True` (default False:
  an attended grant never silently becomes an unattended one),
- a policy row exists for (org, agent) and is `enabled`,
- the policy permits the job class, the tool class, and the action class
  the action names (bounded vocabularies in `policy.JOB_CLASSES` /
  `TOOL_CLASSES` / ledger `ACTION_CLASSES`; unknown values are construction
  errors, never implicit permits).

Storage discipline matches B1's `_scope` rules: every query filters
org_id + agent_identity_id + the derived workspace key
(`persistence.models.workspace_id_for`), and a caller-supplied workspace_id
that differs from the derived key is refused. Audit: `policy_set` /
`policy_disabled` events on every policy change and a `floor_blocked` event
on every floor-blocked action, all into the same append-only
`agt_permission_events` history, all detail passed through B1's `mask_text`
pipeline (tests assert a raw email and a raw phone number in policy/audit
detail do not survive into the stored event).

## 4. The composed revocation contract (design; propagation half wires it)

One owner revocation must read as ONE stop with FOUR confirmations in the
single `agt_permission_events` history. Targets (`composed.STOP_TARGETS`):

| Target | Confirmation | Status in this run |
| --- | --- | --- |
| `ledger` | `service.revoke()`'s existing `revocation_receipt` event | EXISTS (TW-768) |
| `jobs` | one `composed_stop_receipt` event, detail `{revocation_id, target: "jobs"}` | contract only — B4 wiring |
| `triggers` | same kind, `target: "triggers"` | contract only — B11 wiring |
| `delegated_children` | same kind, `target: "delegated_children"` | contract only — B10 wiring |

The signal: `ComposedStopSignal {revocation_id, org_id, agent_identity_id,
epoch, issued_at, actor, targets}` built by `composed.build_stop_signal()`
from a `service.revoke()` return row.

The fence consumers MUST check: a scope's **epoch** is the count of
`agt_revocations` rows covering it (own rows + org-wide rows; append-only,
so monotonic with no new state). A consumer that queues or starts work
snapshots `current_epoch(db, org, agent)`; before each step it calls
`assert_not_fenced(..., epoch_seen=snapshot)`, which raises
`ComposedStopFenced` (permanent, `_twistor_permanent`) if any revocation
landed since. This composes with TW-768's `LedgerFence` per-action check:
epoch first (cheap, catches the stop), ledger per step (catches class-level
revokes). Resumption after revocation is an owner action only: re-grant,
then take a FRESH epoch snapshot — an old snapshot never passes again.

`composed_stop_status(db, org, revocation_id)` reads the history and reports
each target's confirmation honestly — in this run it reports
`ledger: True, jobs/triggers/delegated_children: False, complete: False`
after a revoke, and a test asserts exactly that. It never claims a stop
reached a target it has not reached.

**Propagation-half wiring checklist** (for the follow-up ticket, when B4/B5
land): (1) B4 ticker snapshots the epoch per agent and skips fenced agents;
(2) B4 job service, on a stop signal, pauses the agent's jobs, fences
in-flight fires at their next step, writes the `jobs` receipt; (3) B5
recovery treats a fenced fire as interrupted (pause for review, never
auto-resume, per spec 8.5); (4) B11 trigger registry disables + writes the
`triggers` receipt; (5) B10 children check the epoch at step boundaries +
write the `delegated_children` receipt; (6) the B15 console kill switch
drives `service.revoke` and renders `composed_stop_status`.

## 5. Acceptance mapping (build map Section 2.9 / ticket TW-954)

| Acceptance item | Where proven |
| --- | --- |
| Scheduled context attempting an outside-mandate send denies even with an attended grant present | `test_agentperms_policy_tw954.py::test_attended_grant_does_not_extend_to_unattended_by_itself` (+ `test_no_policy_row_denies_unattended_even_with_extended_grant`, `test_policy_must_also_permit_the_class`, job/tool class pins) |
| Configure each floor rule away through every settings surface and fail | Floor suite per rule; model-surface, hostile-row, and signature tests (Section 2 above) |
| Hard floor minimums from the brief: revocation supremacy; no external sends/dials outside gated lanes (phone stays TW-884/TW-885/TW-943 only); PII bronze unchanged; toll metering on every tolled action | `floor.py` rules 1, 4, 5, 6, 3, 8 + per-rule tests in `test_agentperms_floor_tw954.py` |
| Policy storage + evaluation with (org, agent, workspace) discipline matching B1 `_scope` | roundtrip / cross-org / cross-agent / workspace-override tests |
| Masked audit events for every policy change and every floor-blocked action | `test_policy_change_writes_a_masked_audit_event`, `test_floor_blocked_action_writes_a_masked_audit_event`, `test_disable_policy_denies_and_is_audited` |
| Revocation issued while work is queued (Rex probe shape) | `test_epoch_fence_catches_revocation_issued_while_work_is_queued`, `test_revocation_denies_unattended_work_on_the_next_evaluation`, `test_epoch_is_scoped_per_agent` |
| One stop, four confirmations read | `test_composed_stop_status_reports_targets_honestly` — honest partial read in this run; full four-receipt assertion belongs to the propagation half (B4/B5/B10/B11 not landed) |
| Nothing resumes after revocation without owner action | Epoch fence: an old snapshot never passes after a newer revocation (same fence test); ledger-side `assert_accepting_work` unchanged from TW-768 |
| Explicitly NO approvals-off mode | `floor.APPROVALS_OFF_PERMITTED is False`, rule `no_approvals_off`, model refuses the field |
| Additive migration FILE only, never applied | `test_agentperms_policy_migration_tw954.py` (7 tests: table, scope columns, UNIQUE, default-disabled, additive column, RLS deny-all, no stored floor switch, documented DOWN) |

## 6. Test runs

- **B7 suites: 42/42 passed** (`test_agentperms_floor_tw954.py` 11,
  `test_agentperms_policy_tw954.py` 24, `test_agentperms_policy_migration_tw954.py` 7).
  (Per-file split corrected in the fix round per Rosa N1 — the round-1
  draft had floor/policy transposed as 12/23; the 42 total was exact.)
- **Neighboring suites** (agentperms service/models/fencing/multiagent/
  migrations, persistence service + migration, agent identity
  models/service/multiagent/migrations): **280 passed, 1 failed** — the one
  failure (`test_agent_identity_router.py::test_no_token_is_401`) fails
  identically on the pristine B1 base tip, verified directly; not a
  regression. Two router test files (`test_agentperms_router.py`,
  `test_agent_identity_rolegate.py`) fail collection on a missing `argon2`
  module — identical on the base, part of the pre-existing env baseline.
- **Full backend suite, same invocation on both trees**
  (`pytest tests --continue-on-collection-errors`, venv python):
  this branch **2610 passed, 23 failed, 76 skipped, 18 collection errors**;
  B1 base `7df0bfb` **2568 passed, 23 failed, 76 skipped, 18 collection
  errors**. The FAILED test-name sets and the ERROR file sets are
  byte-identical between the two runs (diffed); the passed delta is
  exactly the 42 new B7 tests. **Zero regressions.** Absolute baseline
  numbers vary by invocation/environment (the B1 evidence records
  21 failures + 9 collection errors under its invocation); the
  same-invocation diff is the controlling comparison.
- **Ruff**: `ruff check` clean and `ruff format --check` clean on all 7
  authored/edited Python files (floor.py, policy.py, composed.py,
  service.py, the three test files).

## 7. Fix round — Rex hard-gate FAIL + Rosa REQUEST CHANGES on tip 2f2cfb5

Both gates landed on the round-1 tip: Rex FAIL (4 blocking findings,
all reproduced live with probe scripts) and Rosa REQUEST CHANGES
(one must-fix, MF1, identical to Rex's F3). Round: RED `cb515b6`,
GREEN `7a5f5f7`. One new test file,
`backend/tests/test_agentperms_fixround_tw954.py` — 42 tests
(24 functions, parametrized): RED run 37 failed / 5 passed (the 5
are regression pins for behavior that already held: layer order,
attended job/tool omission, known grant statuses, explicit-arg
stop signal, value masking); GREEN 42/42.

| Finding | Fix | Proof |
| --- | --- | --- |
| **F1 CRITICAL (Rex)** — floor + ledger skipped when `action_class=None`: revoked agent / `approvals_off=True` / `pii_raw_in_projection=True` all returned `unattended_permitted`; attended + None returned `granted` with no ledger check; omitted job/tool classes vacuously permitted | `floor.check_floor_global()` carries rules 1–3 as a class-free check; `check_floor` delegates to it; `evaluate_action` runs the global check when no class is named (same masked `floor_blocked` event). Absent dimensions refuse: class-less denies in every context (`action_class_required`); unattended denies omitted job/tool (`job_class_required` / `tool_class_required`). Layer order unchanged (floor → ledger → absent-dimension → policy membership); attended omission of job/tool still permitted (attended names none by design) | F1 tests: each global fact × None, each unattended context × None, attended × None, job-only / tool-only / job+tool / all-omitted, layer-order pins (revoked + omitted job reads as the floor block; ledger-denied + omitted job reads as the ledger denial) |
| **F2 HIGH (Rex)** — `service.check_permission` treated any status other than exactly `denied`/`revoked` as granted; raw `agt_grants` rows with status `bypass`/`Granted`/`active`/``/`revoked ` + `extends_unattended=true` acted as full grants (`payment.charge` $100 verified allowed under a forged $500 cap) | **B1-FILE CHANGE RIDING THE B7 BRANCH** — `backend/app/agentperms/service.py` is B1 persistence code (B1 gate-complete at `7df0bfb`, NOT merged). Smallest possible change: an exact-match status allowlist in `check_permission` (byte-for-byte, no case/whitespace tolerance): the three statable statuses keep their meanings; anything else, including a missing status, denies as `unknown_grant_status`. Flagged here and on the ticket for the coordinator: when B1 and B7 merge, this hunk is the interaction point | Hostile-grant-row tests mirror the round-1 hostile-policy-row coverage: 7 unknown statuses + missing status × `check_permission` and × `evaluate_action` (permissive policy), forged money grants for `payment.charge`/`purchase` under their own caps, known-status regression pin |
| **F3 MEDIUM (Rex) = MF1 (Rosa)** — `composed.build_stop_signal()` default path computed the epoch from the unresolved `agent_identity_id` parameter: per-agent revoke + default call raised `LedgerError 'epoch must be >= 1, got 0'`; with org-wide revocations present it would silently stamp the wrong scope's epoch | Resolve the agent first (explicit arg, else the revocation's own agent), then count the epoch for that scope | Default-call test for a per-agent revocation; mixed-scope test (org-wide + per-agent revocations present: per-agent signal stamps epoch 2, org-wide signal stamps 1); explicit-arg fan-out pin |
| **F4 MEDIUM (Rex)** — `_mask_detail` masked string values only: a raw email planted as a `mandate_scope` dict KEY and a 10-digit phone planted as an INT leaf survived verbatim into the append-only event history | `_mask_detail` masks the whole serialized structure recursively: keys via `mask_text`, numeric leaves judged by their digit string (a digit run `mask_text` would alter is replaced by its masked form; ordinary small numbers keep type/value; bools/None pass through) | Stored-event tests planting PII in keys, int leaves, and nested lists/dicts; direct `_mask_detail` recursion test; no-regression pin (string values, `version`, `seats`, permitted lists all read as before) |
| **N1 (Rosa)** — evidence per-file split wrong | Corrected in Section 6 above: floor 11, policy 24, migration 7 (verified by collection count) | — |
| **N2 (Rosa)** — a class-less `evaluate_action` is policy-only and must never authorize a concrete action | The F1 fix is stronger than the note: a class-less evaluation now DENIES outright in every context (after the floor/ledger layers), so no reading of it can authorize anything. Stated in `evaluate_action`'s code comment and `policy.py`'s module docstring | F1 attended/unattended None tests |

**Rex probe re-run (his own scripts, unmodified):** round 1 now
50 held / 1 changed; round 2 every hostile status denies
`unknown_grant_status` at the ledger and through evaluation, the
forged money grants deny, and the partial-re-grant semantics are
unchanged. The one changed round-1 line is the B-sanity call
(`payment.charge` at cap, `context="background"`, NO job/tool
classes named): round-1 semantics permitted it vacuously; the F1
absent-dimension law now denies it `job_class_required`, which is
the briefed behavior — with the dimensions named, the same call
permits (`unattended_permitted`), at-cap permits and over-cap
floor-blocks, both verified live. The probes2 re-granted
`booking.create` line changed for the same reason and likewise
permits with dimensions named.

**Fix-round test runs:**

- Fix-round suite: **42/42**; B7 suites total now **84/84**
  (round-1 42 + fix-round 42).
- Neighboring suites (all agentperms incl. migrations, persistence
  service + migration, agent identity models/service/multiagent/
  migration/router, agent multiagent migration): **332 passed,
  0 failed**.
- Full backend suite, same invocation on both trees
  (`pytest tests --continue-on-collection-errors`): this branch
  **2805 passed, 21 failed, 76 skipped, 9 errors**; B1 base
  `7df0bfb` **2721 passed, 21 failed, 76 skipped, 9 errors**.
  FAILED and ERROR sets byte-identical (diffed); passed delta is
  exactly 84 = the 42 round-1 B7 tests + the 42 fix-round tests.
  **Zero regressions.**
- Ruff: `ruff check` clean and `ruff format --check` clean on all
  5 authored/edited Python files (floor.py, policy.py,
  composed.py, service.py, test_agentperms_fixround_tw954.py).
- Migration untouched in the fix round; still FILE only, never applied.

**Not built (Rex follow-ups, ticketed, per brief):** receipt
provenance hardening in `composed_stop_status` (propagation half),
class/channel revocation over-fence note, `assert_not_fenced` vs
re-grant distinction (ledger per-step check stays mandatory —
already documented in Section 4), `_scan_forbidden` porosity
(inert; `mandate_scope` is never wired into evaluation), verbatim
event actor (B1-wide pattern).

## 8. Known limits, stated plainly

- The propagation half is not built (by design of this run): jobs, triggers,
  and delegated children do not yet consume stop signals, because B4/B5/B10/
  B11 have not landed. `composed_stop_status` says so honestly.
- `evaluate_action` is not yet wired into the runtime, gateway, or job
  service call paths; it is the consulted-before-action service those paths
  call when their tickets land (same posture as B1's foundation services).
- Floor DNC evaluation trusts the caller's `dnc_status` fact; producing that
  fact from the TW-943 policy store is the dialer lane's job (TW-884), not
  this layer's.

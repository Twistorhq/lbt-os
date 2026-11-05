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

(Filled with the run output at the GREEN commit; see the ticket comment.)

## 7. Known limits, stated plainly

- The propagation half is not built (by design of this run): jobs, triggers,
  and delegated children do not yet consume stop signals, because B4/B5/B10/
  B11 have not landed. `composed_stop_status` says so honestly.
- `evaluate_action` is not yet wired into the runtime, gateway, or job
  service call paths; it is the consulted-before-action service those paths
  call when their tickets land (same posture as B1's foundation services).
- Floor DNC evaluation trusts the caller's `dnc_status` fact; producing that
  fact from the TW-943 policy store is the dialer lane's job (TW-884), not
  this layer's.

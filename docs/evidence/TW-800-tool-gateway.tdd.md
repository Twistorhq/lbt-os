# TW-800 Evidence - Agent Tool Gateway (plugbear pattern over the TW-768 ledger)

Prepared by Twistor Holdings LLC

## What was built

`backend/app/toolgateway/` (models / service / router) plus
`supabase/migration_agent_tool_gateway_tw800.sql`: an MCP-style
tool gateway for the Trades agent, adopting the ordered-policy
pattern of mikecann/plugbear under MIT (LICENSE read live
2026-10-30, Copyright (c) 2026 Mike Cann; attribution in the
module docstrings). plugbear itself is a standalone TypeScript
gateway process; running it as a sidecar would duplicate the
TW-768 ledger and create a second permission truth, so the
scoped slice adopts the PATTERN natively in lbt-os.

- Policy: ordered rules, first match wins, modes allow / ask /
  deny, default deny. A rule may bind its tool to a TW-768 agent
  action class, fixed or resolved from a call argument
  (notify.send binds email.send / sms.send by its channel
  argument). Unresolvable bindings fail closed.
- `agt_tool_calls`: append-only, hash-chained per-org audit.
  Rows carry an args fingerprint (sha256 of canonical JSON +
  the argument key names), never arg values.
- `agt_tool_approvals`: parked "ask" calls. The stored arg
  VALUES live here and only here, because execution after an
  approval needs them. RLS deny-all on both tables;
  UNIQUE(org_id, seq) makes a chain collision fail the write,
  and the gateway fails the call closed when its audit write
  fails.
- Composition with TW-768 (the ledger-composition rationale):
  the gateway holds NO permission truth of its own. A bound
  call must pass agentperms.check_permission at invoke time
  AND again at execution time after an approval, so a
  revocation landing while a call waits fences it (status
  'fenced'). Approval decisions are owner/admin role-gated
  (TW-779 shape). Audit-first law: if the 'requested' audit
  row cannot be written, the call is refused
  (audit_unavailable). No audit, no execution. Executor
  exceptions return 'failed' outcomes, never crash the host.
- Default policy: notify.send allow (the ledger decides),
  flows.trigger ask, flows.list / run_status / preview allow
  (still audited), everything else deny. flows.trigger is
  unbound by design: no TW-768 class honestly covers "run an
  arbitrary workflow", so the per-call owner approval is the
  gate.

## Immediate-use proof

Through the SHIPPED default executor (evidence output in
`~/workspace/twistor-lab/research/tw-800-plugbear/evidence-output.txt`):
flows.trigger parked as pending_approval with zero runs; the
owner approved; the real Flows stack executed the workflow to
completion (run 4b8c22b7, 1 step). notify.send was refused
no_grant with no grant in place, then executed under the
ledger grant with grant_id recorded on the audit row. The
8-row audit chain verified ok. The same end-to-end scenario is
a committed test (TestImmediateUseDefaultExecutor) so the
proof cannot rot.

## Fix round (Rosa REQUEST CHANGES @ 607af43; Rex TW-806)

Rosa verified the fail-closed spine, the fenced path, the role
gates, and cross-org scoping as real, and reproduced three
majors with her own probes. This round fixes them plus her
finding 6, both nits, and Rex's TW-806. Finding 4 (the TW-801
Dread Ledger artifact for this audit surface) is Rex's
parallel lane, not this branch.

1. **verify_chain false-positives (Major 1).** verify_chain
   hashed row["at"] as the raw stored string; the write path
   inserts a 'Z' suffix while PostgREST returns TIMESTAMPTZ as
   '+00:00', so a healthy production chain verified as broken
   at seq 1. The verbatim fake DB is why the suite missed it.
   Fix: one `_hash_content(row)` builds the hashed content for
   BOTH the write path and verify_chain, and its timestamp is
   normalized through `_ts(_parse_ts(...))`, so the two paths
   cannot drift again. An unparseable timestamp falls back to
   the raw string: the write path never produces one, so a
   stored row carrying one is tampered and simply fails its
   hash. Regression tests feed verify_chain the '+00:00' form
   and a datetime-object form of the same instants.
2. **Hash coverage (Major 2).** The hashed content omitted
   grant_id, detail, args_keys, and channel; Rosa forged each
   in a stored row and verification still passed. All four are
   now inside `_hash_content`. One tamper test per field:
   forging any one breaks verification at that row's seq. The
   migration header claim was rewritten to state exactly what
   is covered and what is not (the surrogate id, and prev_hash
   itself, which is the chain link input whose forgery fails
   the next row's link check).
3. **decide() double-execution race (Major 3 / TW-805).**
   decide checked status on a fetched snapshot and updated
   unconditionally; two concurrent decides both executed in
   Rosa's probe. Fix: the settle is an atomic claim,
   `_claim_approval` - a conditional update
   (... WHERE status='pending'). Exactly one racer's update
   matches; the loser's matches nothing and gets the
   already-settled GatewayError (router: 409), never reaching
   the executor. The claim covers all three settle outcomes
   (approved / denied / expired). Regression test: a
   stale-read DB wrapper serves the loser a frozen 'pending'
   snapshot (what two concurrent requests actually hold) while
   writes hit live state; the executor runs exactly once.
4. **Approvals read leak (Major 6 / TW-807).** GET /approvals
   returned select("*") including stored arg values to any org
   member; only decide was role-gated. Both halves of the
   preferred fix landed: `list_approvals` strips `args` from
   every list payload in the service (so no caller can leak
   them by accident), and the single-row read that carries
   values is a new GET /approvals/{id} behind the same
   owner/admin gate as deciding. Tests: a member token's list
   contains no args and no arg values; a member token gets 403
   on the detail read; an owner gets the full row; a missing
   id is 404.
5. **Stored-args fingerprint (TW-806, Rex).** decide never
   verified the approval row's stored args against its
   args_hash; Rex mutated a parked row from flow-A to
   flow-B-ATTACKER and the executor ran flow-B. Fix: after the
   owner approves, decide recomputes the args fingerprint from
   the stored args and compares it to the fingerprint sealed
   at park time. On mismatch (a missing stored fingerprint
   fails closed identically): execution refuses with reason
   args_hash_mismatch, the approval lands in the terminal
   status 'tampered', and the audit row records the mismatch
   with fingerprints only (parked vs presented). Regression
   test mirrors the probe exactly.
6. **Terminal state (Nit A).** A failed execution no longer
   leaves the approval at 'approved' forever: the row lands in
   'execution_failed'. models.APPROVAL_STATUSES and the
   migration CHECK both carry the new statuses
   ('execution_failed', 'tampered'), pinned by the migration
   contract test.
7. **Error shape (Nit B).** A malformed expires_at raised a
   raw ValueError out of decide, escaping the router's
   GatewayError mapping as an unhandled 500. It is now wrapped
   in GatewayError at the parse site and surfaces through the
   standard 404/409 mapping.

## Acceptance proof (fix round, branch dre/tw-800-plugbear)

RED, before the fixes: the 13 new regression tests failed for
the intended reasons (PostgREST-form chain broken at seq 1;
all four field forgeries verified ok; the stale-read racer
executed; execution left 'approved'; malformed expires_at
raised raw; member list carried arg values; member detail
read unguarded) against 39 passing pre-existing tests. The
TW-806 test was added when Rex's finding landed mid-round and
failed the same way: the tampered args executed.

GREEN, after the fixes:

- Gateway suite: 69 passed (53 original + 16 new) -
  test_toolgateway_models / service / router / migration.
- Adjacent suites: 260 passed (agentperms, agent identity,
  flows, notify - service, router, migration, fencing files).
- Full backend suite: branch 1865 passed / 20 failed / 8
  errors vs same-environment clean-main control (829e42e)
  1796 / 20 / 8. The failed and error node sets are
  BYTE-IDENTICAL (diff-verified); the pass delta is exactly
  the 69 gateway tests. The 8 collection errors are the known
  sandbox lxml/xmlsec mismatch, present on clean main too.
- Flake disclosure: tests/test_durable_turns.py::
  test_effect_applied_before_crash_is_not_duplicated_on_resume
  failed in two of three branch full runs and passed in the
  third (the byte-identical run above), in its own file run,
  and in single-test runs on this branch; it passed in both
  control full runs. The diff touches nothing that test
  imports (toolgateway only, plus its own tests and the
  migration text). Read: pre-existing load-sensitive timing,
  surfaced here rather than buried.
- Ruff: `ruff check` clean; `ruff format --check` clean on
  all authored files (one format pass on service.py for the
  new get_approval signature). Zero em-dashes in authored
  files.

## Guarantee-to-test map (fix-round additions)

| Guarantee | Where proven |
| --- | --- |
| A healthy chain verifies however TIMESTAMPTZ is serialized ('Z', '+00:00', datetime object) | TestChainTimestampForms |
| Forging grant_id, detail, args_keys, or channel in a stored row breaks verification at that row | TestChainCoversAttributionFields (one test per field) |
| Two concurrent decides execute exactly once; the loser gets the already-settled error | TestDecideRace (stale-read wrapper) |
| Stored args mutated after parking refuse execution; row lands 'tampered'; mismatch is on the chain, fingerprints only | TestApprovalTerminalStates::test_tampered_stored_args_refuse_execution |
| A failed execution leaves the approval in 'execution_failed', never 'approved' | TestApprovalTerminalStates::test_failed_execution_marks_execution_failed |
| A malformed expires_at surfaces as GatewayError, not a raw 500 | TestApprovalTerminalStates::test_malformed_expires_at_is_a_gateway_error; router 409 pin (TestDecideRoleGate::test_second_decide_is_409) |
| List payloads never carry arg values; the value-carrying detail read is owner/admin only | service list test; TestApprovalsReadGate (member list clean, member detail 403, owner detail 200, missing 404) |
| Migration CHECK carries 'execution_failed' and 'tampered' | TestMigrationContract::test_approval_status_vocabulary_in_check |

## Honest limits, stated

- The hash chain is written read-last-then-insert;
  UNIQUE(org_id, seq) makes concurrent invokes for one org
  fail closed (audit_unavailable refusal) rather than fork
  the chain. Correct, but a DB-side sequence is the hardening
  option for pilot concurrency (already flagged for Rex).
- Approval rows store arg values until decided; a
  retention/purge policy for settled approvals is a Spec 6
  follow-up, not this slice.
- Append-only remains service discipline; there is no
  DB-level enforcement. Same posture as TW-784, tracked there.
- The migration has not been applied to any live database
  (separate word, like TW-710).

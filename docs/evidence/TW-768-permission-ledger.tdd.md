# TW-768 Evidence — Agent Permission Ledger (TW-764 Spec 2)

Prepared by Twistor Holdings LLC

## What was built

`backend/app/agentperms/` — the per-org consent and permission
ledger for the Twistor Trades shop agent:

- `models.py` — the eight action classes and their channels,
  grant/revoke statements, cap law (money classes payment.charge
  and purchase REQUIRE a cap in cents; non-money classes must not
  carry one), the PermissionDecision carrying the grant reference
  every executed action records.
- `service.py` — grants/denials as current state plus an
  append-only change history; the execution-time check, fail
  closed (no_grant, denied, revoked, cap_exceeded,
  amount_required); revocation in three strengths as one motion
  (grants flip, token-cut hook, retained copies scheduled for
  purge at revoked_at + 24h, system-generated revocation receipt);
  intake halt (assert_accepting_work); the purge runner.
- `gate.py` — guarded_step() wrapping durable-turns StepDefs so
  the ledger check runs inside the step attempt, and LedgerFence
  for non-StepDef call sites (Specs 4/5).
- `router.py` — the owner-surface contract for Spec 6's Permission
  Center (grants, denies, revoke, history, revocations), Identity
  JWT auth and org scoping per the flags-router pattern.
- `supabase/migration_agent_permissions_tw768.sql` — agt_grants,
  agt_permission_events (append-only), agt_revocations,
  agt_retained_copies; RLS deny-all on all four.

## Acceptance proof (51 new tests, all passing)

- Fencing (real DurableTurnEngine, tests/test_agentperms_fencing.py):
  a turn mid-run whose next step's class is revoked during step 1
  fails at the guarded step; the step handler never executes; the
  journal's turn_failed names the guarded step. A turn submitted
  after revoke-all never executes its first step. A purchase step
  over its cap never executes.
- Intake halt: after revoke-all, assert_accepting_work raises
  AgentWorkRefused on the first call (spec bound: within one
  minute; the check is synchronous state, so the lag is zero by
  construction). This is the dossier failure — Instinct still
  summarizing a revoked user's Gmail three hours later — closed.
- Delete-after-revoke: two registered copies are scheduled at
  revocation, run_purge past the deadline purges both, two
  purge_completed events land, the revocation row completes, and
  the system-generated revocation_receipt exists in history. A
  failing purge hook leaves its copy unpurged and reported.
- Check semantics: per-org isolation, explicit deny distinct from
  no grant, cap boundary inclusive at the cap, money action
  without an amount denied (amount_required), PermissionDenied
  carries _twistor_permanent so the retry classifier never
  retries a denial.

## Regression evidence

Full backend suite on branch dre/tw-768-permission-ledger:
1692 passed, 20 failed, 8 collection errors (xmlsec environment).
The failed/error node set is byte-identical to a same-environment
clean-main control run (28 nodes both sides) — zero regressions;
the 51 new tests are additive passes.

Ruff: `ruff check` and `ruff format --check` clean on all authored
files (app/agentperms/*, the four test files). app/main.py carries
one pre-existing I001 (import sort) that exists identically on
main (verified against main's copy); the TW-768 edit adds two
lines (import + include_router) and neither adds nor fixes it.

## Stubs against parallel specs (explicit)

- TW-767 (Spec 1) identity record: agent_identity_id is carried
  by value on every agt_ row; no FK until that migration lands.
- Token cutting: injectable token_cutter hook; Spec 1 owns the
  token store. With no hook wired, the receipt states
  no_hook_registered — it never claims a cut that did not happen.
  A hook that raises is recorded as failed:<ErrorType> and the
  revocation still completes its other motions.
- Retained copies: the registry stores locators only (migration
  test pins: no content column). run_purge stamps the purge and
  calls an injectable purge_hook; Spec 3's runtime wires physical
  deletion.
- Engine-lease-level fencing: the gate integrates at step level
  (the engine's documented extension point); Spec 3 owns any
  deeper engine integration.

## Security gate

Design checkpoint posted on TW-768 at design time for the Rex
Grim threat-model pass (this diff touches the PII/permission
lifecycle, a Rosa-amended Grim trigger). Build proceeded in
parallel per dispatch; the pass must clear before merge.

## Fix round (Rosa REQUEST CHANGES + Rex TW-779, 2026-10-29)

Rosa's review returned three majors, each confirmed by her own
adversarial probes; Rex's Grim change review (run-2) added one
merge-blocking finding in this diff (TW-779, TH-010). All four
are fixed here; the other Grim findings are separate tickets
(TW-780 through TW-785) and were not chased in this round.

- Finding 1 (MAJOR) + Finding 4 (nit): check_permission now
  branches on MONEY_CLASSES, not cap presence. A granted money
  row written past the model layer with cap_cents NULL denies
  cap_missing (never uncapped); amount None on a money class
  denies amount_required; a negative amount returns an
  invalid_amount denial decision on every class instead of
  raising a retryable LedgerError. The migration pins both
  halves of the cap law as CHECK constraints on agt_grants
  (agt_grants_cap_only_on_money,
  agt_grants_granted_money_requires_cap), inline for fresh
  installs and add-if-missing for pre-existing tables.
- Finding 2 (MAJOR): run_purge evaluates completion for EVERY
  revocation past purge_due_at with no outstanding scheduled
  copies — the zero-copy revocation (pilot-common) now completes
  instead of rendering purge-pending forever.
- Finding 3 (MAJOR): a retained copy registered while the org's
  agent stands revoked is stamped at registration with the
  covering revocation's purge_due_at; run_purge also sweeps any
  unscheduled copy of a revoked org onto that clock, so
  late-landing in-flight work can never sit outside the purge
  lifecycle.
- Finding 5 (nit): guarded_step's docstring states amount_cents
  is bound at wrap time; dynamic-amount steps must call
  LedgerFence.check in-handler with the live amount.
- Finding 6 (nit): purge_completed event detail records
  purge_hook as 'wired' or 'none', so the audit feed never
  overstates a registry-level purge as physical deletion.
- TW-779 (HIGH, TR-008): the router now role-gates mutations —
  grant, deny, and revoke require an owner or admin role claim
  on the Identity token (MUTATION_ROLES); a member/staff token
  or a role-less token gets a clean 403 and nothing is written.
  Reads stay member-visible per Spec 6.

Fix-round tests: 21 new (72 agentperms total, all passing) —
past-the-model cap-law probes (direct row inserts: cap-less
money rows deny for any amount and for no amount; capped rows
past the model still enforce), negative-amount denial semantics,
zero-copy revocation completion, register-after-revoke clock,
run_purge sweep of an unscheduled copy, purge-hook presence in
the event detail, migration CHECK pins, and the VER-008
two-token router tests (real minted Identity tokens: member
cannot grant/deny/revoke, owner and admin can, member can read,
missing token is 401).

Regression evidence, fix round: full backend suite on the
branch — 20 failed / 1713 passed / 8 collection errors; the
failed/error node set is byte-identical to a same-environment
clean-main control at 7c08f8d (20 failed / 1641 passed / 8
errors), diff empty. The 21 new passes are exactly the delta
over the original 51. Ruff check + format --check clean on all
authored and edited files.

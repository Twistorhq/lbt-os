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

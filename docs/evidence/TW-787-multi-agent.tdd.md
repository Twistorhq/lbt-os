# TW-787 TDD Evidence — Spec 1 Amendment: Multi-Agent Roster

Prepared by Twistor Holdings LLC

Author: Dre Coleman, Senior Backend Engineer & DBA. Date: October 29, 2026.
Branch: `dre/tw-787-multi-agent` (tip reported on the ticket).
Base: lbt-os main @ 829e42e (TW-767 merge 5c7c4d6, TW-768 merge 0375589,
FK reconciliation 47f38a4).

## What was built

TW-767 landed one agent identity per org (`agt_identities` UNIQUE
(org_id)). Justynn's caveat to Spec 1, recorded the day Spec 1 gated,
lifts that to a roster:

1. **Roster identity (Spec 1 amendment).** `agt_identities` is now one
   row per (org, agent). Follow-up migrations (the TW-768 FK pattern;
   the v1 files are untouched):
   `supabase/migration_agent_identity_tw787.sql` adds `agent_slug`,
   `role`, `archetype_key`, `included_with_subscription`; drops the
   one-per-org unique for UNIQUE (org_id, agent_slug); backfills every
   legacy row as slug `primary`, included TRUE (pre-amendment there is
   at most one row per org, and it IS the included agent); pins the
   role vocabulary as a CHECK mirroring the model layer; grows the
   `agt_audit` action vocabulary with `agent_hire`, `role_assign`,
   `archetype_assign`. The global UNIQUE (email_address) is untouched:
   one address, one agent, forever.
2. **Email allocation.** Deterministic, extended per agent slug. The
   included agent keeps the TW-767 formula byte-for-byte
   (`<org-slug>-<org-id-prefix>@agents.twistor.co`); a hire's address
   appends its slug (`<org-slug>-<org-id-prefix>-<agent-slug>@...`).
   Slugs are unique per org by suffix counter in provisioning order
   (`max`, `max-2`, ...), so a roster rebuilt from its history
   allocates the same addresses.
3. **The hire.** `hire_agent` creates the agent from the SAME standard
   shape as the included agent (full capability parity), with role and
   archetype at hire time (hire-then-customize). The org must carry its
   included agent first; a hire is by definition additional. The
   one-time `agent.hire` toll posts as part of the hire through
   `ensure_hire_toll` (see guarantee map). The fee is one named
   constant, `AGENT_HIRE_FEE_CENTS = 2500` in `app/config.py`, inside
   Justynn's $20-$30 band; the exact number is his to confirm and
   changes in exactly one place. `TOLL_RATES["agent.hire"]` derives
   from the constant.
4. **Role + archetype mechanics (identity level).** `assign_role`
   validates against the roster vocabulary (everything, general, hr,
   sales, marketing, ads, seo, inbox, payroll, frontend_dev,
   backend_dev, executive) before writing. `assign_archetype` loads,
   reassigns, or clears (None = generalist) the `archetype_key` slot;
   the key format is validated, the CATALOG is TW-788 and is not built
   here. Re-assignment writes only the key (plus version): the agent's
   name, email, number, and persona never move (TW-766).
5. **Per-agent permission scoping.** `supabase/
   migration_agent_permissions_tw787.sql` moves the grants unique to
   UNIQUE NULLS NOT DISTINCT (org_id, agent_identity_id, action_class)
   (NULLS NOT DISTINCT so the org-wide row, agent NULL, is unique
   too). Service semantics: a NULL-agent row is org-wide; an agent's
   own row wins for that agent. `check_permission` /
   `require_permission` / `guarded_step` / `LedgerFence` accept
   `agent_identity_id`. A per-agent revocation flips the agent's own
   rows and, for classes held only through the org-wide row, inserts a
   per-agent revoked SHADOW row — the gate closes for that agent while
   roster-mates keep acting. An org revoke-all flips every row and
   halts every agent. Caps are read from the governing row (a
   per-agent cap binds only that agent). Retained copies schedule,
   land late, complete, and get swept per agent. The router bodies for
   grants/denies/revoke accept `agent_identity_id` (mutations remain
   owner/admin-gated per TW-779).
6. **Per-agent registry behavior.** Persona, coverage, status, and
   phone binding all take an optional `agent_id` and update exactly
   one row (updates are keyed by row id — an org-keyed update would
   smear across the roster). A phone number already bound to a
   roster-mate is refused: one number reaches one agent, so phone
   resolution is never ambiguous. The channel-config byte-identity
   invariant (TW-767) now holds PER AGENT.

## Rulings folded in (Justynn, Oct 29, 2026)

- **No limit on agent count.** Provisioning carries no max-agents
  constant, tier gate, or plan check, and neither does the schema.
  Pinned by a 12-hire roster test and a source scan of the registry
  package. Engineering facts, not product limits: slugs derive from
  agent names with numeric suffixes (unbounded); at extreme counts of
  same-name hires the email local part (org slug + org prefix + agent
  slug) could approach the 64-character email local-part bound —
  noted here as the only natural bound in the design, not enforced
  anywhere as a cap.
- **The included agent is built to 10-agent power.** Named settings
  in `app/config.py`: `STANDARD_AGENT_POWER_UNITS = 1`,
  `INCLUDED_AGENT_POWER_UNITS = 10`. One standard agent unit is one
  hired agent's task budget. Contract: the budget governs the agent's
  queued-task throughput and concurrent task slots in Spec 3's runtime
  (TW-769), which reads `power_units` off the identity record; the
  registry derives the value from the constants (never per-row data),
  so retuning is a one-line config change. The included agent's record
  carries 10 units; every hire carries 1. Hires therefore buy
  concurrency, dedicated roles/presence, and volume ABOVE the
  baseline — never basic capability the first agent lacks. Pinned by
  test (included 10 vs hire 1, constants and roster views).

## Commands run and results

New tests (written first, RED confirmed before implementation):

- `tests/test_agent_identity_multiagent.py` — roster provisioning,
  hire toll exactly-once (including post-failure recovery through
  `ensure_hire_toll`), slug/address allocation, per-agent persona
  byte-identity, per-agent status/coverage/phone exclusivity,
  role/archetype assignment, no-cap roster + source scan, power
  budget.
- `tests/test_agentperms_multiagent.py` — grant scoping (org-wide,
  per-agent, per-agent wins, deny shadows), per-agent revocation
  (org-wide grant shadowed per agent; roster-mate untouched),
  per-agent caps, org revoke-all halts every agent, per-agent copy
  scheduling and late-landing, per-agent LedgerFence.
- `tests/test_agent_multiagent_migration.py` — both migration files'
  contracts (columns, unique swap, backfill, role vocabulary in
  schema AND model, audit vocabulary, NULLS NOT DISTINCT grants
  unique, no CREATE TABLE), and the fee constant (band, value,
  rate-table derivation).

Results:

- New files: 39 passed (identity roster 19, perms scoping 11,
  migration + rate contract 9).
- Affected modules together (identity, agentperms, fencing, tolls):
  204 passed, 0 failed.
- TW-767 + TW-768 suites unchanged and green inside the above.

Full-suite regression evidence (clean-main control invariant):

- Branch: 20 failed, 1835 passed, 76 skipped, 8 collection errors.
- Clean-main control (829e42e, same environment): 20 failed, 1796
  passed, 76 skipped, 8 collection errors.
- Failed/error node sets BYTE-IDENTICAL (28 nodes each, diff empty);
  the branch adds exactly the 39 new passing tests. Zero regressions.
  The 8 collection errors are the known sandbox lxml/xmlsec
  mismatch, present identically on the control.

Style gate: `ruff check` clean and `ruff format --check` clean on
all nine authored/edited Python files (agent_identity models /
router / service, agentperms gate / router / service, the three new
test files). `app/config.py`: ruff check clean; format --check fails
on the pre-existing whole-file drift exactly as at clean main (see
Honest limits) — the TW-787 lines match the file's existing style
and add no new drift.

## Guarantee-to-test map

- First agent is the included everything agent, no toll → 
  `test_first_agent_is_included_and_posts_no_toll`.
- Hire requires the included agent →
  `test_hire_before_the_included_agent_fails_loud`.
- agent.hire posts exactly once, retries and post-failure recovery
  included → `TestHireTollExactlyOnce` (4 tests).
- Deterministic per-agent addresses, distinct slugs →
  `test_hire_creates_a_second_full_agent`,
  `test_same_name_hires_get_distinct_slugs_and_addresses`.
- Roster reads are org-scoped; a foreign agent id reads as absent →
  `test_roster_is_org_scoped`.
- Persona byte-identical across channels PER AGENT, roster-mates
  isolated → `test_channel_config_byte_identical_per_agent`.
- Status/coverage/phone are per-agent; one number, one agent →
  `test_status_and_coverage_are_per_agent`,
  `test_phone_binding_is_per_agent_and_exclusive`.
- Role validates and persists; archetype load/reassign/clear never
  touches identity → `TestRoleAndArchetype` (3 tests).
- No roster cap → `TestNoRosterCap` (2 tests).
- Included 10 units vs hire 1 unit → `TestIncludedAgentPower`.
- Org-wide grant covers all; per-agent grant only its agent;
  per-agent row wins; per-agent deny shadows org-wide →
  `TestGrantScoping` (4 tests).
- Revoking agent A never affects agent B (checks, status, intake);
  class-strength per-agent revoke; re-grant restores only A →
  `TestPerAgentRevocation`.
- Org revoke-all halts every agent →
  `test_org_revoke_all_halts_every_agent`.
- Copies schedule and late-land per agent →
  `test_per_agent_revoke_schedules_only_that_agents_copies`,
  `test_late_copy_for_revoked_agent_lands_on_its_clock`.
- Gate fencing is per agent → `test_ledger_fence_is_per_agent`.
- Schema contract (columns, uniques, backfill, vocabularies, fee) →
  `tests/test_agent_multiagent_migration.py` (9 tests).

## Honest limits and notes

- The archetype SLOT and its load/reassign mechanics are built; the
  archetype CATALOG (persona + task scope + tool allowlist +
  permission profile per shape) is TW-788, not this ticket. Loading a
  key here stores the key; behavior shaping lands with TW-788/Spec 3.
- `power_units` is modeled and exposed on the identity record; the
  runtime that ENFORCES it (task throughput, concurrent slots) is
  Spec 3 (TW-769). Nothing in this ticket throttles work yet.
- The hire endpoint on the identity router is org-JWT scoped like the
  rest of the TW-767 surface; it is NOT owner/admin role-gated the way
  TW-779 made the permission mutations. A hire posts a charge to the
  org, so whether hiring should be an owner-only act is a live
  question for the Rosa/Rex gate — flagged, not silently decided here.
- `app/config.py` fails `ruff format --check` at clean main (pre-
  existing drift across the file, including the TOLL_RATES block);
  the TW-787 edit matches the file's existing alignment style and adds
  no new drift. Disclosed per the brief, not fixed here.
- Migrations are additive and idempotent; running them against a
  pre-TW-787 database keeps every existing identity row, address, and
  grant (grants rows carry agent NULL = org-wide, which is exactly
  what they meant under TW-768).

## What Spec 3 (TW-769) must know about the roster shape

- Agents are a LIST: `service.list_agents(db, org_id)` (included
  first) and `identity_view(db, org_id, agent_id)` per agent. Every
  turn belongs to exactly one agent id; pass it to the permission
  fence (`guarded_step(..., agent_identity_id=...)` /
  `LedgerFence(db, org_id, agent_identity_id=...)`) and to intake
  (`assert_accepting_work(db, org_id, agent_identity_id)`), or the
  runtime silently reads org-wide scope.
- Multiple agents may hold the same role/task class (Justynn's
  clarification): the runtime must support N agents on one task class
  for volume, not assume role uniqueness.
- `power_units` on the identity record is the task budget contract:
  included agent 10 units, hires 1 each (config constants). The
  runtime sizes queued-task throughput and concurrent task slots from
  it.
- Persona/coverage/channel config are per agent: assemble prompts
  from the TURN'S agent record (`channel_config(..., agent_id=...)`),
  never from the org's primary by default.
- Phone resolution returns the one agent bound to the number; email
  resolution returns the addressed agent. Inbound enqueue already
  queues against the resolved agent's identity id.

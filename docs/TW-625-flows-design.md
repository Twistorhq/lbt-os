# TW-625 Twistor Flows — Design

**Prepared by Twistor Holdings LLC**

Workflow automation in the Activepieces mold (MIT CE, mechanism studied, no
Activepieces code — clean-room). n8n is fair-code and was never touched.

## Why it exists

Two customers. Internal: the outreach machine — Dez's Touch 1-4 sequences,
why-switch lanes, and warmup ramps run as Flows workflows from day one,
sending through Twistor Notify. External: every shop needs follow-up
automation (the M2 thesis extended) — Flows is the product that replaces
their Zapier bill.

## The wedge (from TW-630 complaint dossiers)

Zapier's #1 anger: runaway loops with no auto-throttle (52k tasks, $700
bills) and per-task metering nobody can forecast. Flows answers:

1. **Task budgets with auto-throttle.** Every workflow carries a budget
   (max steps/run, max runs/day, max cost/day). At 80% the owner gets a
   warning; at 100% the workflow pauses itself. The throttle fires BEFORE
   money burns, never after.
2. **Cost preview per run.** Every trigger shows its exact cost before
   executing. No surprise bills, ever.
3. **Flat + capped tolls.** `flows.step_executed` at $0.01/step against the
   M1 ledger. The pricing doctrine holds: land with flat, expand with tolls.

## Workflow model (JSON)

```json
{
  "name": "Touch 1 — Denver HVAC",
  "trigger": {"type": "webhook", "key": "whsec_..."},
  "steps": [
    {"id": "s1", "type": "notify", "channel": "email",
     "to": "{{lead.email}}", "template": "touch1",
     "variables": {"shop": "{{lead.shop}}"}},
    {"id": "s2", "type": "delay", "duration": "3d"},
    {"id": "s3", "type": "branch",
     "condition": "{{lead.replied}} == false",
     "then": [{"id": "s3a", "type": "notify", "channel": "email",
               "to": "{{lead.email}}", "template": "touch2"}],
     "else": [{"id": "s3b", "type": "set", "var": "lead.stage",
               "value": "engaged"}]},
    {"id": "s4", "type": "loop", "over": "{{leads}}", "as": "lead",
     "max_iterations": 500, "steps": [{"id": "s4a", "type": "notify",
       "channel": "email", "to": "{{lead.email}}", "template": "warmup"}}]}
  ],
  "budget": {"max_steps_per_run": 100, "max_runs_per_day": 500,
             "max_cost_per_day_cents": 1000}
}
```

Triggers: `manual`, `webhook` (key in path, the key is the secret),
`schedule` (`cron` — evaluated by the tick endpoint).

Step types V1: `notify` (via Twistor Notify), `delay` (suspends the run;
resumed by tick), `branch` (tiny safe condition evaluator: `==`, `!=`,
`>`, `<`, `in` on `{{path}}` values), `webhook` (outbound HTTP),
`set` (context variable), `loop` (iterate a list with a circuit breaker:
iteration cap + error-rate breaker — if over half the iterations error,
the loop breaks and the workflow pauses).

## Execution

Synchronous executor (Notify V1 did the same; async workers are Dre's
follow-up lane). Every step writes to `flw_run_steps`; the run ledger is
the receipt. Delay steps suspend with `resume_at`; `POST /flows/tick`
(guarded by `FLOWS_TICK_KEY`, fail-closed when unset) resumes due
continuations and fires due schedules.

## MCP exposure

`flows/mcp.py` exposes three tools over `POST /flows/mcp`
(org-scoped by the Identity JWT): `flows.list`, `flows.trigger`,
`flows.run_status`. The agent workforce orchestrates through its own
automation layer — that is the forward play from the TW-617 brief.

## Migration

`flows/migration.py`: `from_zapier()` converts Zapier-concept JSON
(trigger + action list) to a Flows definition. Supported: email→notify,
delay→delay, filter→branch, webhook→webhook. `code` steps are refused
with a clear error (documented, not silently dropped). Concept mapping
table lives in the module docstring.

## Tables (`supabase/migration_flows_v1.sql`)

`flw_workflows`, `flw_runs`, `flw_run_steps`, `flw_budget_counters`.
RLS deny-all, matching the identity/notify migrations.

## Non-goals for V1

Visual builder UI (Sofia's lane), async queue workers (Dre's lane), real
push provider (Notify's lane), sub-workflow calls, human-approval steps.

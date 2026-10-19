# TW-621 Twistor Flags — Design

**Prepared by Twistor Holdings LLC**

Feature-flag platform, clean-room from the GO Feature Flag mechanism (MIT).
No GO Feature Flag code. No Unleash code (AGPL — never touch).

## The wedge (TW-630)

LaunchDarkly's #1 user anger is per-seat x per-context metering nobody can
forecast — quotes doubling $6k to $11k at signing. Complaint #2 is flag debt
with no lifecycle tooling. Twistor Flags answers both:

1. **No context meter.** Evaluations are UNLIMITED on the flat subscription.
   There is deliberately no evaluation log table — logging every evaluation
   would recreate the meter's data exhaust. Flat price, unlimited flags,
   unlimited contexts, unlimited evaluations.
2. **Governance in the base plan.** The audit trail (who flipped what, when)
   ships in V1, not as an enterprise upsell.
3. **Lifecycle tooling.** Scheduled flips (enable/disable at a UTC time) and
   the audit trail give flag debt a paper trail from day one.

## Model

A flag is a key with:
- `variations`: ordered list of values (boolean, string, number, JSON)
- `default_variation`: index served when disabled or when nothing matches
- `targeting`: ordered rules; first match wins. A rule has conditions
  (attribute, operator, value — 11 operators) and a `then`: a fixed
  variation or a percentage split (must sum to 100)
- `scheduled`: list of {at, enabled} flips applied by the service

All semantic validation raises `DefinitionError` raw on every construction
path (a strict base converts pydantic's `ValidationError`). Fail loud,
never silent.

## Evaluation

Pure functions (`evaluation.py`): no DB, no network, no clock. The SDK hot
path evaluates a cached `FlagDefinition`.

- Disabled -> default variation, reason `disabled`
- First matching rule -> its variation (or percentage bucket), reason
  `targeting_rule`
- No match -> default variation, reason `default`
- Bucketing: `sha256(flag_key + ":" + targeting_key) % 10000`, deterministic
- Fail-closed: missing attributes never match; bool/numeric comparisons
  reject mismatched types; unknown operators are impossible (validated)

## Service

`service.py`: CRUD with version bumps, `flip_flag` (audited as `flip`),
`apply_due_schedules` (earliest-due change wins per flag; naive datetimes
treated as UTC), append-only `flg_audit`. TW-208: an audit-insert failure
never fails the flag mutation.

## Migration

`migration.py`: LaunchDarkly flag dicts -> `FlagDefinition`. Operator map
covers `in/notIn/equal/notEqual/gt/gte/lt/lte/contains/startsWith/endsWith`;
negation inverts `in`<->`not_in`, other negations are reported. LD weights
(per-mille of 100000) convert to integer percents with rounding-drift
repair. `migrate_with_report` returns unmapped constructs (segment refs,
semver ops) instead of dropping them silently; strict mode raises.

## API

- `POST /flags/evaluate` and `POST /flags/evaluate/bulk` (cap 100) — no toll
- Admin: `POST/GET /flags/admin`, `GET/PATCH /flags/admin/{key}`,
  `POST /flags/admin/{key}/flip`, `DELETE /flags/admin/{key}`,
  `GET /flags/admin/{key}/audit`,
  `POST /flags/admin/import/launchdarkly` (strict default),
  `POST /flags/admin/apply-schedules` (scheduler tick calls this)
- Org scoping from the Twistor Identity JWT on every endpoint; the actor
  for audit comes from the token claims.

## Schema

`supabase/migration_flags_v1.sql`: `flg_flags` (one key per org, versioned
definition JSON), `flg_audit` (append-only). RLS deny-all, idempotent.

## Non-goals (V1)

Real-time flag streaming to SDKs (poll the eval API), a visual rule
builder (Sofia's lane), segment reuse across flags, approval workflows
(ticketed as follow-up).

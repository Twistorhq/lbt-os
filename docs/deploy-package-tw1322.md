# Twistor Trades — Deploy Package (TW-1322)

Prepared by Twistor Holdings LLC

Status: **NOT YET PROD-READY — staged, fixes on branch awaiting
gates.** Everything in this package is staged and verified locally.
Nothing has been deployed, no Fly app has been created, and no secret
value appears in this document or in the repo.

Rex's adversarial audit (TW-1322) found three launch-blocking
identity wedges (TW-1323 F1 critical, TW-1324 F2, TW-1325 F3) plus
two smaller findings (TW-1326 F4, TW-1327 F5). All five are FIXED on
branch `dre/tw-1322-prod-ready` with probe-shaped tests (17 new
tests, full suite green). The verdict stays NOT YET until Rosa
gates the branch and Rex re-probes F1–F4 on the patched code. Only
then does this become "prod ready, awaiting founder deploy word."

Supersedes the Fly/secrets sections of `docs/deployment.md`, which
still describe the retired legacy plan prices and the old app name.

---

## 1. Fly app name

**Use `twistor-trades-backend`.** Do not use `lbt-os-backend`.

Verified live against the Fly API on 2026-11-24 (Dre, TW-1322):

| Name | Fly API result | Meaning |
|---|---|---|
| `lbt-os-backend` | `403 unauthorized` on `GET /v1/apps/lbt-os-backend` | The name is registered to a **third party**. It is gone and not reclaimable by us. |
| `twistor-trades-backend` | `404 app not found` | Unclaimed. Available to create. |
| Our org (`personal` / "Twistor") | App list = `ourlgcy-intake`, `twistor-social` only | Neither name is ours today; no collision inside the org. |

Final confirmation happens implicitly at `flyctl launch` / app
creation (step 6 below): if creation succeeds, the name is ours.

`backend/fly.toml` on this branch is already staged with
`app = "twistor-trades-backend"`, region `den`, `APP_ENV=production`.

---

## 2. The 10 secrets (names and sources only — never values in chat, tickets, or the repo)

All 10 are code-verified against `backend/app/config.py`: nine are
required `Settings` fields with no default, and the tenth
(`CLERK_JWT_AUDIENCE`) is enforced by `Settings.model_post_init`,
which refuses to boot in production without it. Set them with
`flyctl secrets set` (step 6). The founder collects each value from
his own dashboard session; nobody else handles the values.

| # | Secret (env name) | Where the value comes from |
|---|---|---|
| 1 | `SUPABASE_URL` | Supabase dashboard → the Trades project → Settings → API → Project URL |
| 2 | `SUPABASE_SERVICE_KEY` | Same screen → `service_role` key (secret). This is the backend's only database credential; it bypasses RLS by design, which is why TW-710 (section 4) is safe to apply. |
| 3 | `CLERK_SECRET_KEY` | Clerk dashboard → the Twistor application → API Keys → Secret key |
| 4 | `CLERK_PUBLISHABLE_KEY` | Same screen → Publishable key |
| 5 | `CLERK_WEBHOOK_SECRET` | Clerk dashboard → Webhooks → Signing secret. Note: the backend currently has **no Clerk webhook consumer** — orgs are created by the authenticated onboarding call, not a webhook. The value is still required for boot (config.py requires the field). If the Clerk app has no webhook endpoint yet, create one in the dashboard to mint the secret; the URL does not need to resolve for launch. |
| 6 | `CLERK_JWT_AUDIENCE` | Clerk dashboard → JWT Templates → the template in use → Audience field |
| 7 | `STRIPE_SECRET_KEY` | Stripe dashboard → Developers → API keys → Secret key (live mode at go-live) |
| 8 | `STRIPE_WEBHOOK_SECRET` | Stripe dashboard → Developers → Webhooks → the endpoint for `https://twistor-trades-backend.fly.dev/api/stripe/billing/webhook` → Signing secret |
| 9 | `STRIPE_PRICE_TRADES_STARTER` | Stripe dashboard → Products → Trades Starter ($299/shop/mo) → the recurring Price ID (`price_...`) |
| 10 | `STRIPE_PRICE_TRADES_PRO` | Stripe dashboard → Products → Trades Pro ($499/shop/mo) → the recurring Price ID (`price_...`) |

Production boot also validates the Stripe values' shapes
(`sk_`, `whsec_`, `price_` prefixes) and refuses to start if any is
missing or a placeholder — a bad secret set fails loudly at boot,
never silently in production.

### Also required at deploy time (configuration, not secrets)

| Env name | Value / source |
|---|---|
| `TRUSTED_HOSTS` | `twistor-trades-backend.fly.dev` — boot raises in production if unset. |
| `FRONTEND_URL` | The production frontend origin for the Trades site. |
| `CORS_ORIGINS` | The same origin (comma-separated if several). |
| `OWNER_EMAILS` | The founder's owner-SSO emails (TW-484), comma-separated. |

### Optional, decided separately

- `LLM_PROVIDER=openai` + `OPENAI_API_KEY`: the config default is a
  local Ollama, which does not exist on Fly. Without this pair the
  AI audit features degrade; signup, billing, and the core product
  are unaffected. Founder's call, not a launch blocker for the wedge.

---

## 3. Founder steps, in run order

Steps 1–5 prepare the outside services; step 6 is the deploy itself.
Steps 1–5 can be done any time. Step 6 waits for the deploy word.

1. **Supabase — schema.** In the Trades project's SQL Editor, apply
   `supabase/schema.sql`, then `supabase/rls_policies.sql`, then the
   feature migrations under `supabase/migration_*.sql` (they are
   written to be run once, in filename order within each feature).
   One callout: `supabase/migration_org_clerk_user_unique_tw1323.sql`
   (the TW-1323 partial UNIQUE index on
   `organizations.clerk_user_id`) runs after
   `migration_add_clerk_user_id.sql`; it refuses to apply if
   duplicate org rows already exist for a user — on a fresh project
   there are none, and its pre-flight query in the file header
   proves it.
2. **Supabase — TW-710 RLS.** Apply the RLS migration exactly as
   written in section 4 below.
3. **Clerk.** Confirm the application exists; note the JWT template
   Audience for secret #6 and the Webhooks signing secret for #5
   (see the note on #5 — no backend Clerk-webhook route exists, so
   there is no Clerk webhook URL to wire at launch).
4. **Stripe.** Create the two products/prices ($299 Starter, $499
   Pro, per shop per month) and the webhook endpoint for
   `https://twistor-trades-backend.fly.dev/api/stripe/billing/webhook`
   with events `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `checkout.session.completed`.
5. **Collect the 10 secrets** from the table in section 2 (plus the
   four configuration values). Values go straight into the `flyctl
   secrets set` command in step 6 — never into a file in this repo.
6. **Fly — create + secrets + deploy** (from `lbt-os/backend`, with
   this branch's staged `fly.toml`):
   - `flyctl launch --no-deploy` (creates `twistor-trades-backend`)
   - `flyctl secrets set SUPABASE_URL=... SUPABASE_SERVICE_KEY=... CLERK_SECRET_KEY=... CLERK_PUBLISHABLE_KEY=... CLERK_WEBHOOK_SECRET=... CLERK_JWT_AUDIENCE=... STRIPE_SECRET_KEY=... STRIPE_WEBHOOK_SECRET=... STRIPE_PRICE_TRADES_STARTER=... STRIPE_PRICE_TRADES_PRO=... TRUSTED_HOSTS=twistor-trades-backend.fly.dev FRONTEND_URL=... CORS_ORIGINS=... OWNER_EMAILS=...`
   - `flyctl deploy`
   - Smoke check: `GET https://twistor-trades-backend.fly.dev/health`
     returns 200, then one real signup → trial → first-lead pass
     (the same flow TW-1322 proves locally) before any outreach.

---

## 4. TW-710 — Supabase RLS migration, apply-ready

**File:** `supabase/migration_rls_deny_all_tw710.sql` (already in the
repo; reviewed by Dre on TW-1322 — no changes needed).

What it does: enables Row Level Security plus one explicit deny-all
policy (`FOR ALL USING (false)`) on the 19 tables appsecradar found
created without RLS: 13 `sci_*` data-layer tables, `stripe_events`,
`plan_audit_log`, `lead_stage_history`, `workspace_exports`,
`migration_runs`, and `visitor_events`. The FastAPI backend is the
only client and connects with the service role, which bypasses RLS
by design — product behavior is unchanged; every other role (anon
key, PostgREST) gets nothing.

Properties verified on review:

- **Idempotent** — safe to re-run (`ENABLE` is a no-op when already
  enabled; policy creation is guarded by `pg_policies` lookups).
- **Reversible** — the file's DOWN section (bottom, commented) drops
  the policies and disables RLS in reverse order.
- **Prerequisites** (from the file header) — run after:
  `migration_sci_core_v1.sql`, `migration_sci_electrocatalysis_v1.sql`,
  `migration_sci_high_tc_v1.sql`, `migration_data_portability.sql`,
  `migration_revenue_intelligence.sql`, `migration_stripe_events.sql`,
  `migration_visitor_events.sql`. All are covered by step 1 above.

**Founder apply steps:**

1. Supabase dashboard → Trades project → SQL Editor → New query.
2. Paste the full contents of `supabase/migration_rls_deny_all_tw710.sql` and run it.
3. Verify with this query — it must return **19**:

```sql
SELECT count(*) FROM pg_policies WHERE policyname LIKE '%_deny_all';
```

   And this must return **19** (every TW-710 table has RLS on):

```sql
SELECT count(*) FROM pg_class
WHERE relname IN (
  'sci_labs','sci_calibrations','sci_bronze_artifacts',
  'sci_dataset_versions','sci_corrections','sci_manifests',
  'sci_attestations','sci_benchmarks','sci_benchmark_runs',
  'sci_electro_measurements','sci_electro_gold',
  'sci_hightc_measurements','sci_hightc_gold',
  'workspace_exports','migration_runs','lead_stage_history',
  'plan_audit_log','stripe_events','visitor_events'
) AND relrowsecurity;
```

4. If anything looks wrong, run the DOWN section from the bottom of
   the migration file and stop; the backend is unaffected either way
   (service role bypasses RLS).

---

## 5. Deploy checklist

- [x] Signup → trial → first-job persistence proven locally (TW-1322 tests, incl. mid-signup failure recovery)
- [x] Stripe webhook sync defect found and fixed (TW-1322; trials/payments now actually sync — see ticket)
- [x] Rex F1–F5 fixed on branch with probe-shaped tests (TW-1323–TW-1327; 17 tests, full suite green, zero regressions)
- [ ] Rosa gate on branch `dre/tw-1322-prod-ready`
- [ ] Rex re-probe of F1–F4 on the patched code
- [x] Fly app name chosen and collision state re-verified (`twistor-trades-backend`)
- [x] `fly.toml` staged with the new app name
- [x] 10 secrets enumerated by name with sources (section 2)
- [x] TW-710 migration reviewed, apply-ready, verification queries written (section 4)
- [x] TW-1323 migration written (partial UNIQUE index; refuses to apply over duplicates)
- [ ] Founder: Supabase schema + migrations applied, incl. TW-710 and TW-1323 (steps 1–2)
- [ ] Founder: Clerk + Stripe dashboards configured (steps 3–4)
- [ ] Founder: secrets collected and set (steps 5–6)
- [ ] Founder: `flyctl deploy` on his word (step 6)
- [ ] Post-deploy smoke pass: /health, one signup → trial → first lead

**State: NOT YET prod-ready — fixes staged on branch, awaiting
Rosa gate + Rex re-probe (TW-1323–TW-1327). After those close:
prod ready, awaiting founder deploy word.**

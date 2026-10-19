# Prepared by Twistor Holdings LLC

# TW-608 TDD Evidence: rate-limit notifications, no-surprise-overcharges

## Rule (Justynn 2026-10-18)
A customer must NEVER be charged overage fees they did not see coming.
Warn at 80% of the rate limit; at 100% SHUT OFF (429), charge nothing.

## Design
- `RATE_LIMIT_WARN_RATIO = 0.80`, `RATE_LIMIT_WINDOW_SECONDS = 60`,
  `RATE_LIMIT_WARN_COOLDOWN_SECONDS = 900` -- all configurable module
  constants, not hardcoded.
- `rate_limit_usage(db, org_id)`: counts `data.science.*` toll events for
  the org in the trailing 60s. The toll ledger is the source of truth, so
  counted requests are exactly the requests billed. Fail-soft: on lookup
  failure returns zero usage so the caller serves (fail-open, TW-208).
  A broken counter must never block a paying customer.
- `enforce_rate_limit(db, org_id)`: pre-serve gate. At 100% raises 429
  with `Retry-After` + `X-Twistor-RateLimit-*` headers. The 429 path emits
  NO toll, so a rejected request can never produce a charge. Below the
  limit, triggers the 80% courtesy warning and returns the usage status.
- `maybe_send_rate_limit_warning`: email via
  `app.services.email.send_rate_limit_warning`, cooldown-deduped per org
  (15 min, process-local). Recipient is `organizations.billing_email`
  best-effort; when absent, the in-band warning (usage block/headers)
  still fires and the miss is logged at debug.
- `gate(db, org_id)`: subscription (402) + rate limit (429), drop-in for
  the Wave-1 routers' `_gate` helpers.
- The 4 older lanes (electrocatalysis, high_tc, pfas, turbulence) have no
  per-endpoint `_gate`; each endpoint calls
  `paywall.enforce_rate_limit(db, org)` as its first statement after db
  resolution. turbulence uses `auth.org_id`; the 3 public lanes use their
  existing toll org as a shared public bucket
  ("public-science", "twistor_science", "twistor-science").
- Conscious scope call: Wave-1 `/ingest` endpoints are NOT rate-limited.
  They authenticate by lab API key + lab binding (`require_science_key`,
  `enforce_lab_binding`) -- a separate trust domain from customer-billed
  reads, so customer rate-limit tiers do not apply. Older lanes' ingest
  endpoints carry the shared-bucket enforcement as abuse protection.
- Usage block (TW-607) gains `rate_limit_used` and `rate_limit_warning`;
  headers gain `X-Twistor-RateLimit-Limit`, `X-Twistor-RateLimit-Used`,
  and `X-Twistor-RateLimit-Warning: 1` when the warning is active.

## Tests
`backend/tests/test_science_rate_limit.py`: 20 tests, all green (18 unit +
2 router-wiring regression tests added in Rosa round 1).
Covers: window counting (science events only, org-scoped, window-scoped),
warn/over flags at 80%/100%, tier rpm respected, unscoped fail-open,
DB-down fail-open, 429 shape + headers + ledger untouched on reject,
subscription gate fires before 429, email sent once per cooldown,
no email without billing_email, no warning below threshold, usage
block/header fields.

Full science suite: 240 passed (test_science_paywall 25,
test_science_rate_limit 20, carbon 44, subseasonal, turbulence 33,
electrocatalysis, high_tc, pfas). Ruff check clean. Ruff format --check
fails on pre-existing repo drift in the lane routers and test file
(CI gates only `ruff check`); the new TW-608 section extracted alone
is format-clean.

## Known limitations (V1)
- Warning-email dedupe is process-local; a second worker may rarely
  double-send inside the cooldown. The email is courtesy only, never a
  charge, so this is acceptable.
- Per-request cost is now up to 2 small toll_events count queries on
  Wave-1 lanes (gate + finalize usage block). Follow-up: batch the
  usage lookups ("meter the metering", Rosa's TW-607 note).
- `organizations.billing_email` column does not exist yet; the email
  channel activates once it is added and populated. Until then the
  in-band warning is the guaranteed channel.

## Rosa round 1 (REQUEST_CHANGES) -- fixed
1. high_tc `dataset_detail`: enforce was in the 404 branch only; moved to
   the top of the endpoint so the 200 path is rate-limited.
2. high_tc `list_benchmarks`: enforce was inside the per-benchmark loop
   (bypassed on empty table, N scans otherwise); moved to the top.
3. high_tc `list_datasets`: enforce call was missing entirely; added.
4. Evidence doc corrected (format claim, endpoint-coverage claim).
5. Added 2 router-wiring regression tests so placement misses cannot
   recur: every @router endpoint in the 4 older lanes must call
   enforce_rate_limit at function top level (indent 4); Wave-1 _gate
   helpers must route through paywall.gate.
6. Conscious call documented: Wave-1 /ingest endpoints stay on lab
   API-key auth (separate trust domain, not customer-billed).

## Branch
`dre/tw-608-ratelimit-notifications` (off `dre/tw-607-usage-metering`).
NOT merged -- awaiting Justynn's word per standing rule.

# TW-622 Twistor Telemetry — TDD Evidence Report

**Prepared by Twistor Holdings LLC**

## 1. Source

- Ticket: TW-622 (Twistor Telemetry — product analytics, clean-room from
  the PostHog MIT-core mechanism; no PostHog code).
- Design: `docs/TW-622-telemetry-design.md`.
- Branch: `dre/tw-622-telemetry` (worktree
  `~/workspace/worktrees/dre-tw622-telemetry`).

## 2. Task report

Per behavior: one-sentence summary; the validation command actually run;
RED/GREEN outcome; what the passing tests guarantee.

| # | Behavior | Validation | RED | GREEN | Guarantees |
|---|---|---|---|---|---|
| 1 | Event/batch/query/insight models fail loud on bad input | `python3 -m pytest tests/test_telemetry_models.py -q` | collection error (module absent) | 10 passed | blank names, >100 props, non-JSON values, empty/oversize batches, inverted ranges, bad buckets, bad insight kinds all rejected |
| 2 | PII scrubbed recursively at ingestion, deterministic per org | `python3 -m pytest tests/test_telemetry_scrub.py -q` | collection error | 8 passed | email/phone hashed (64-hex), nested values scrubbed, cross-org hashes differ, distinct_id always hashed, input never mutated |
| 3 | Trend/funnel/retention/breakdown pure query engine | `python3 -m pytest tests/test_telemetry_query.py -q` | collection error | 10 passed | zero-filled buckets, out-of-order + out-of-window funnel steps excluded, cohort day-N math, breakdown top-N with missing-prop exclusion |
| 4 | Budget contract: warn at 80%, sample (never drop/bill) over cap | `python3 -m pytest tests/test_telemetry_budgets.py -q` | collection error | 8 passed | ingest/sample/warn decisions, sample rate shrinks with overage, deterministic sampling, invalid rates rejected |
| 5 | Service: ingest scrubs+hashes, batch insert, budget accounting, insights CRUD | `python3 -m pytest tests/test_telemetry_service.py -q` | collection error | 9 passed | single batch insert, sampled rows flagged with rate, budget counts RECEIVED (honest metering), version bumps, bad kinds rejected |
| 6 | PostHog import maps events/funnels, reports unmapped honestly | `python3 -m pytest tests/test_telemetry_migration.py -q` | collection error | 5 passed | event/funnel mapping, replay/heatmap/experiment/flag sections named as unmapped, never silently dropped |
| 7 | Datafeed: cohort aggregates with k-anonymity gate, no identifiers | `python3 -m pytest tests/test_telemetry_datafeed.py -q` | collection error | 4 passed | grouping, small-cohort suppression, output provably identifier-free |
| 8 | Router: capture 202, queries, admin, import, datafeed end to end | `python3 -m pytest tests/test_telemetry_router.py -q` | collection error, then fixture-call + mock-chain bugs (fixed in test code, not prod) | 8 passed | 202 accounting, 422/404 mappings, insight CRUD round trip, PostHog import unmapped report, datafeed k-gate |

Full telemetry suite: `61 passed` (1 pre-existing warning: starlette
BlockingPortal deprecation, unrelated).

Full backend suite on branch: `676 passed, 20 failed` — the 20 failures
are byte-identical to main's pre-existing failures (verified by running
`test_bi_whitelabel.py`, `test_missed_call.py`, `test_pilot_blockers.py`
on the clean main checkout: same 20 failed, 43 passed). **Zero
regressions.** The 7 xmlsec collection errors are also pre-existing on
main (missing lxml/xmlsec libs in this environment).

`ruff check` clean; `ruff format` clean on all new files.

## 3. Coverage and known gaps

- No statistical/ML models ship in V1 — funnels, retention, trends are
  deterministic queries. When anomaly detection lands, it gets the
  9-point governance spec per Justynn's standing rule.
- `occurred_at` strings that fail ISO parsing are skipped in
  `fetch_events` (defensive; logged nowhere — acceptable at V1 volume,
  ticketed as a follow-up if it ever fires).
- Email warning on 80% budget is log + in-band response flag only; the
  TW-608 email path needs `organizations.billing_email`, which does not
  exist yet (same blocker as TW-608).
- Session replay, heatmaps, experiments: honestly unmapped in the
  PostHog importer (V1 scope decision, documented).
- Migration SQL not run against a live DB (integration pass owns it).

## 4. Plan-safety notes

No untrusted plan content encountered. All requirements came from the
Marcus brief + standing doctrine (TW-552 PII, TW-208 fail-soft, TW-630
wedge). No destructive or credential-handling steps taken.

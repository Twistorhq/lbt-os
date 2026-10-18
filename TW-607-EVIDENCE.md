# Prepared by Twistor Holdings LLC

# TW-607 Evidence: Per-Transaction Usage Metering (Claude Code EOM Style)

**Branch:** `dre/tw-607-usage-metering` (worktree `/tmp/dre-tw607`)
**Base:** `8a56bec` (TW-606 dynamic pricing) — rebased mid-build after discovering TW-606 landed
**Tests:** 22 new (`test_usage_metering.py`) + 240 science-lane tests — all green
**E2E:** Verified via TestClient — usage block in JSON body + `X-Twistor-*` headers

## What was built

Every science API response now carries a Claude Code end-of-message style usage block:

```json
"usage": {
  "records": 100,
  "toll_cents": 42,
  "toll": "$0.42",
  "period": "trailing_30d",
  "period_total_cents": 1250,
  "period_total": "$12.50",
  "rate_limit_tier": "pro",
  "rate_limit_rpm": 600
}
```

Plus headers for programmatic clients:
- `X-Twistor-Toll: $0.42`
- `X-Twistor-Usage: {"records":100,"toll_cents":42,"period_total_cents":1250}`

## One source of truth

`paywall.build_usage_block()` reads **only** from the `toll_events` ledger:
- Per-call toll: sum of rows matching the request's idempotency keys (`sci:{rid}`, `sci:api_rows:{rid}`, `sci:download:{rid}`, `sci:{rid}:download`)
- Running total: `trailing_science_spend_cents()` (the same function TW-606 pricing uses)
- Tier: `org_rate_limit_tier()` (the same function TW-606 uses)

Nothing is recomputed from pricing formulas. The readout is what was actually charged.

## Coverage: all 7 lanes, 40+ endpoints

| Lane | Body | Headers | Notes |
|------|------|---------|-------|
| carbon (8) | `ScienceEnvelope.usage` | Yes | Download: headers only (body is the file) |
| subseasonal (6) | `ScienceEnvelope.usage` | Yes | Download: headers only |
| crop_climate (9) | `Envelope.usage` | Yes | Download: headers only; ingest: `IngestResponse.usage` |
| electrocatalysis (6) | dict `usage` key | Yes | No org concept (org=None) |
| high_tc (7) | `ResponseEnvelope.usage` | Yes | `BenchmarkRunRead` headers-only (not an envelope); `submit_benchmark_run` routes through `finalize` |
| pfas (6) | 6 models | Yes | Uses `_TOLL_ORG` for period total |
| turbulence (5) | 3 models | Yes | JSON arrays headers-only |

## Fail-soft (TW-208)

Every metering path is wrapped: ledger lookup failure → zeroed usage block, header stamp failure → logged, never breaks serving. `finalize()` without a response object works for service-layer use.

## Pre-existing failures (not mine)

20 failures in `test_bi_whitelabel.py` (8), `test_missed_call.py` (11), `test_pilot_blockers.py` (1) — confirmed failing on base via `git stash`. Unrelated modules (BI, Twilio). 7 collection errors from missing `sniffio` — environment issue.

## For Sofia (portal display)

The API contract she'll render:
- `GET` responses: `body.usage` object (see shape above)
- `X-Twistor-Toll` header: human-readable per-call charge
- `X-Twistor-Usage` header: compact JSON for JS clients
- Excel downloads: `X-Twistor-Toll` / `X-Twistor-Usage` on the file response; README sheet also lists rows + fee

## Fixes from Rosa review (REQUEST_CHANGES → addressed)

1. **high_tc `submit_benchmark_run`**: was returning `BenchmarkRunRead` directly without `finalize` — now routes through `paywall.finalize()` so usage headers are stamped.
2. **crop_climate request_id consistency**: `_toll()` now takes an explicit `request_id` parameter instead of minting its own via `_request_id(request)`. All 8 call sites thread the single per-endpoint `request_id` through both the toll emission and `finalize`, so the usage lookup hits the same idempotency key the toll was charged under.
3. **Regression tests added**: `test_crop_climate_toll_uses_single_request_id` and `test_high_tc_benchmark_submit_routes_through_finalize` (22 tests total, all green).

## For Rosa

Diff: `git diff 8a56bec..dre/tw-607-usage-metering` — 15 files.
Key files: `paywall.py` (the shared module), 7 routers, 6 schema files, 1 test file.
All 240 science tests green (22 in test_usage_metering.py). Ready for re-review; **no merge without Justynn's word**.

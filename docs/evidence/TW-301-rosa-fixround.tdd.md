# TW-301 Rosa fix round — TDD evidence

Prepared by Twistor Holdings LLC.

Rosa's review of `feature/tw-301-tradeview-api` (2026-10-04): CHANGES REQUIRED
(2 major, 2 minor, 2 nit). Independent verification matched Zeke's claims
(backend 154 passed/8 subtests, frontend tradeview 18/18, ruff clean).

## RED

Commit `RED`: 6 new tests across 4 test classes; 4 failed pre-fix
(validated). The two dossier/diagnostics DB-outage tests PASSED pre-fix —
guards, not regression proofs (the old code answered 404, which satisfies
`assertNotEqual(200)`); kept for protection:
- `DbOutageTest::test_pins_db_outage_is_not_200_empty` — FAILED (got 200 empty)
- `DbOutageTest::test_dossier_db_outage_is_not_200` — PASSED (guard)
- `DbOutageTest::test_diagnostics_db_outage_is_not_200` — PASSED (guard)
- `GeocodeBudgetTest::test_geocode_attempts_capped_per_request` — FAILED (30 calls)
- `LeakScanMemoTest::test_scan_runs_once_per_org_within_ttl` — FAILED (2 calls)
- `GeocodeTest::test_cache_is_bounded_lru` — FAILED (unbounded)

## GREEN — fixes

**MAJOR 1 — DB outage → 502, never 200-empty.** `_rows` now raises
`_DBUnavailable` on total table failure instead of returning `[]`. The pins,
dossier, and diagnostics endpoints catch it and answer HTTP 502, so the
frontend `.catch()` engages the TW-295 sample fallback. Per-row isolation is
preserved in the pins loop (one bad row / one failed geocode still degrades
to `unlocated`). Bonus honesty: pins response now carries `records_total`;
a 200 with zero pins AND zero records renders "Your book is empty — connect
customers or leads" instead of a bare map.

**MAJOR 2 — geocode budget per request.** `_GeocodeBudget(10)` attempts per
`/pins` request, pins-path timeout lowered 10s → 2s. Remainder count as
unlocated this round; the process cache warms across requests. Worst case is
now bounded instead of minutes-long.

**MINOR 1 — dossier/diagnostics error states.** `liveDossierError` /
`liveDiagnosticsError` state; `LiveDossier` / `LiveDiagnosticsReport` accept
an `error` prop rendering an honest failure note. "Loading…" can no longer
stick forever.

**MINOR 2 — leak-scan memo.** Module-level `_brief_cache` keyed
`(org_id, id(db))`, 60s TTL, 64-org cap. `get_db()` is a process singleton
in production (effectively per-org); test doubles get fresh entries.
Failures are never cached.

**NIT 1 — bounded geocode cache.** `GeocodeCache(max_entries=2000)` with a
real LRU (`OrderedDict`, `move_to_end` on hit, evict least-recent on insert).

**NIT 2 — single rate limit.** Dropped the redundant IP-keyed
`@limiter.limit("30/hour")`; kept the spoof-proof per-user
`enforce_user_limit`, raised to `120/hour` for interactive map use.

## Verification (Zeke, post-fix)

- Backend: `160 passed, 8 subtests passed` (22 in test_tradeview_api.py)
- Frontend: `63 passed` (9 files), incl. 4 new failure-state tests
- `ruff check` + `ruff format --check`: clean on all touched files
- RED→GREEN: the 4 RED tests failed pre-fix and pass post-fix (no test edits
  between, except the `_geo` mock gaining the new `timeout` kwarg and one
  `ruff format` reflow)

## Before/after proof

- Before: raising DB → `200 {"source":"live","pins":[],"unlocated_count":0}`
  → frontend rendered an empty map with a "Live data" badge.
  After: raising DB → `502` → frontend `.catch()` → sample map.
- Before: 30 distinct uncached addresses → 30 sequential geocode calls inline.
  After: ≤10 attempts per request, rest counted unlocated.
- Before: dossier fetch failure → `liveDossier` stayed null → "Loading live
  dossier…" forever. After: honest error note.
- Before: 2 pin clicks → 2 full-org `run_leak_scan` calls.
  After: 1 call (60s TTL).

Nothing merges without Justynn's exact approval.

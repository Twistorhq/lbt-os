# TW-571 M4 roofing pro pack — TDD evidence

Prepared by Twistor Holdings LLC.

## 1. Source
- Ticket TW-571 (reporter Justynn, green-lit 2026-10-16, parallel with M3).
- Spec: `openspec/changes/tw-571-m4-roofing/` (proposal.md, design.md,
  tasks.md, specs/roofing/spec.md) — `openspec validate tw-571-m4-roofing`
  → "Change 'tw-571-m4-roofing' is valid".
- Rosa TW-547 findings applied: minor 3 (`quotes.attributes` ADDED by the
  migration, not assumed), Major 2 (TCPA consent model in the proposal —
  existing customers only, opt-out language, no prospect blasts in v1).

## 2. Task report
- **Takeoff math (RED→GREEN):** tests written first; initial run failed with
  `ModuleNotFoundError` (no implementation). After implementation:
  `compute_takeoff(28.5, "6/12", 10, "architectural_shingle")` →
  material 31.35 sq @ $145 = $4,545.75; labor 28.5 sq @ $95 = $2,707.50;
  total $7,253.25 — Decimal exact, `ROUND_HALF_UP`.
  Command: `~/workspace/.venvs/lbt-os/bin/python -m pytest
  backend/tests/test_roofing.py -q` → **12 passed**.
- **Full suite:** same venv, `pytest backend/tests/ -q -p no:cacheprovider`
  → **249 passed, 8 subtests passed**.
- **Ruff:** CI's exact invocation (`ruff==0.16.8`):
  `ruff check app tests` → **All checks passed** (5 findings fixed:
  3 import-sort, 1 unused var, 1 ambiguous name).
- **Frontend:** `vite build` (via main-checkout node_modules) → built in
  13.93s, no errors (chunk-size warning is pre-existing).
- **Router behavior verified by tests:** unknown material → 422; cross-org
  quote → 404 (no org enumeration); PO send draft→sent with exact-cent
  total $4,640.00 on 32 × $145; supplier PII masked in responses;
  NWS down → 502 with no fabricated alerts; campaign creates 1 lead for the
  1 ZIP-matching customer (2nd customer in other ZIP excluded).

## 3. Guarantees table
| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Takeoff math exact to the cent | `backend/tests/test_roofing.py::TakeoffMathTests::test_takeoff_exact_cents` | RED→unit | PASS | $4,545.75 / $2,707.50 / $7,253.25 |
| 2 | Unknown material rejected, quote untouched | `...::test_takeoff_unknown_material_rejected` + router 422 test | RED→unit | PASS | 422 |
| 3 | Cross-org quote reads as not found | `...::test_takeoff_cross_org_quote_404` | integration | PASS | 404 |
| 4 | PO send transitions + exact total | `...::test_po_send_transitions_status` | integration | PASS | status=sent, $4,640.00 |
| 5 | Supplier PII masked in API responses | `...::test_supplier_crud_scoped_to_org` | integration | PASS | masked email/phone asserted |
| 6 | Storm alerts filtered to roofing events | `...::test_storm_recent_filters_roofing_events` | integration | PASS | 1/2 events kept |
| 7 | Weather outage → honest 502 | `...::test_storm_service_down_502` | integration | PASS | 502 |
| 8 | Campaign → leads for matching ZIPs only | `...::test_campaign_creates_leads_for_matching_customers` + inline-event test | integration | PASS | 1 recipient, 1 lead |
| 9 | No regressions | full suite | CI | PASS | 249 passed + 8 subtests |
| 10 | Lint clean under CI's exact ruff | `ruff check app tests` | CI | PASS | All checks passed |
| 11 | Frontend compiles | `vite build` | CI | PASS | built, no errors |

## 4. Coverage and known gaps
- Frontend has no unit tests (repo convention: frontend tested via build;
  backend carries the behavioral tests). Noted, not hidden.
- `fetch_fulltext`-style PDF extraction is out of scope (harvester ticket).
- Storm recipient matching is ZIP-substring on free-text `address`
  (deterministic v1); structured service-area polygons are a follow-up.
- Twilio SMS in campaigns is best-effort: unconfigured Twilio records the
  campaign with 0 sent, honestly reported (TW-208).
- Migration SQL is not executed against a live Supabase in this run
  (repo convention: migrations apply at deploy).

## 5. Plan-safety notes
- No embedded instructions, override phrases, or suspicious content found
  in the TW-547 architecture doc, the parent brief, or any repo file read
  during this work. The NWS/zippopotam URLs are public keyless APIs named
  in the brief; no new external calls were added beyond them.
- Scope interpretation recorded: the brief said "takeoff entry on the quote
  surface" but no quote UI exists in the frontend; the takeoff calculator
  ships as a tab on the new Roofing page instead (same workflow, documented
  here). No scope widened beyond the brief.

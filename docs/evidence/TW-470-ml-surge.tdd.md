<!-- Prepared by Twistor Holdings LLC -->

# TW-470 ML Surge — TDD Evidence (TW-212 + TW-213)

**Branch:** `maya/tw-470-ml-surge` · **Spec:** `docs/evidence/TW-470-ml-surge.spec.md`
**Rule:** skill rule 8 — every behavior-change diff carries quoted commands, a guarantees table, and a RED/GREEN summary. No result below is invented; all commands were run in `~/workspace/worktrees/tw-470-maya`.

## RED → GREEN narrative

**RED 1 (design flaw found by tests):** After wiring the ML scorer into `_equipment_graveyard_run` with the rule "past iff median_remaining ≤ 0", 4 of 7 `EquipmentGraveyardTest`/`HostileCellTest`/`ProductionShapeRegressionTest` cases failed. Root cause, confirmed by hand-derivation: for Weibull ρ > 0, `t* = (a^ρ + λ^ρ·ln2)^(1/ρ) − a > 0` for all finite ages — median remaining *never* reaches zero, so the "past" branch was dead code. The tests caught a rule that could never fire.

**GREEN 1:** Replaced with the cohort-median-life rule — past iff `age ≥ L50`, entering iff `0 < L50 − age ≤ 1yr`, where `L50 = λ·(ln2)^(1/ρ)`. Added `cohort_median_life_years` to `SurvivalEstimate`. Fixtures moved to ML-unambiguous ages (documented in spec §Piece B); test *intent* preserved.

**RED 2 (none):** the 23 new tests passed on first run after the L50 fix — the closed forms were verified against the independent reimplementation before commit.

## Quoted commands and results

```
$ cd ~/workspace/worktrees/tw-470-maya/backend
$ python3 -m pytest tests/test_equipment_survival.py tests/test_brief_actions.py -q
.....................                                                    [100%]
21 passed in 0.17s
```

```
$ python3 -m pytest tests/test_leak_engine.py -q          # after L50 fix + fixture moves
3 failed, 32 passed in 2.10s
# the 3 failures are BenchmarkGuardTest — verified identical on clean main @ cf65da3
```

```
$ python3 -m pytest tests/ -q -p no:cacheprovider \        # 12 pre-existing collection
    --ignore=tests/test_analytics_events.py \              # errors (missing httpx et al.)
    --ignore=tests/test_demo_audit_limit.py \              # — byte-identical file list
    --ignore=tests/test_demo_seed_resilience.py \          # on clean main
    --ignore=tests/test_integrations_platform.py \
    --ignore=tests/test_pilot_blockers.py \
    --ignore=tests/test_security_fixes.py \
    --ignore=tests/test_self_healing.py \
    --ignore=tests/test_stripe_checkout.py \
    --ignore=tests/test_tradeview_actions.py \
    --ignore=tests/test_tradeview_api.py \
    --ignore=tests/test_tradeview_leaks.py \
    --ignore=tests/test_visitor_events.py
3 failed, 63 passed in 1.37s
# clean main, same ignores: 3 failed, 40 passed → +23 new tests, zero regressions
```

```
$ python3 backend/scripts/export_survival_artifact.py --n 4000
wrote .../backend/app/data/survival_artifact.v1.json (20 groups, global n=4000)
global empirical coverage: 0.800 (nominal 0.80)
```

```
$ python3 -m ruff check backend/app/services/equipment_survival.py \
    backend/app/services/brief_actions.py backend/scripts/export_survival_artifact.py \
    backend/tests/test_equipment_survival.py backend/tests/test_brief_actions.py \
    backend/app/leak_engine/detectors/hvac.py backend/app/services/email.py \
    backend/app/services/scheduler.py backend/tests/test_leak_engine.py
All checks passed!
```

## Guarantees table

| Guarantee | How it's proven |
|---|---|
| Closed-form Weibull math correct | `test_closed_form_matches_independent_math` — adapter vs independent reimplementation across 5 ages, ±0.01 |
| Interval sanity (lo ≤ μ ≤ hi, lo ≥ 0) | `test_interval_sanity` |
| Older units score more urgent | `test_p_fail_monotone_in_age` |
| Every number labeled | `test_ml_rows_carry_provenance_labels`, `test_provenance_labels` — provenance + 80%-interval + synthetic calibration on every row |
| Undecidable cases never raise, never silent | `test_labeled_none_cases` (unknown type, negative/NaN age, bad horizon); `_ml_estimate_for` catches all; corrupt artifact → loud log → static-only |
| Brief always sends | `test_empty_on_scan_error_never_raises`; rendering wrapped belt-and-suspenders |
| No markup injection via entity names | `test_email_section_escapes_markup` pins `<script>` neutralization |
| Action ranking = expected $ desc, one per entity | `test_ranking_by_expected_recovery_desc`, `test_one_action_per_entity_dedupe` |
| Static-floor fallback preserved for out-of-schema types | `test_out_of_schema_type_uses_static_floor` (water_heater → static, labeled) |
| No regressions | suite delta vs clean main: +23 passed, failures identical (3 pre-existing) |
| Artifact reproducible | export re-run → byte-identical JSON; coverage self-check 0.800 = nominal 0.80 |

## Merge-gate check (2026-10-12)

lbt-os open PRs at push time: #3 (frontend-only), #4 (touches `backend/app/routers/tradeview.py` + tradeview tests). This branch touches neither file — verified via `gh pr view` file lists before push. No conflicts.

## Rosa fix-round (2026-10-12/13)

Rosa verdict on first review: REQUEST_CHANGES (1 blocker = this file; 4 nits).
Nits fixed in the fix-round commit: dead duplicate `log_ml` removed; `import logging`
moved to module top; call-list sort aligned to the customer-facing copy (ML rows by
p_fail desc, then static rows by age desc); artifact `fit_date` is UTC (noted, harmless).

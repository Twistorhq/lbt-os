<!-- Prepared by Twistor Holdings LLC -->

# TW-470 ML Surge — Spec: TW-212 + TW-213

**Branch:** `maya/tw-470-ml-surge` (worktree `~/workspace/worktrees/tw-470-maya`, from lbt-os main @ cf65da3)
**Author:** Maya Chen. **Status:** spec → implement → test → Rosa review. No merge without Justynn.
**Merge-gate check (2026-10-12):** lbt-os open PRs: #3 (frontend-only, no overlap), #4 (touches routers/tradeview.py + tradeview tests — my touch surface avoids both). No file conflicts.

## Assessment (no blind rebuilds)

- **TW-212 (next-best-action engine):** IMPLEMENTED by TW-306 — `backend/app/services/next_actions.py` docstring literally reads "implements the TW-212 brief." Merged to main, Rosa-approved. Remaining gap: the ticket requires the prioritized action queue **in the Morning Brief**. The brief (`scheduler.py` + `email.py`) has no action-queue section today. → Build the integration, not the engine.
- **TW-213 (equipment survival models):** IMPLEMENTED by math engine v1 (`maya/tw-374-427-419-math-engine`: Weibull AFT survival + hierarchical Bayes + conformal, Rosa-approved 2×, 24/24 tests, synthetic C-index 0.678 vs 0.523 baseline). Remaining gap: the lbt-os equipment-graveyard detector (`hvac.py::_equipment_graveyard_run`) still uses static `_TYPE_LIFE_FLOOR` band floors — the "ML dates instead of static band floors" wire-in never happened. → Build the wire-in, not the model.

## Math (named, with the machinery)

### 1. Weibull AFT survival (TW-374) — the replacement-timing model
For equipment with install-to-replacement time T, per (metro, equipment_type):
```
S(t | x) = exp( -( t / λ(x) )^ρ ),   λ(x) = λ₀ · exp(β_brand · x)
```
- **S(t)** = survival function = P(still running at age t). Weibull with shape ρ > 1 gives *increasing* hazard (wear-out): the older the unit, the faster failure approaches. That's the physical story of furnaces and ACs.
- **AFT (accelerated failure time):** covariates (brand tier x ∈ {-1,0,1}) *scale time itself* — a premium brand stretches the clock, a value brand compresses it.
- **Right-censoring:** a unit still running at age a contributes S(a), not a made-up failure date. Conditional remaining life: S_R(t) = S(a+t)/S(a).
- **Closed forms used by the adapter (no lifelines at runtime):**
  - median remaining: t* = (a^ρ + λ^ρ·ln2)^(1/ρ) − a
  - P(fail within H): 1 − S(a+H)/S(a)
- **Why effective:** the median is the *honest* point summary of a skewed remaining-life distribution; means would be dragged by the long tail. The conditional framing means a 10-year-old furnace and a 2-year-old one get different answers from the same model — age is information, not just a label.

### 2. Hierarchical Bayesian partial pooling (TW-427) — the sparse-group fix
```
log λ_m = μ_λ + β_brand·brand + u_m,   u_m ~ Normal(0, σ_λ)
```
Metro-level random effects u_m shrink toward the global mean. A metro with 3 observed failures borrows strength from the national pool instead of producing a wild estimate; a metro with 300 failures earns its own curve. **Why effective:** it kills the small-sample variance that would otherwise make Denver's estimate jump every time one furnace dies, while still letting real metro differences (altitude, hard water, install culture) show through. The deterministic fallback ladder in `survival.py` ((metro,type)→(metro)→global) is the same idea in closed form — the deployment artifact exports per-group Weibull params so lbt-os never runs PyMC.

### 3. Split-conformal intervals (TW-419) — honest uncertainty, not fake precision
```
s_i = |y_i − μ(x_i)| ;  q = Quantile_{⌈(1−α)(n+1)⌉/n}({s_i}) ;  C(x) = [max(μ−q, 0), μ+q]
```
Calibrated on held-out units with *known* remaining life (synthetic truth for now — provenance stamped `"synthetic"` on every row). **Guarantee:** P(Y_new ∈ C) ≥ 1−α under exchangeability — a finite-sample coverage promise with no distributional assumptions on the residuals. **Why effective:** Justynn's bar is "14–18 years, 80% interval" instead of "16.2 years." Conformal delivers exactly that, and the coverage is *measurable* (empirical_coverage backtest), so when real calibration data arrives (TW-428) we can watch the interval actually cover 80% of real outcomes. v1 synthetic coverage: 0.828 vs 0.80 nominal.

## Piece A — TW-212: action queue in the Morning Brief

**Schema (new):** `backend/app/services/brief_actions.py`
- `top_brief_actions(db, org_id, top_n=3) -> list[dict]` — runs `run_leak_scan`, takes `what_should_we_do` findings, `na.build_action_queue(...)`, dedupes to one action per entity (highest rank wins), returns top N as `{rank, title, entity_name, next_move, expected_recovery, dollars, detector}`.
- **Self-healing:** every exception → log + return `[]`. The brief sends regardless; a dead action section never kills the Monday email.

**Email** (`email.py::send_weekly_brief`): new "Your top money moves" section — ranked rows: `#1 $X expected — {title} — {next_move}`. HTML-escaped. Rendered only when actions exist.

**SMS** (`scheduler.py::_weekly_brief_sms`): append `Top move: {title} (${expected})` for rank 1 only (length discipline).

**Assumptions:** leak detectors already emit `recommended_action` (verified in hvac.py); `expected_recovery` is a heuristic-prior product, labeled as such downstream (already labeled in next_actions.py).

**Unit checks:** ranking order by expected_recovery desc; None-recovery last; one action per entity; empty list on scan exception; HTML escaping of entity names.

**Baseline:** brief without the section (current behavior) — the section must be strictly additive.

**Failure modes:** scan throws → section omitted, brief sends. Zero findings → section omitted. Twilio/SMTP down → unchanged existing behavior.

## Piece B — TW-213: ML survival dates in the graveyard detector

**New module (stdlib only):** `backend/app/services/equipment_survival.py`
- `load_artifact(path) -> dict` — validates `schema_version`, required keys.
- `score_asset(*, age_years, equipment_type, brand_tier, metro_key, today) -> SurvivalEstimate | None` — closed-form Weibull scoring (formulas above) + conformal interval from artifact q.
- Returns `None` (labeled fallback) for: unknown equipment_type, missing/negative age, no artifact loaded, non-positive λ/ρ. **Never raises into the detector.**
- `SurvivalEstimate`: `median_remaining_years`, `remaining_lo/hi_years` (80% interval), `p_fail_within_2y`, `provenance` (`"modeled:synthetic-calibration"`), `basis` (metro-specific vs global conformal, brand-tier assumption flag).
- Brand mapping: small `BRAND_TIER_MAP`; unknown brand → `"mid"` with `brand_assumed=True` in basis.

**Build tool (not shipped as runtime):** `backend/scripts/export_survival_artifact.py`
- Draws synthetic records WITH KNOWN TRUE remaining life from the v1
  generator (`synthetic.py`). The generator's Weibull ground-truth params
  are the model — no fitter runs in the build env (lifelines/PyMC
  unavailable), so the artifact validates the adapter's closed-form math
  against known truth, isolating adapter correctness from estimation noise.
  Brand effects are stored as exact per-tier multipliers (the generator's
  brand effect is not perfectly log-symmetric, so no beta approximation).
- Split-conformal q per metro on |truth − mu| residuals (v1 TW-419
  mechanics). Self-check on the export draw: global empirical coverage
  0.800 vs nominal 0.80.
- Emits `backend/app/data/survival_artifact.v1.json`:
  `{schema_version: 1, model_version: "synthetic-v1", calibration_provenance: "synthetic", groups: {"denver|furnace": {rho, lambda0_years, brand_mult, q_years, n_calibration, nominal_coverage}}, global: {q_years, n_calibration}}`
- The artifact is checked in so lbt-os runs with zero ML deps.

**Metro handling:** orgs carry no metro field. The adapter defaults to the
home launch metro (`denver`) and labels every estimate
`metro_basis="denver (org metro unknown)"`. Per-org metro is the documented
upgrade path (the artifact already carries all four metros).

**Detector wiring** (`hvac.py::_equipment_graveyard_run`):
- Per asset with a valid install_date: `score_asset(...)`. On success the
  ML rule replaces the static floor:
  - **past** iff `age_years >= L50`, where `L50 = λ·(ln2)^(1/ρ)` is the
    cohort median replacement age (half the cohort replaced by L50).
    Principled reading of "past expected life": the unit outlived its
    cohort's median.
  - **entering** iff `0 < L50 − age_years ≤ 1yr` (inside one window-year of
    the cohort median).
  - Finding info gains `replacement_basis: "ml"`, the 80% remaining-life
    window, `p_fail_2y`, and `cohort_median_life_years`.
- On `None` (unknown type, no install date, no artifact): existing
  static-floor path unchanged, `replacement_basis: "static-floor"`.
- Types outside the v1 schema (water heaters, springs, openers): static
  floor — documented as the v1 coverage gap, not silently modeled.
- **Test impact (documented, not hidden):** the old graveyard fixtures were
  tuned to static-floor thresholds (furnace 15y, AC 10y) and are ambiguous
  under ML semantics — TW-213 explicitly replaces those floors, so the
  fixtures move to ages unambiguous under the ML rule (24y furnace → past,
  13.5y AC → entering). Test *intent* (past→happened, nearing→will,
  hostile cells don't crash) is preserved; placement assertions follow the
  new semantics. A new test pins the static-floor fallback for
  out-of-schema types.

**Provenance rule (Justynn's bar):** every graveyard row is labeled `modeled estimate (80% interval, synthetic calibration)` or `static floor (heuristic)` — never a bare number.

**Calibration plan:** synthetic now (machinery validation, labeled). Real calibration on TW-428 ingestion: re-run export script with provenance `"real"`, ship artifact v2, watch `empirical_coverage` on holdout ≥ 0.80 or roll back to v1.

**Backtest sketch:** cross-check adapter medians against math-engine `SurvivalModel.median_remaining` on 200 synthetic units (|Δ| < 1e−6); interval coverage on synthetic holdout ≥ nominal − 0.05.

**Baseline:** current static-floor detector — ML path must not reduce detector recall on the existing test fixtures.

**Failure modes:** corrupt artifact → adapter refuses to load, detector runs static-only, loud log. Future-dated install → age negative → fallback. Unknown metro → global conformal q + flag.

## Tests
- `backend/tests/test_brief_actions.py` — ranking, dedupe, empty-on-error, HTML escaping, SMS line format.
- `backend/tests/test_equipment_survival.py` — closed-form vs math-engine cross-check, interval sanity (lo ≤ mu ≤ hi, lo ≥ 0), all None-cases, artifact schema rejection, brand-tier fallback flag.
- Full backend suite green before Rosa.

## Done when
Both pieces implemented + tested, artifact generated and checked in, docs updated, Rosa review dispatched same turn, tickets commented. Merge only on Justynn's exact word.

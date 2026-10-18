#!/usr/bin/env python3
# Prepared by Twistor Holdings LLC.
"""TW-213 — export the ML survival artifact for lbt-os (build-time tool).

NOT shipped as runtime code. Run once (and again when TW-428 delivers real
calibration data), check the JSON artifact into backend/app/data/.

Method (honest synthetic-calibration machinery validation):
  1. Draw synthetic equipment records WITH KNOWN TRUE remaining life from
     the math-engine v1 generator (synthetic.py). The generator's Weibull
     ground-truth params are the model — no fitter runs here, so the
     artifact tests the runtime adapter's closed-form math against known
     truth, isolating adapter correctness from estimation noise.
  2. Compute each record's median remaining life with the SAME closed forms
     the lbt-os adapter uses (median_remaining formula in
     equipment_survival.py).
  3. Split-conformal calibration per metro on |truth - mu| residuals
     (v1 TW-419 mechanics): q = quantile_{ceil((1-alpha)(n+1))/n}.
  4. Emit backend/app/data/survival_artifact.v1.json.

Every number downstream is labeled "synthetic calibration" until this
script is re-run with provenance="real" on TW-428 data.

Usage:
    python3 backend/scripts/export_survival_artifact.py [--seed 7] [--n 4000]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

# --- import the v1 generator (read-only; the branch is Rosa-approved) ---
_MATH_ENGINE_SRC = (
    Path(__file__).resolve().parents[3]
    / ".." / "worktrees" / "tw-math-engine" / "src"
)
_MATH_ENGINE_SRC = _MATH_ENGINE_SRC.resolve()
if not _MATH_ENGINE_SRC.exists():
    # fall back to an explicit env override for CI/dev machines
    import os

    _MATH_ENGINE_SRC = Path(os.environ.get("TW_MATH_ENGINE_SRC", str(_MATH_ENGINE_SRC)))
sys.path.insert(0, str(_MATH_ENGINE_SRC))

# NOTE: we do NOT `from twistor_core.math_engine.synthetic import ...` here:
# the package __init__ pulls in hierarchical -> pymc, which is unavailable in
# this build env. _load_synthetic() loads synthetic.py standalone instead.

ALPHA = 0.2
METROS = ("denver", "phoenix", "austin", "seattle")
BRAND_CODE = {"value": -1.0, "mid": 0.0, "premium": 1.0}


def _lam(metro: str, etype: str, brand: str) -> tuple[float, float]:
    """Ground-truth (rho, lambda) for one synthetic unit."""
    k, lam0 = _TYPE_PARAMS[etype]
    lam = lam0 * _BRAND_SCALE_MULT[brand]
    if etype in ("ac", "heat_pump"):
        lam = lam * _METRO_AC_WEAR.get(metro, 1.0)
    return k, lam


def _median_remaining(age: float, rho: float, lam: float) -> float:
    return max((age**rho + (lam**rho) * math.log(2)) ** (1.0 / rho) - age, 0.0)


def _conformal_q(residuals: list[float], alpha: float = ALPHA) -> float:
    scores = np.sort(np.abs(np.asarray(residuals, dtype=float)))
    n = len(scores)
    if n < 10:
        raise ValueError(f"need >= 10 calibration points, got {n}")
    level = min(math.ceil((1.0 - alpha) * (n + 1)) / n, 1.0)
    return float(np.quantile(scores, level, method="higher"))


def _load_synthetic():
    """Load synthetic.py standalone (pymc unavailable in build env).

    synthetic.py needs only `schemas` (dataclasses, no heavy deps), so we
    load both files directly instead of importing the package (whose
    __init__ pulls in hierarchical -> pymc).
    """
    import importlib.util
    import types

    src = _MATH_ENGINE_SRC / "twistor_core" / "math_engine"
    pkg = types.ModuleType("tw_me_standalone")
    pkg.__path__ = [str(src)]  # type: ignore[attr-defined]
    sys.modules["tw_me_standalone"] = pkg
    for mod_name in ("schemas", "synthetic"):
        spec = importlib.util.spec_from_file_location(
            f"tw_me_standalone.{mod_name}", src / f"{mod_name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "tw_me_standalone"  # before exec: fixes `from .schemas`
        sys.modules[f"tw_me_standalone.{mod_name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["tw_me_standalone.synthetic"]


# Populated by _load_synthetic() at runtime (module globals for _lam).
_BRAND_SCALE_MULT: dict = {}
_METRO_AC_WEAR: dict = {}
_TYPE_PARAMS: dict = {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n", type=int, default=4000)
    args = ap.parse_args()

    syn = _load_synthetic()
    global _BRAND_SCALE_MULT, _METRO_AC_WEAR, _TYPE_PARAMS
    _BRAND_SCALE_MULT = dict(syn.BRAND_SCALE_MULT)
    _METRO_AC_WEAR = dict(syn.METRO_AC_WEAR)
    _TYPE_PARAMS = dict(syn.TYPE_PARAMS)
    generate_with_truth = syn.generate_with_truth

    records, truths = generate_with_truth(args.n, metros=METROS, seed=args.seed)

    # mu per record via the adapter's closed forms; residuals vs known truth
    by_metro: dict[str, list[float]] = {}
    all_res: list[float] = []
    for r, truth in zip(records, truths, strict=True):
        rho, lam = _lam(r.metro, r.equipment_type, r.brand_tier)
        mu = _median_remaining(r.time_years, rho, lam)
        res = abs(truth - mu)
        by_metro.setdefault(r.metro, []).append(res)
        all_res.append(res)

    groups: dict[str, dict] = {}
    for metro in METROS:
        q = _conformal_q(by_metro[metro])
        for etype, (k, lam0) in _TYPE_PARAMS.items():
            wear_note = (
                f"metro AC wear x{_METRO_AC_WEAR.get(metro, 1.0)}"
                if etype in ("ac", "heat_pump")
                else "no metro wear"
            )
            groups[f"{metro}|{etype}"] = {
                "rho": k,
                "lambda0_years": round(lam0 * (_METRO_AC_WEAR.get(metro, 1.0) if etype in ("ac", "heat_pump") else 1.0), 4),
                "brand_mult": dict(_BRAND_SCALE_MULT),
                "q_years": round(q, 4),
                "n_calibration": len(by_metro[metro]),
                "nominal_coverage": round(1.0 - ALPHA, 2),
                "note": wear_note,
            }

    artifact = {
        "schema_version": 1,
        "model_version": "synthetic-v1",
        "fit_date": date.today().isoformat(),
        "calibration_provenance": "synthetic",
        "nominal_coverage": round(1.0 - ALPHA, 2),
        "alpha": ALPHA,
        "method": (
            "Weibull AFT closed forms on synthetic generator ground-truth "
            "params; split-conformal q per metro on |truth - mu| residuals. "
            "Machinery validation only — NOT real calibration."
        ),
        "equipment_types": sorted(_TYPE_PARAMS),
        "metros": list(METROS),
        "groups": groups,
        "global": {
            "q_years": round(_conformal_q(all_res), 4),
            "n_calibration": len(all_res),
        },
    }

    out = (
        Path(__file__).resolve().parents[1] / "app" / "data" / "survival_artifact.v1.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {out} ({len(groups)} groups, global n={len(all_res)})")

    # coverage self-check on the same draw (machinery validation)
    inside = sum(1 for x in all_res if x <= artifact["global"]["q_years"])
    print(f"global empirical coverage: {inside/len(all_res):.3f} (nominal 0.80)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

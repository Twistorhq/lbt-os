# Prepared by Twistor Holdings LLC.
"""TW-213 wire-in — ML equipment-survival scoring for lbt-os (TW-470 surge).

Stdlib only. This module NEVER fits a model and NEVER imports the math
engine (which needs lifelines/PyMC). It scores assets against a checked-in
JSON artifact exported from the math engine v1 pipeline
(`backend/scripts/export_survival_artifact.py`).

Math (closed forms of the v1 Weibull AFT specification):
    S(t) = exp( -(t / lam)^rho ),   lam = lambda0 * brand_mult[tier]
    S_R(t) = S(a + t) / S(a)                       (conditional on age a)
    median remaining t*:  S_R(t*) = 0.5
        => t* = (a^rho + lam^rho * ln2)^(1/rho) - a
    P(fail within H) = 1 - S_R(H)
    80% interval (split-conformal, v1 TW-419): [max(mu - q, 0), mu + q]

Provenance rule (Justynn's bar): every estimate carries
`provenance="modeled:synthetic-calibration"` until TW-428 delivers real
calibration data. Anything the adapter cannot score returns None — the
caller falls back to the static floor and labels the row accordingly.
A bare number with no provenance never leaves this module.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = 1
NOMINAL_COVERAGE = 0.80
DEFAULT_HORIZON_YEARS = 2.0

# Orgs carry no metro field. The home launch metro is the honest default —
# every estimate labels this assumption (metro_basis), and the spec records
# per-org metro as the upgrade path.
DEFAULT_METRO = "denver"

# Coarse brand -> tier mapping. Unknown brands assume "mid" and the
# estimate is flagged (brand_assumed=True) — a wrong tier prior is
# worse than a labeled assumption.
BRAND_TIER_MAP = {
    "carrier": "premium",
    "trane": "premium",
    "lennox": "premium",
    "rheem": "mid",
    "goodman": "mid",
    "york": "mid",
    "amana": "mid",
    "bryant": "mid",
}


@dataclass(frozen=True)
class SurvivalEstimate:
    """One asset's ML replacement-timing estimate. Years throughout."""

    median_remaining_years: float
    remaining_lo_years: float  # 80% conformal interval, lower
    remaining_hi_years: float  # 80% conformal interval, upper
    p_fail_within_2y: float
    cohort_median_life_years: float  # L50 = lam*(ln2)^(1/rho): the age by
    # which half the cohort is replaced. The detector's "past expected life"
    # trigger: age >= L50.
    provenance: str  # "modeled:synthetic-calibration" (until TW-428)
    metro_basis: str  # "metro" | "denver (org metro unknown)"
    brand_tier: str
    brand_assumed: bool


def brand_tier_for(brand: str | None) -> tuple[str, bool]:
    """Map a brand name to a tier. Unknown -> ("mid", True)."""
    if brand:
        tier = BRAND_TIER_MAP.get(brand.strip().lower())
        if tier:
            return tier, False
    return "mid", True


def load_artifact(path: str | Path) -> dict[str, Any]:
    """Load and validate a survival artifact. Raises on any defect.

    A corrupt artifact must fail LOUDLY at load time (build/deploy), never
    silently at scoring time — the detector then runs static-only.
    """
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError("artifact root must be an object")
    if raw.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {raw.get('schema_version')!r} "
            f"(want {ARTIFACT_SCHEMA_VERSION})"
        )
    for key in ("calibration_provenance", "groups", "global"):
        if key not in raw:
            raise ValueError(f"artifact missing required key: {key}")
    if not isinstance(raw["groups"], dict) or not raw["groups"]:
        raise ValueError("artifact 'groups' must be a non-empty object")
    for gkey, g in raw["groups"].items():
        for fkey in ("rho", "lambda0_years", "brand_mult", "q_years"):
            if fkey not in g:
                raise ValueError(f"group {gkey!r} missing {fkey!r}")
        if not (g["rho"] > 0 and g["lambda0_years"] > 0 and g["q_years"] >= 0):
            raise ValueError(f"group {gkey!r} has non-physical parameters")
    return raw


class SurvivalScorer:
    """Scores assets against a loaded artifact. Construct once, reuse."""

    def __init__(self, artifact: dict[str, Any]):
        self._artifact = artifact
        self.provenance = str(artifact["calibration_provenance"])
        self._global_q = float(artifact["global"]["q_years"])

    @classmethod
    def from_path(cls, path: str | Path) -> "SurvivalScorer":
        return cls(load_artifact(path))

    def _group(self, metro_key: str, equipment_type: str) -> tuple[dict, str] | None:
        groups = self._artifact["groups"]
        hit = groups.get(f"{metro_key}|{equipment_type}")
        if hit is not None:
            return hit, "metro"
        # Orgs carry no metro: fall back to the home launch metro, labeled.
        hit = groups.get(f"{DEFAULT_METRO}|{equipment_type}")
        if hit is not None:
            return hit, f"{DEFAULT_METRO} (org metro unknown)"
        return None

    def score_asset(
        self,
        *,
        age_years: float,
        equipment_type: str,
        brand_tier: str = "mid",
        metro_key: str = "",
        horizon_years: float = DEFAULT_HORIZON_YEARS,
        brand_assumed: bool = False,
    ) -> SurvivalEstimate | None:
        """Score one asset. Returns None when it cannot score honestly.

        None-cases (all labeled fallbacks at the call site, never silent):
        unknown equipment_type, missing/negative age, non-positive params,
        horizon <= 0.
        """
        if age_years is None or not math.isfinite(age_years) or age_years < 0:
            return None
        if horizon_years <= 0:
            return None
        grp = self._group(metro_key, equipment_type)
        if grp is None:
            return None
        g, metro_basis_raw = grp
        rho = float(g["rho"])
        lam0 = float(g["lambda0_years"])
        if not (rho > 0 and lam0 > 0):
            return None
        brand_mult = g["brand_mult"].get(brand_tier, 1.0)
        lam = lam0 * float(brand_mult)
        if lam <= 0:
            return None

        a = float(age_years)
        # Median remaining: t* = (a^rho + lam^rho * ln2)^(1/rho) - a
        t_star = (a**rho + (lam**rho) * math.log(2)) ** (1.0 / rho) - a
        t_star = max(t_star, 0.0)

        # Cohort median life: the age by which half the cohort is replaced.
        # L50 = lam * (ln2)^(1/rho). The detector's "past expected life" rule.
        l50 = lam * (math.log(2) ** (1.0 / rho))

        # P(fail within H) = 1 - S(a+H)/S(a)
        s_a = math.exp(-((a / lam) ** rho)) if a > 0 else 1.0
        s_ah = math.exp(-(((a + horizon_years) / lam) ** rho))
        p_fail = max(0.0, min(1.0, 1.0 - s_ah / s_a)) if s_a > 0 else 1.0

        q = float(g.get("q_years", self._global_q))
        lo = max(t_star - q, 0.0)
        hi = t_star + q
        return SurvivalEstimate(
            median_remaining_years=round(t_star, 2),
            remaining_lo_years=round(lo, 2),
            remaining_hi_years=round(hi, 2),
            p_fail_within_2y=round(p_fail, 4),
            cohort_median_life_years=round(l50, 2),
            provenance=f"modeled:{self.provenance}-calibration",
            metro_basis=metro_basis_raw,
            brand_tier=brand_tier,
            brand_assumed=brand_assumed,
        )

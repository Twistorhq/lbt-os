"""Launch-gate metrics for the Twistor Verification Engine (TW-649).

Definitions follow TW-646 spec Section 5.3. A score is a similarity in
[0, 1]; higher is more similar; an attempt is accepted when
score >= threshold. Stage adapters are responsible for normalizing
their native outputs (for example dlib's distance) into this shape.

The gate is fail-honest: a group with too few attempts to measure is
INSUFFICIENT_DATA, never a pass by silence. A measured failure outranks
insufficient data, because a breach we can see is a breach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


@dataclass(frozen=True)
class ScoredAttempt:
    """One scored verification attempt.

    `group` is whatever demographic grouping the registered dataset
    provides; the harness reports groups, it never invents them.
    `genuine` True means a mated attempt (the same person), False an
    impostor (non-mated) attempt.
    """

    group: str
    genuine: bool
    score: float


@dataclass(frozen=True)
class RateSet:
    genuine_count: int
    impostor_count: int
    false_matches: int
    false_non_matches: int

    @property
    def fmr(self) -> Optional[float]:
        """False-match rate. None when unmeasured (no impostor attempts)."""
        if self.impostor_count == 0:
            return None
        return self.false_matches / self.impostor_count

    @property
    def fnmr(self) -> Optional[float]:
        """False-non-match rate. None when unmeasured (no genuine attempts)."""
        if self.genuine_count == 0:
            return None
        return self.false_non_matches / self.genuine_count

    def as_dict(self) -> dict:
        return {
            "genuine_count": self.genuine_count,
            "impostor_count": self.impostor_count,
            "false_matches": self.false_matches,
            "false_non_matches": self.false_non_matches,
            "fmr": self.fmr,
            "fnmr": self.fnmr,
        }


def rates_for(attempts: Iterable[ScoredAttempt], threshold: float) -> RateSet:
    genuine_count = impostor_count = 0
    false_matches = false_non_matches = 0
    for attempt in attempts:
        accepted = attempt.score >= threshold
        if attempt.genuine:
            genuine_count += 1
            if not accepted:
                false_non_matches += 1
        else:
            impostor_count += 1
            if accepted:
                false_matches += 1
    return RateSet(
        genuine_count=genuine_count,
        impostor_count=impostor_count,
        false_matches=false_matches,
        false_non_matches=false_non_matches,
    )


def per_group(
    attempts: Iterable[ScoredAttempt], threshold: float
) -> dict[str, RateSet]:
    """Rates broken out by demographic group, keyed in sorted order."""
    grouped: dict[str, list[ScoredAttempt]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.group, []).append(attempt)
    return {name: rates_for(grouped[name], threshold) for name in sorted(grouped)}


def max_gap(rate_sets: dict[str, RateSet], metric: str) -> Optional[float]:
    """Largest cross-group spread of a rate (max minus min).

    Groups whose rate is unmeasured (None) are skipped. Returns None
    when fewer than two groups carry a measurement: a gap needs two
    sides, and an unmeasurable gap must never read as zero.
    """
    values = [getattr(rs, metric) for rs in rate_sets.values()]
    measured = [v for v in values if v is not None]
    if len(measured) < 2:
        return None
    return max(measured) - min(measured)


@dataclass(frozen=True)
class LivenessRates:
    apcer: Optional[float]  # attacks accepted as bona fide / attacks
    bpcer: Optional[float]  # bona fide rejected as attack / bona fide


def liveness_rates(
    presentations: Iterable[tuple[str, bool]],
) -> LivenessRates:
    """ISO 30107-3 rates from (kind, accepted_as_bonafide) presentations.

    kind is "attack" or "bonafide". APCER is the share of attack
    presentations the liveness stage wrongly accepted; BPCER is the
    share of bona fide presentations it wrongly rejected.
    """
    attacks = attacks_accepted = 0
    bonafide = bonafide_rejected = 0
    for kind, accepted in presentations:
        if kind == "attack":
            attacks += 1
            if accepted:
                attacks_accepted += 1
        elif kind == "bonafide":
            bonafide += 1
            if not accepted:
                bonafide_rejected += 1
        else:
            raise ValueError(f"unknown presentation kind: {kind!r}")
    return LivenessRates(
        apcer=(attacks_accepted / attacks) if attacks else None,
        bpcer=(bonafide_rejected / bonafide) if bonafide else None,
    )


class GateStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class GateCriteria:
    """Launch-gate thresholds (TW-646 spec Section 5.3).

    fmr_max carries the spec's planning target (1 in 10,000). The FNMR
    ceiling, the cross-group gap ceiling, and the minimum sample sizes
    are planning values: they make the gate runnable today, and the
    final numbers are set with the measured report in front of Rosa
    and Justynn before T2 opens. Raising a sample floor is always a
    tightening; the gate never passes on silence.
    """

    fmr_max: float = 1e-4
    fnmr_max: float = 0.10
    fnmr_gap_max: float = 0.05
    min_genuine_per_group: int = 300
    min_impostor_per_group: int = 5000


@dataclass(frozen=True)
class GateReport:
    status: GateStatus
    failures: list[str] = field(default_factory=list)
    insufficient: list[str] = field(default_factory=list)
    groups: dict[str, RateSet] = field(default_factory=dict)
    overall: Optional[RateSet] = None


def evaluate_gate(
    attempts: Iterable[ScoredAttempt],
    threshold: float,
    criteria: GateCriteria = GateCriteria(),
) -> GateReport:
    """Evaluate the launch gate over scored attempts at a threshold."""
    attempts = list(attempts)
    groups = per_group(attempts, threshold)
    overall = rates_for(attempts, threshold)
    failures: list[str] = []
    insufficient: list[str] = []

    if not attempts:
        insufficient.append("no attempts supplied")
        return GateReport(
            status=GateStatus.INSUFFICIENT_DATA,
            failures=failures,
            insufficient=insufficient,
            groups=groups,
            overall=overall,
        )

    for name, rs in groups.items():
        if rs.genuine_count < criteria.min_genuine_per_group:
            insufficient.append(
                f"group {name}: {rs.genuine_count} genuine attempts "
                f"< minimum {criteria.min_genuine_per_group}"
            )
        if rs.impostor_count < criteria.min_impostor_per_group:
            insufficient.append(
                f"group {name}: {rs.impostor_count} impostor attempts "
                f"< minimum {criteria.min_impostor_per_group}"
            )
        if rs.fmr is not None and rs.fmr > criteria.fmr_max:
            failures.append(
                f"group {name}: fmr {rs.fmr:.6f} exceeds ceiling "
                f"{criteria.fmr_max}"
            )
        if rs.fnmr is not None and rs.fnmr > criteria.fnmr_max:
            failures.append(
                f"group {name}: fnmr {rs.fnmr:.4f} exceeds ceiling "
                f"{criteria.fnmr_max}"
            )

    if overall.fmr is not None and overall.fmr > criteria.fmr_max:
        failures.append(
            f"overall: fmr {overall.fmr:.6f} exceeds ceiling {criteria.fmr_max}"
        )

    gap = max_gap(groups, "fnmr")
    if gap is not None and gap > criteria.fnmr_gap_max:
        failures.append(
            f"demographic fnmr gap {gap:.4f} exceeds ceiling "
            f"{criteria.fnmr_gap_max}"
        )

    if failures:
        status = GateStatus.FAIL
    elif insufficient:
        status = GateStatus.INSUFFICIENT_DATA
    else:
        status = GateStatus.PASS
    return GateReport(
        status=status,
        failures=failures,
        insufficient=insufficient,
        groups=groups,
        overall=overall,
    )

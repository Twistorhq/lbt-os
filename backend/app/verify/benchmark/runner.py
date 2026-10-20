"""Benchmark runner (TW-649): scored attempts in, gate report out.

The runner is deliberately dumb about images: stage adapters produce
ScoredAttempts (directly, or by scoring pairs a dataset defines), and
the runner applies the launch-gate math from metrics.py and renders a
JSON-serializable report. Synthetic attempts exist so the harness can
be smoke-tested end to end with no dataset and no faces; synthetic
numbers are never gate evidence (registry: twistor-synth-smoke).

Run a smoke benchmark:
    python -m app.verify.benchmark.runner --seed 7 --out report.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict
from typing import Iterable, Optional

from app.verify.benchmark.metrics import (
    GateCriteria,
    ScoredAttempt,
    evaluate_gate,
    liveness_rates,
    per_group,
    rates_for,
)

# Family names, per repo tradition. In a real run the group labels are
# whatever the registered dataset provides; the harness never invents
# a demographic grouping of its own.
_SMOKE_GROUPS = ("ama", "kofi")


def synthetic_attempts(
    seed: int = 7,
    per_group_genuine: int = 300,
    per_group_impostor: int = 300,
    groups: tuple[str, ...] = _SMOKE_GROUPS,
) -> list[ScoredAttempt]:
    """Deterministic synthetic scored attempts (smoke scale).

    Genuine scores center high, impostor scores low, with a slight
    per-group difficulty offset so the demographic-gap math has
    something real to measure. Same seed, same list, every time.
    """
    rng = random.Random(seed)
    attempts: list[ScoredAttempt] = []
    for idx, group in enumerate(groups):
        offset = 0.03 * idx
        for _ in range(per_group_genuine):
            score = max(0.0, min(1.0, rng.gauss(0.88 - offset, 0.08)))
            attempts.append(ScoredAttempt(group=group, genuine=True, score=score))
        for _ in range(per_group_impostor):
            score = max(0.0, min(1.0, rng.gauss(0.25 + offset, 0.10)))
            attempts.append(ScoredAttempt(group=group, genuine=False, score=score))
    return attempts


def run_benchmark(
    attempts: Iterable[ScoredAttempt],
    threshold: float,
    criteria: GateCriteria = GateCriteria(),
    liveness: Optional[Iterable[tuple[str, bool]]] = None,
    expected_groups: Iterable[str] = (),
) -> dict:
    """Apply the launch gate and render the serializable report.

    The report is the launch evidence shape from TW-646 Section 5.3:
    overall accuracy, FMR/FNMR overall and broken out by demographic
    group, the demographic FNMR gap, and the gate verdict with its
    reasons. INSUFFICIENT_DATA is a verdict, not an absence of one.

    `expected_groups` carries the coverage set the run's dataset
    declares in registry.py; an expected group with no attempts is
    reported INSUFFICIENT_DATA by name, never silently dropped.
    """
    attempts = list(attempts)
    gate = evaluate_gate(attempts, threshold, criteria, expected_groups=expected_groups)
    overall = rates_for(attempts, threshold)
    groups = per_group(attempts, threshold)

    gate_section: dict = {
        "status": gate.status.value,
        "failures": gate.failures,
        "insufficient": gate.insufficient,
        "criteria": asdict(criteria),
    }
    if expected_groups:
        gate_section["expected_groups"] = sorted(set(expected_groups))

    report: dict = {
        "threshold": threshold,
        "overall": overall.as_dict(),
        "groups": {name: rs.as_dict() for name, rs in groups.items()},
        "gate": gate_section,
    }
    if liveness is not None:
        rates = liveness_rates(liveness)
        report["liveness"] = {"apcer": rates.apcer, "bpcer": rates.bpcer}
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TW-649 benchmark harness (synthetic smoke run)"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--per-group-genuine", type=int, default=60)
    parser.add_argument("--per-group-impostor", type=int, default=60)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    attempts = synthetic_attempts(
        seed=args.seed,
        per_group_genuine=args.per_group_genuine,
        per_group_impostor=args.per_group_impostor,
    )
    report = run_benchmark(attempts, args.threshold)
    report["dataset"] = "twistor-synth-smoke"
    report["smoke"] = True

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    overall = report["overall"]
    print(
        "smoke run: "
        f"n={overall['genuine_count'] + overall['impostor_count']} "
        f"fmr={overall['fmr']} fnmr={overall['fnmr']} "
        f"gate={report['gate']['status']} "
        "(synthetic, smoke scale: not gate evidence)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

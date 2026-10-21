"""TW-658: benchmark gate wiring for the engine (TDD RED).

The TW-649 harness computes the gate; this wiring is what a real
gate run calls. It pulls the coverage set from the registry entry
by default (Rosa's TW-649 observation), stamps the dataset and the
engine version into the report, and refuses to run the consented
volunteer gate until the collection protocol's coverage strata are
declared: labels are self-described, so the strata come from the
protocol, never from the harness.

The synthetic-path proof at the bottom scores synthetic pairs
through the engine's own face scorer and runs the gate over the
result. It proves the wiring end to end; the numbers are synthetic
and are never gate evidence.
"""

from __future__ import annotations

import math

import pytest

from app.verify.benchmark.gate import (
    GateConfigurationError,
    declare_volunteer_strata,
    run_gate,
    volunteer_strata,
)
from app.verify.benchmark.metrics import ScoredAttempt
from app.verify.benchmark.registry import get as registry_get
from app.verify.engine.benchmarking import (
    EngineFaceScorer,
    LivenessScorer,
    PairSpec,
)
from app.verify.engine.config import ENGINE_VERSION


def _attempts(group: str, genuine: int, impostor: int) -> list[ScoredAttempt]:
    return [ScoredAttempt(group, True, 0.9)] * genuine + [
        ScoredAttempt(group, False, 0.1)
    ] * impostor


class TestRunGate:
    def test_expected_groups_default_from_registry(self):
        # FairFace declares its seven published race labels; a run
        # covering none of them reports every one INSUFFICIENT_DATA.
        report = run_gate("fairface", [], threshold=0.55)
        assert report["dataset"] == "fairface"
        assert report["engine_version"] == ENGINE_VERSION
        assert report["gate"]["status"] == "insufficient_data"
        assert set(report["gate"]["expected_groups"]) == set(
            registry_get("fairface").expected_groups
        )
        joined = " ".join(report["gate"]["insufficient"])
        for name in registry_get("fairface").expected_groups:
            assert name in joined

    def test_explicit_expected_groups_override(self):
        attempts = _attempts("ama", 300, 5000)
        report = run_gate(
            "twistor-synth-smoke",
            attempts,
            threshold=0.55,
            expected_groups=("ama",),
        )
        assert report["gate"]["status"] == "pass"
        assert report["gate"]["expected_groups"] == ["ama"]

    def test_unknown_dataset_is_an_error(self):
        with pytest.raises(KeyError):
            run_gate("no-such-dataset", [], threshold=0.55)


class TestVolunteerStrata:
    def test_gate_refuses_until_strata_declared(self):
        assert volunteer_strata() == ()
        with pytest.raises(GateConfigurationError):
            run_gate("twistor-consented-volunteers", [], threshold=0.55)

    def test_declared_strata_become_the_coverage_set(self):
        declare_volunteer_strata(["ama", "kofi", "ama"])
        assert volunteer_strata() == ("ama", "kofi")
        report = run_gate("twistor-consented-volunteers", [], threshold=0.55)
        assert report["gate"]["expected_groups"] == ["ama", "kofi"]
        assert report["gate"]["status"] == "insufficient_data"

    def test_empty_strata_declaration_is_rejected(self):
        with pytest.raises(GateConfigurationError):
            declare_volunteer_strata([])


class _PairEmbed:
    """Deterministic fake embedder: vectors keyed by image name."""

    def __init__(self, vectors: dict[str, tuple[float, ...]]):
        self.vectors = vectors

    def embed(self, image_ref: str) -> tuple[float, ...]:
        return self.vectors[image_ref]


def _unit(degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return (math.cos(rad), math.sin(rad))


class TestEngineScorerSyntheticPath:
    def test_pairs_score_through_engine_math(self):
        vectors = {
            "ama-1": _unit(5),
            "ama-2": _unit(9),
            "kofi-1": _unit(95),
        }
        scorer = EngineFaceScorer(_PairEmbed(vectors))
        attempts = scorer.score_pairs(
            [
                PairSpec("ama", True, "ama-1", "ama-2"),
                PairSpec("ama", False, "ama-1", "kofi-1"),
            ]
        )
        assert attempts[0].genuine and attempts[0].score > 0.99
        assert not attempts[1].genuine and attempts[1].score < 0.2

    def test_synthetic_gate_run_end_to_end(self):
        # 300 genuine + 5000 impostor synthetic pairs per group at a
        # clean separation: the gate math runs over engine-produced
        # attempts and passes. Synthetic: wiring proof, not evidence.
        vectors: dict[str, tuple[float, ...]] = {}
        pairs: list[PairSpec] = []
        for group, base in (("ama", 0.0), ("kofi", 40.0)):
            for i in range(300):
                a, b = f"{group}-g{i}a", f"{group}-g{i}b"
                vectors[a] = _unit(base + (i % 7))
                vectors[b] = _unit(base + (i % 7) + 2)
                pairs.append(PairSpec(group, True, a, b))
            for i in range(5000):
                a, b = f"{group}-i{i}a", f"{group}-i{i}b"
                vectors[a] = _unit(base)
                vectors[b] = _unit(base + 85)
                pairs.append(PairSpec(group, False, a, b))
        scorer = EngineFaceScorer(_PairEmbed(vectors))
        attempts = scorer.score_pairs(pairs)
        report = run_gate(
            "twistor-synth-smoke",
            attempts,
            threshold=0.55,
            expected_groups=("ama", "kofi"),
        )
        assert report["gate"]["status"] == "pass"
        assert report["overall"]["fmr"] == 0.0
        assert report["overall"]["fnmr"] == 0.0

    def test_liveness_scorer_shapes_presentations(self):
        class FakeLive:
            def __init__(self):
                self.probs = {"real-1": 0.98, "print-1": 0.05}

            def bona_fide_probability(self, image_ref: str) -> float:
                return self.probs[image_ref]

        scorer = LivenessScorer(FakeLive(), floor=0.5)
        presentations = scorer.presentations(
            [("bonafide", "real-1"), ("attack", "print-1")]
        )
        assert presentations == [("bonafide", True), ("attack", False)]

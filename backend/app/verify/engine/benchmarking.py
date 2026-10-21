"""Engine scorers for the benchmark gate (TW-658).

The TW-649 harness consumes ScoredAttempts; these scorers produce
them with the engine's own stage components, so a gate run measures
the pipeline that would actually verify members, not a parallel
one. Pair scoring uses the engine's cosine banding math on
embeddings from the embed port (the dlib stack in production, a
deterministic fake under test). Liveness presentations come from
the liveness port against the engine's review floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.verify.benchmark.metrics import ScoredAttempt

from .face import cosine_similarity
from .ports import EmbedPort, LivenessPort


@dataclass(frozen=True)
class PairSpec:
    """One benchmark pair: two images, a group, a mating truth."""

    group: str
    genuine: bool
    image_a: str
    image_b: str


class EngineFaceScorer:
    """Scores image pairs into harness attempts via the embed port."""

    def __init__(self, embed_port: EmbedPort):
        self._embed = embed_port
        self._cache: dict[str, tuple[float, ...]] = {}

    def _embedding(self, image_path: str) -> tuple[float, ...]:
        if image_path not in self._cache:
            self._cache[image_path] = self._embed.embed(image_path)
        return self._cache[image_path]

    def score_pairs(self, pairs: list[PairSpec]) -> list[ScoredAttempt]:
        attempts: list[ScoredAttempt] = []
        for pair in pairs:
            similarity = cosine_similarity(
                self._embedding(pair.image_a), self._embedding(pair.image_b)
            )
            attempts.append(
                ScoredAttempt(group=pair.group, genuine=pair.genuine, score=similarity)
            )
        return attempts


class LivenessScorer:
    """Shapes (kind, image) presentations into harness liveness input."""

    def __init__(self, liveness_port: LivenessPort, floor: float):
        self._liveness = liveness_port
        self._floor = floor

    def presentations(self, items: list[tuple[str, str]]) -> list[tuple[str, bool]]:
        return [
            (kind, self._liveness.bona_fide_probability(ref) >= self._floor)
            for kind, ref in items
        ]

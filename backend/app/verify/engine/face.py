"""Face-match math: engine stage 3 and the stage 5 searches (TW-658).

Pure vector logic over embeddings produced at the model boundary.
The 1:1 match bands a similarity into match / review / no-match
(spec S5.2.3: a low-confidence automated pass is a failed design).
The 1:N searches (duplicate identities, stolen photos) share one
implementation: the same face standing behind two identities is
the realistic in-platform fraud, and it is one search, not two.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import EngineConfig

Embedding = tuple[float, ...]


def cosine_similarity(a: Embedding, b: Embedding) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def band_for_similarity(similarity: float, config: EngineConfig) -> str:
    if similarity >= config.match_threshold:
        return "match"
    if similarity >= config.review_floor:
        return "review"
    return "no_match"


@dataclass(frozen=True)
class TemplateRecord:
    """One live template in the duplicate-search corpus."""

    anchor_hash: str
    user_id: str
    is_banned: bool
    embedding: Embedding


@dataclass(frozen=True)
class DuplicateHit:
    anchor_hash: str
    user_id: str
    is_banned: bool
    similarity: float


def find_duplicates(
    embedding: Embedding,
    corpus: list[TemplateRecord],
    config: EngineConfig,
) -> list[DuplicateHit]:
    """Every corpus template at or above the duplicate threshold.

    Sorted by similarity, strongest first. The caller decides what a
    hit means (review, or refusal when the hit anchor is banned);
    this function only reports.
    """
    hits: list[DuplicateHit] = []
    for record in corpus:
        similarity = cosine_similarity(embedding, record.embedding)
        if similarity >= config.duplicate_threshold:
            hits.append(
                DuplicateHit(
                    anchor_hash=record.anchor_hash,
                    user_id=record.user_id,
                    is_banned=record.is_banned,
                    similarity=similarity,
                )
            )
    hits.sort(key=lambda h: h.similarity, reverse=True)
    return hits

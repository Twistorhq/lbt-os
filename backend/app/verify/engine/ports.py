"""The model boundary: ports the pipeline runs against (TW-658).

Every heavy FOSS library sits behind one of these protocols, the
same discipline the TW-649 harness stages use: the metric core and
the pipeline logic are pure and testable with the stack stubbed,
and a machine without the models gets StageUnavailable, never a
silently weaker check. Real adapters wrapping the harness stages
live in adapters.py; tests inject deterministic fakes.

InsightFace is absent by design (non-commercial model packs,
spec S5.3). No vendor API exists anywhere in this boundary: there
is no fallback port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .face import Embedding


class OcrPort(Protocol):
    def extract_text(self, image_path: str) -> list[str]: ...


class MrzPort(Protocol):
    def read(self, image_path: str) -> dict | None: ...


class Pdf417Port(Protocol):
    def decode_fields(self, image_path: str) -> dict[str, str]: ...


class FaceDetectPort(Protocol):
    def detect(self, image_path: str) -> list[tuple]: ...


class EmbedPort(Protocol):
    def embed(self, image_path: str) -> Embedding: ...


class LandmarkPort(Protocol):
    def landmarks(self, image_path: str) -> list[tuple[float, float, float]]: ...


class LivenessPort(Protocol):
    def bona_fide_probability(self, image_path: str) -> float: ...


@dataclass(frozen=True)
class PipelinePorts:
    """The full stage set one verification session runs through."""

    ocr: OcrPort
    mrz: MrzPort
    pdf417: Pdf417Port
    face_detect: FaceDetectPort
    embed: EmbedPort
    landmarks: LandmarkPort
    liveness: LivenessPort

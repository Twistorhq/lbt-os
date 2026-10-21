"""The verdict combiner: engine stage 5 decision (TW-658, spec S5.2).

Every stage's outcome lands here as plain values, and the rules are
ordered, explicit, and testable:

- Refusals (fail): a banned identity anchor, an under-16 birthdate,
  an expired document, a face that does not match, a capture with
  no face. These are facts, not scores.
- Review: everything ambiguous. Tamper signals, a failed MRZ check,
  a mid-band face score, a duplicate hit, a velocity trip, a failed
  active challenge, a weak passive liveness score, a high
  AI-generated-face score, an unreadable document. A low-confidence
  automated pass is a failed design, not a passed user.
- Pass: nothing above fired. Confidence is high only when the match
  is strong and liveness is clean; a pass near the threshold is a
  medium-confidence pass, recorded as such.

Two doctrines are load-bearing: passive liveness and the AI-face
detector are signals that can route to review, never sole gates;
and no combination of weak signals manufactures a fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import EngineConfig


@dataclass(frozen=True)
class VerdictInputs:
    document_extracted: bool
    mrz_valid: bool | None
    tamper_signals: list[str]
    document_expired: bool
    dob_band: str | None  # spec S5.8 band, None when DOB unreadable
    face_band: str  # match | review | no_match
    face_similarity: float | None
    selfie_face_count: int
    document_face_count: int
    liveness_active_ok: bool
    liveness_passive: float | None
    duplicate_hit: bool
    banned_anchor: bool
    velocity_exceeded: list[str]
    ai_face_score: float | None
    # TW-678: the secondary identity stage. Required only by
    # flows that set it (Arizona-purpose enrollment); the status
    # is the stage's verdict string, None when not required.
    secondary_required: bool = False
    secondary_status: str | None = None


@dataclass(frozen=True)
class Verdict:
    status: str  # pass | fail | review
    confidence_band: str  # low | medium | high
    reasons: list[str] = field(default_factory=list)


def decide(inputs: VerdictInputs, config: EngineConfig) -> Verdict:
    # --- Refusals: facts, not scores. ---
    if inputs.banned_anchor:
        return Verdict("fail", "high", ["banned_anchor"])
    if inputs.dob_band == "under_16":
        return Verdict("fail", "high", ["under_16"])
    if inputs.document_expired:
        return Verdict("fail", "high", ["document_expired"])
    if inputs.selfie_face_count == 0 or inputs.document_face_count == 0:
        return Verdict("fail", "high", ["no_face_captured"])
    if inputs.face_band == "no_match":
        return Verdict("fail", "high", ["face_no_match"])

    # --- Review: ambiguity belongs to a human. ---
    review: list[str] = []
    if not inputs.document_extracted:
        review.append("document_unreadable")
    if inputs.dob_band is None:
        # An age band is this product's core fact, and the schema
        # states the same doctrine (vfy_documents forbids a NULL
        # dob_hash for government classes). A pass without a band
        # must be impossible by construction, not by the persist
        # path happening to crash later.
        review.append("dob_unreadable")
    if inputs.mrz_valid is False:
        review.append("mrz_check_failed")
    if inputs.tamper_signals:
        review.append("tamper_signals")
    if inputs.face_band == "review":
        review.append("face_in_review_band")
    if inputs.duplicate_hit:
        review.append("duplicate_hit")
    if inputs.velocity_exceeded:
        review.append("velocity_exceeded")
    if not inputs.liveness_active_ok:
        review.append("liveness_challenge_failed")
    if (
        inputs.liveness_passive is not None
        and inputs.liveness_passive < config.liveness_review_floor
    ):
        review.append("liveness_passive_weak")
    if (
        inputs.ai_face_score is not None
        and inputs.ai_face_score >= config.ai_face_review_at
    ):
        review.append("ai_face_signal")
    if inputs.secondary_required and inputs.secondary_status != "passed":
        # Fail closed (TW-678): an enrollment that requires the
        # secondary identity stage cannot complete without its
        # pass. A records contradiction and a missing source both
        # land in review, not fail: the member may be a data
        # error away from verified, and a human owns that call.
        review.append(
            "secondary_identity_failed"
            if inputs.secondary_status == "failed"
            else "secondary_identity_unavailable"
        )
    if inputs.selfie_face_count > 1 or inputs.document_face_count > 1:
        review.append("multiple_faces")
    if review:
        confidence = (
            "low" if len(review) >= 2 or inputs.face_band == "review" else "medium"
        )
        return Verdict("review", confidence, review)

    # --- Pass. ---
    strong_match = (
        inputs.face_similarity is not None
        and inputs.face_similarity >= config.match_threshold + 0.15
    )
    clean_liveness = inputs.liveness_passive is None or inputs.liveness_passive >= 0.9
    confidence = "high" if (strong_match and clean_liveness) else "medium"
    return Verdict("pass", confidence, ["all_stages_clean"])

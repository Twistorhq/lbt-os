"""Twistor Verification Engine configuration (TW-658).

Everything here is a planning value until the criteria session with
Rosa and Justynn sets final numbers on measured data (TW-646
Section 5.3); the thresholds are configuration, versioned with the
engine in vfy_engine_versions.pipeline_config, never constants
buried in stage code.

The D3 production capture flag lives here and nowhere else:
production capture is OFF unless the environment carries an
explicit affirmative. Default off is the legal posture Angela's
TW-654 package set, and no other module may invent its own switch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ENGINE_VERSION = "tw-verify-engine-1.0.0"

# The adopted BIPA-standard consent flow (TW-654 Section 1.4 item 1,
# decision D1) and retention schedule (item 2, decision D2) as
# versioned legal instruments. vfy_consents records which version
# each member saw; a consent granted under an older version does not
# satisfy a capture under a newer one.
CONSENT_POLICY_VERSION = "biometric-policy-2026-10-20-v1"
RETENTION_SCHEDULE_VERSION = "retention-schedule-2026-10-20-v1"

_CAPTURE_ENV = "VFY_PRODUCTION_CAPTURE_ENABLED"
_AFFIRMATIVE = {"1", "true", "yes", "on"}


def production_capture_enabled(env: dict | None = None) -> bool:
    """True only on an explicit affirmative in the environment."""
    source = os.environ if env is None else env
    return str(source.get(_CAPTURE_ENV, "")).strip().lower() in _AFFIRMATIVE


@dataclass(frozen=True)
class EngineConfig:
    """Pipeline thresholds and limits (planning values, Section 5.3)."""

    # Face match banding over cosine similarity of dlib embeddings.
    match_threshold: float = 0.55
    review_floor: float = 0.45
    # 1:N duplicate search: a stricter bar than the 1:1 match, since
    # a hit accuses an existing verified identity of recycling.
    duplicate_threshold: float = 0.90
    # Passive liveness (MiniFASNet bona-fide probability). One signal,
    # never a sole gate: below the floor it routes to review only.
    liveness_review_floor: float = 0.5
    # AI-generated-face detector score at or above which the signal
    # alone routes to review (weak signal doctrine, Section 5.2.5).
    ai_face_review_at: float = 0.8
    # Active liveness challenges issued per session (Section 5.2.4).
    liveness_challenge_count: int = 3
    # Velocity limits per rolling 24h window (Section 5.2.5).
    velocity_max_per_document: int = 5
    velocity_max_per_device: int = 10
    velocity_max_per_network: int = 20
    velocity_window_hours: int = 24
    # Raw image staging hard ceiling (Section 5.7), enforced by
    # Staging.sweep_expired through pipeline.sweep_staging; the
    # pipeline destroys on decision, long before this ever binds.
    staging_ttl_hours: int = 72
    # D3: production capture flag. Default OFF, always.
    production_capture_enabled: bool = False
    # TW-678: whether THIS enrollment flow requires the secondary
    # identity verification stage (A.R.S. 4-101 condition (b)) to
    # pass before a verdict can pass. Arizona-purpose enrollments
    # set it; the generic flow does not. Default off, and the
    # stage's data source is a separate question (secondary.py):
    # requiring the stage while no source is configured fails the
    # enrollment closed into review, never through.
    secondary_identity_required: bool = False
    # Document classes whose capture is built. school_id is phase 2
    # (spec Section 5.12): its schema shape exists, its capture does
    # not, and the pipeline refuses it honestly until then.
    enabled_document_classes: frozenset = field(
        default_factory=lambda: frozenset({"passport", "drivers_license", "state_id"})
    )

    @classmethod
    def from_env(cls, env: dict | None = None) -> "EngineConfig":
        return cls(production_capture_enabled=production_capture_enabled(env))

    def pipeline_config(self) -> dict:
        """The threshold set recorded on the engine version row."""
        return {
            "match_threshold": self.match_threshold,
            "review_floor": self.review_floor,
            "duplicate_threshold": self.duplicate_threshold,
            "liveness_review_floor": self.liveness_review_floor,
            "ai_face_review_at": self.ai_face_review_at,
            "velocity_max_per_document": self.velocity_max_per_document,
            "velocity_max_per_device": self.velocity_max_per_device,
            "velocity_max_per_network": self.velocity_max_per_network,
            "secondary_identity_required": self.secondary_identity_required,
        }

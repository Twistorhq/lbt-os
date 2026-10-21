"""Liveness geometry: engine stage 4 active challenges (TW-658).

The selfie is a guided capture, not an upload (spec S5.2.4): the
engine issues a randomized challenge set, and the returned landmark
frames must answer it. This module is the pure geometry of that
check over MediaPipe Face Mesh landmark frames: head yaw from the
nose tip against the eye span, blink from the eye aspect ratio.
The passive MiniFASNet signal rides alongside as one score among
many; it is scored at the model boundary, never here, and it can
route to review but never fail anyone by itself.
"""

from __future__ import annotations

import math
import random

CHALLENGE_POOL = ("turn_left", "turn_right", "blink", "follow_target")

# MediaPipe Face Mesh indices used by the geometry below.
_NOSE_TIP = 1
_LEFT_EYE_OUTER = 33
_RIGHT_EYE_OUTER = 263
# Standard six-point eye-aspect-ratio sets (p1..p6).
_LEFT_EYE = (33, 160, 158, 133, 153, 144)
_RIGHT_EYE = (362, 387, 385, 263, 380, 373)

_YAW_THRESHOLD = 0.30
_EAR_OPEN = 0.25
_EAR_CLOSED = 0.18

Landmarks = list[tuple[float, float, float]]


def generate_challenges(rng: random.Random, count: int = 3) -> list[str]:
    """A randomized challenge set: sampled without replacement.

    Randomization is the defense (a replayed video cannot answer a
    challenge it did not predict); the rng is injected so runs are
    reproducible under test and unpredictable in production.
    """
    if count < 1 or count > len(CHALLENGE_POOL):
        raise ValueError(f"challenge count must be 1..{len(CHALLENGE_POOL)}")
    return rng.sample(list(CHALLENGE_POOL), count)


def _dist(a: tuple, b: tuple) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def eye_aspect_ratio(landmarks: Landmarks, side: str) -> float:
    """Eye aspect ratio: near 0.3 open, collapsing toward 0 closed."""
    idx = _LEFT_EYE if side == "left" else _RIGHT_EYE
    p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in idx)
    vertical = _dist(p2, p6) + _dist(p3, p5)
    horizontal = 2.0 * _dist(p1, p4)
    if horizontal == 0.0:
        return 0.0
    return vertical / horizontal


def head_yaw(landmarks: Landmarks) -> float:
    """Signed yaw proxy: nose offset from the eye-span center.

    Positive when the nose turns toward frame right, negative
    toward frame left, scaled by the eye span so it is comparable
    across face sizes and camera distances.
    """
    left = landmarks[_LEFT_EYE_OUTER]
    right = landmarks[_RIGHT_EYE_OUTER]
    nose = landmarks[_NOSE_TIP]
    center = (left[0] + right[0]) / 2.0
    half_span = abs(right[0] - left[0]) / 2.0
    if half_span == 0.0:
        return 0.0
    return (nose[0] - center) / half_span


def evaluate_challenge(challenge: str, frames: list[Landmarks]) -> bool:
    """Did the frame sequence answer this one challenge?"""
    if not frames:
        return False
    if challenge == "turn_left":
        return any(head_yaw(f) <= -_YAW_THRESHOLD for f in frames)
    if challenge == "turn_right":
        return any(head_yaw(f) >= _YAW_THRESHOLD for f in frames)
    if challenge == "follow_target":
        yaws = [head_yaw(f) for f in frames]
        return min(yaws) <= -_YAW_THRESHOLD and max(yaws) >= _YAW_THRESHOLD
    if challenge == "blink":
        ears = [
            min(eye_aspect_ratio(f, "left"), eye_aspect_ratio(f, "right"))
            for f in frames
        ]
        opened = any(e >= _EAR_OPEN for e in ears)
        closed = any(e <= _EAR_CLOSED for e in ears)
        return opened and closed
    raise ValueError(f"unknown challenge: {challenge!r}")


def evaluate_challenges(
    challenges: list[str], frames: list[Landmarks]
) -> dict[str, bool]:
    """Per-challenge results, so the signal trail names what failed."""
    return {name: evaluate_challenge(name, frames) for name in challenges}

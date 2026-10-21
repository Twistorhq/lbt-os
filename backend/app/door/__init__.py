"""Twistor Door Pass: biometric door transaction prototype (TW-671).

Re-scope of TW-647 under Justynn's 2026-10-21 decision: the member
takes their own picture at the door and the Twistor Verification
Engine (TW-658) verifies it against the ID anchored enrollment
from onboarding. Flows are server derived from TW-657 state
profiles: reliance_biometric in FULL states, lane_qr_photo as a
non-reliance lane in PARTIAL states, nothing in KILL states, fail
closed when a profile is disabled.

Prototype scope, stated plainly: this package runs on consented
test and benchmark fixtures only. The D3 production capture flag
(VFY_PRODUCTION_CAPTURE_ENABLED) stays OFF, and the service
refuses non-fixture enrollments by construction.
"""

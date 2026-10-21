"""Twistor Verification Engine (TW-658): spec TW-646 Section 5.

The proprietary, FOSS-only verification engine behind Twistor
Social's T2 tier: document OCR with MRZ/PDF417 cross-checks,
ID-photo-to-selfie face match, liveness, duplicate and stolen-photo
search, and a verdict that persists as hashes and signals while the
images are destroyed on decision. Benchmark-facing in this build:
production capture stays behind the D3 flag (config), default off.

Layout: config (thresholds + the capture flag), ports (the model
boundary), adapters (real ports over the TW-649 harness stages),
crosschecks / face / liveness / velocity / verdict (pure stage
logic), consent (TW-654 D1 writes), store (vfy_ persistence),
staging (image lifecycle), pipeline (the session orchestrator),
benchmarking (engine scorers feeding the TW-649 gate).
"""

from app.verify.engine.config import ENGINE_VERSION, EngineConfig
from app.verify.engine.pipeline import VerificationPipeline

__all__ = ["ENGINE_VERSION", "EngineConfig", "VerificationPipeline"]

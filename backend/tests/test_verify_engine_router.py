"""TW-658: verdict-only API boundary (spec Section 5.6), TDD RED.

The API returns verdicts and booleans, never images, fields, or
scores. While the D3 production capture flag is off, session
creation is refused with an honest 403: capture cannot occur.
"""

from __future__ import annotations

import base64
import hashlib
import random
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.database import get_db
from app.limiter import limiter
from app.verify import router as verify_router
from app.verify.engine.config import EngineConfig
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.hashing import FieldHasher
from app.verify.engine.pipeline import VerificationPipeline
from app.verify.engine.store import InMemoryVerifyStore
from tests.test_verify_engine_pipeline import _ports

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
USER = "u-ama"


def _pipeline(capture_enabled: bool) -> VerificationPipeline:
    from app.verify.engine.staging import InMemoryStaging

    return VerificationPipeline(
        store=InMemoryVerifyStore(),
        staging=InMemoryStaging(),
        hasher=FieldHasher(b"tw658-router-test-key"),
        encryptor=AesGcmEncryptor(bytes(range(32))),
        ports=_ports(),
        config=EngineConfig(production_capture_enabled=capture_enabled),
        rng=random.Random(7),
        clock=lambda: NOW,
    )


def _build_app(*, authenticated: bool, capture_enabled: bool) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(verify_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    pipeline = _pipeline(capture_enabled)
    app.dependency_overrides[verify_router.get_pipeline] = lambda: pipeline
    if authenticated:
        app.dependency_overrides[verify_router._verified_claims] = lambda: {"sub": USER}
    app.state.test_pipeline = pipeline
    return app


@pytest.fixture()
def app_off():
    return _build_app(authenticated=True, capture_enabled=False)


@pytest.fixture()
def app_on():
    return _build_app(authenticated=True, capture_enabled=True)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestRoutes:
    def test_routes_registered(self):
        found = {
            (m, r.path)
            for r in verify_router.router.routes
            if getattr(r, "methods", None)
            for m in r.methods
        }
        assert ("POST", "/verify/sessions") in found
        assert ("POST", "/verify/sessions/{session_id}/document") in found
        assert ("POST", "/verify/sessions/{session_id}/selfie") in found
        assert ("GET", "/verify/sessions/{session_id}") in found
        assert ("POST", "/verify/anchors/lookup") in found

    def test_unauthenticated_is_401(self):
        app = _build_app(authenticated=False, capture_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/verify/sessions", json={"document_type": "passport"}
        )
        assert resp.status_code == 401


class TestCaptureFlagOff:
    def test_session_creation_refused_while_flag_off(self, app_off):
        client = TestClient(app_off, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/verify/sessions", json={"document_type": "passport"}
        )
        assert resp.status_code == 403
        assert "capture" in resp.json()["detail"].lower()
        # Nothing was captured as a side effect.
        assert app_off.state.test_pipeline.store.sessions == {}


class TestVerdictFlow:
    def test_consent_then_full_flow_returns_verdict_only(self, app_on):
        from app.verify.engine.consent import grant_consent

        pipeline = app_on.state.test_pipeline
        grant_consent(pipeline.store, USER, "passport")
        client = TestClient(app_on, raise_server_exceptions=False)

        created = client.post(
            "/api/v1/verify/sessions", json={"document_type": "passport"}
        )
        assert created.status_code == 201
        session_id = created.json()["verification_id"]

        from tests.test_verify_engine_pipeline import AMA_DOC, AMA_SELFIE

        doc = client.post(
            f"/api/v1/verify/sessions/{session_id}/document",
            json={"image_b64": _b64(AMA_DOC)},
        )
        assert doc.status_code == 200

        challenges = pipeline.begin_liveness(session_id)
        from tests.test_verify_engine_pipeline import _frames_for

        selfie = client.post(
            f"/api/v1/verify/sessions/{session_id}/selfie",
            json={
                "image_b64": _b64(AMA_SELFIE),
                "frames": _frames_for(challenges),
            },
        )
        assert selfie.status_code == 200
        body = selfie.json()
        assert body["status"] == "passed"
        assert set(body) == {
            "verification_id",
            "status",
            "tier_granted",
            "confidence_band",
            "age_band",
            "decided_at",
        }

        fetched = client.get(f"/api/v1/verify/sessions/{session_id}")
        assert fetched.status_code == 200
        assert fetched.json() == body

    def test_anchor_lookup_is_booleans_only(self, app_on):
        client = TestClient(app_on, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/verify/anchors/lookup",
            json={"anchor_hash": hashlib.sha256(b"nobody").hexdigest()},
        )
        assert resp.status_code == 200
        assert resp.json() == {"exists": False, "banned": False}

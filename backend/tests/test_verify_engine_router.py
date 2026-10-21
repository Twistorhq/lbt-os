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
        assert ("POST", "/verify/sessions/{session_id}/liveness") in found
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

        # Challenges come from the HTTP surface (Rosa's M8): a real
        # client has no out-of-band pipeline to ask.
        live = client.post(f"/api/v1/verify/sessions/{session_id}/liveness")
        assert live.status_code == 200
        challenges = live.json()["challenges"]
        assert len(challenges) == 3

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


def _app_for(pipeline, sub: str) -> FastAPI:
    """An app over an existing pipeline, authenticated as `sub`."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(verify_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[verify_router.get_pipeline] = lambda: pipeline
    app.dependency_overrides[verify_router._verified_claims] = lambda: {"sub": sub}
    return app


class TestOwnership:
    def test_verdict_read_and_liveness_require_ownership(self, app_on):
        from app.verify.engine.consent import grant_consent
        from tests.test_verify_engine_pipeline import (
            AMA_DOC,
            AMA_SELFIE,
            _frames_for,
        )

        pipeline = app_on.state.test_pipeline
        grant_consent(pipeline.store, USER, "passport")
        client = TestClient(app_on, raise_server_exceptions=False)
        created = client.post(
            "/api/v1/verify/sessions", json={"document_type": "passport"}
        )
        session_id = created.json()["verification_id"]
        assert (
            client.post(
                f"/api/v1/verify/sessions/{session_id}/document",
                json={"image_b64": _b64(AMA_DOC)},
            ).status_code
            == 200
        )
        live = client.post(f"/api/v1/verify/sessions/{session_id}/liveness")
        selfie = client.post(
            f"/api/v1/verify/sessions/{session_id}/selfie",
            json={
                "image_b64": _b64(AMA_SELFIE),
                "frames": _frames_for(live.json()["challenges"]),
            },
        )
        assert selfie.json()["status"] == "passed"

        # A second member, same pipeline: the verdict (age band
        # included) is not theirs to read (Rosa's M7 probe), and
        # liveness on their session is refused the same way.
        other = TestClient(_app_for(pipeline, "u-kofi"), raise_server_exceptions=False)
        assert other.get(f"/api/v1/verify/sessions/{session_id}").status_code == 403
        assert (
            other.post(f"/api/v1/verify/sessions/{session_id}/liveness").status_code
            == 403
        )
        # The owner still reads it fine.
        assert client.get(f"/api/v1/verify/sessions/{session_id}").status_code == 200


class TestSessionCreateRefs:
    def test_device_and_network_refs_reach_velocity(self, app_on):
        from app.verify.engine.consent import grant_consent
        from tests.test_verify_engine_pipeline import (
            AMA_DOC,
            AMA_SELFIE,
            _frames_for,
        )

        pipeline = app_on.state.test_pipeline
        grant_consent(pipeline.store, USER, "passport")
        client = TestClient(app_on, raise_server_exceptions=False)
        created = client.post(
            "/api/v1/verify/sessions",
            json={
                "document_type": "passport",
                "device_ref": "dev-hash-1",
                "network_ref": "net-hash-1",
            },
        )
        session_id = created.json()["verification_id"]
        client.post(
            f"/api/v1/verify/sessions/{session_id}/document",
            json={"image_b64": _b64(AMA_DOC)},
        )
        live = client.post(f"/api/v1/verify/sessions/{session_id}/liveness")
        client.post(
            f"/api/v1/verify/sessions/{session_id}/selfie",
            json={
                "image_b64": _b64(AMA_SELFIE),
                "frames": _frames_for(live.json()["challenges"]),
            },
        )
        # m2: two of the three velocity dimensions were silently
        # dead over HTTP. The refs are pre-hashed by the caller;
        # the service never sees a raw identifier.
        kinds = {a.kind for a in pipeline.store.attempts}
        assert {"document", "device", "network"} <= kinds


class TestPipelineFactory:
    def test_flag_off_without_keys_answers_403_not_500(self, monkeypatch):
        from fastapi import HTTPException

        for var in (
            "VFY_TEMPLATE_KEY_B64",
            "VFY_HMAC_KEY",
            "VFY_PRODUCTION_CAPTURE_ENABLED",
        ):
            monkeypatch.delenv(var, raising=False)
        # m3: the flag refusal must run ahead of key material, so a
        # flag-off deployment answers the honest 403 even before
        # its keys are provisioned.
        with pytest.raises(HTTPException) as excinfo:
            verify_router.get_pipeline(db=MagicMock())
        assert excinfo.value.status_code == 403

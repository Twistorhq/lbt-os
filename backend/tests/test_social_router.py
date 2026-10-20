"""TW-650 Twistor Social — router skeleton (TDD RED).

The skeleton contract: routes exist under /social, every endpoint
requires verified Identity claims (401 without a token), and feature
endpoints answer an honest 501 until their build phase lands — a
skeleton must never pretend to work.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.database import get_db
from app.limiter import limiter
from app.social import router as social_router


def _build_app(*, authenticated: bool) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(social_router.router, prefix="/api/v1")
    # The service layer is stubbed in this phase, so the DB handle is
    # never used; a mock keeps the tests off the real Supabase client
    # (same pattern as the Telemetry router tests).
    app.dependency_overrides[get_db] = lambda: MagicMock()
    if authenticated:
        app.dependency_overrides[social_router._verified_claims] = lambda: {
            "sub": str(uuid4())
        }
    return app


@pytest.fixture()
def client():
    return TestClient(_build_app(authenticated=True))


@pytest.fixture()
def anon_client():
    return TestClient(_build_app(authenticated=False))


EXPECTED_ROUTES = {
    ("GET", "/social/profiles/me"),
    ("PUT", "/social/profiles/me"),
    ("POST", "/social/groups"),
    ("GET", "/social/groups/{group_id}"),
    ("POST", "/social/events"),
    ("GET", "/social/events/{event_id}"),
    ("POST", "/social/events/{event_id}/rsvps"),
    ("POST", "/social/rsvps/{rsvp_id}/decision"),
    ("POST", "/social/events/{event_id}/reveal"),
    ("POST", "/social/events/{event_id}/checkins"),
    ("POST", "/social/reports"),
}


class TestRoutesRegistered:
    def test_all_skeleton_routes_exist(self):
        found = {
            (m, r.path)
            for r in social_router.router.routes
            if getattr(r, "methods", None)
            for m in r.methods
        }
        missing = EXPECTED_ROUTES - found
        assert missing == set(), f"missing routes: {missing}"


class TestSkeletonHonesty:
    def test_profile_get_is_501_until_phase_4(self, client):
        resp = client.get("/api/v1/social/profiles/me")
        assert resp.status_code == 501

    def test_group_create_is_501(self, client):
        resp = client.post(
            "/api/v1/social/groups",
            json={
                "name": "Denver Hikers",
                "slug": "denver-hikers",
                "category": "outdoors",
                "metro": "denver",
                "min_age_band": "age_18_plus",
            },
        )
        assert resp.status_code == 501

    def test_report_create_is_501_not_silently_accepted(self, client):
        resp = client.post(
            "/api/v1/social/reports",
            json={
                "target_type": "user",
                "target_id": str(uuid4()),
                "category": "fraud",
                "description": "something happened",
            },
        )
        assert resp.status_code == 501

    def test_reveal_is_501_and_fails_closed(self, client):
        resp = client.post(f"/api/v1/social/events/{uuid4()}/reveal")
        assert resp.status_code == 501
        assert "address" not in resp.text.lower()


class TestAuthRequired:
    def test_no_token_is_401(self, anon_client):
        resp = anon_client.get("/api/v1/social/profiles/me")
        assert resp.status_code == 401

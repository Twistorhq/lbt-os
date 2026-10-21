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
    ("GET", "/social/groups"),
    ("GET", "/social/groups/{group_id}"),
    ("POST", "/social/groups/{group_id}/join"),
    ("POST", "/social/groups/{group_id}/memberships/{user_id}/decision"),
    ("POST", "/social/groups/{group_id}/memberships/{user_id}/role"),
    ("POST", "/social/events"),
    ("GET", "/social/events/{event_id}"),
    ("POST", "/social/events/{event_id}/location"),
    ("POST", "/social/events/{event_id}/rsvps"),
    ("POST", "/social/rsvps/{rsvp_id}/decision"),
    ("POST", "/social/rsvps/{rsvp_id}/withdraw"),
    ("POST", "/social/series"),
    ("GET", "/social/series/{series_id}"),
    ("POST", "/social/series/{series_id}/location"),
    ("GET", "/social/listings"),
    ("GET", "/social/listings/{listing_id}"),
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

    def test_group_create_maps_service_refusal_to_403(self, monkeypatch):
        # TW-660: group creation is implemented; the router's job is
        # now mapping the service layer's refusals, so record at the
        # service seam (behavior lives in test_social_core_loop.py).
        def _refuse(db, user_id, payload):
            raise social_router.service.ForbiddenError("verification required")

        monkeypatch.setattr(social_router.service, "create_group", _refuse)
        app = _build_app(authenticated=True)
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/social/groups",
            json={
                "name": "Denver Hikers",
                "slug": "denver-hikers",
                "category": "outdoors",
                "metro": "denver",
                "min_age_band": "age_18_plus",
            },
        )
        assert resp.status_code == 403

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

    def test_reveal_fails_closed_when_service_refuses(self, monkeypatch):
        # TW-660: the reveal is implemented and fail-closed in the
        # service layer; an invisible event answers the standard
        # restricted shell and never carries location text.
        def _refuse(db, user_id, event_id):
            raise social_router.service.NotFoundError()

        monkeypatch.setattr(social_router.service, "reveal_event_location", _refuse)
        app = _build_app(authenticated=True)
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/social/events/{uuid4()}/reveal"
        )
        assert resp.status_code == 404
        assert "address" not in resp.text.lower()


class TestAuthRequired:
    def test_no_token_is_401(self, anon_client):
        resp = anon_client.get("/api/v1/social/profiles/me")
        assert resp.status_code == 401


class TestReadsCarryTheViewer:
    """Rosa m1: band, audience, and block checks are viewer-relative,
    so get_group / get_event must hand the service layer the caller's
    subject from the verified token, never discard it."""

    VIEWER = uuid4()

    def _client_with_recorders(self, monkeypatch):
        calls = {}

        def recorder(name):
            def _rec(db, viewer_user_id, target_id):
                calls[name] = (viewer_user_id, target_id)
                raise NotImplementedError("Phase 4 (tasks.md items 15-18)")

            return _rec

        monkeypatch.setattr(social_router.service, "get_group", recorder("get_group"))
        monkeypatch.setattr(social_router.service, "get_event", recorder("get_event"))
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.include_router(social_router.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[social_router._verified_claims] = lambda: {
            "sub": str(self.VIEWER)
        }
        return TestClient(app, raise_server_exceptions=False), calls

    def test_get_group_passes_viewer_subject(self, monkeypatch):
        client, calls = self._client_with_recorders(monkeypatch)
        group_id = uuid4()
        resp = client.get(f"/api/v1/social/groups/{group_id}")
        assert resp.status_code == 501  # still an honest skeleton stub
        assert calls["get_group"] == (self.VIEWER, group_id)

    def test_get_event_passes_viewer_subject(self, monkeypatch):
        client, calls = self._client_with_recorders(monkeypatch)
        event_id = uuid4()
        resp = client.get(f"/api/v1/social/events/{event_id}")
        assert resp.status_code == 501
        assert calls["get_event"] == (self.VIEWER, event_id)

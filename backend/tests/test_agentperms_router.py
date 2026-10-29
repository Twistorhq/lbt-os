"""TW-768 / TW-779 — agentperms router: the owner/admin role gate.

Rex's Grim change review (run-2, TH-010) found the mutation API
authorized by org membership alone: the router verified the
Identity JWT and scoped by its org claim but never read the role
claim, so ANY org token could grant payment.charge at an arbitrary
cap or revoke-all. VER-008 is the two-token test: real Identity
tokens are minted here (owner, admin, member, role-less) and the
member token must be refused on every mutation while the owner
token succeeds. Reads stay member-visible per Spec 6.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_agentperms_service import FakeDb

from app.agentperms import router as agentperms_router
from app.agentperms import service
from app.database import get_db
from app.identity import keys, tokens
from app.identity import service as idn_service

ORG = "org-1"
OWNER_USER = "user-owner"
STAFF_USER = "user-staff"


@pytest.fixture()
def keypair():
    return keys.generate_keypair(kid="tw-test")


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def client(db, keypair, monkeypatch):
    monkeypatch.setattr(idn_service, "get_verification_keypairs", lambda: [keypair])
    app = FastAPI()
    app.include_router(agentperms_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _headers(keypair, user_id, role):
    token = tokens.mint_access_token(keypair, user_id=user_id, org_id=ORG, role=role)
    return {"Authorization": f"Bearer {token}"}


class TestMutationRoleGate:
    def test_staff_token_cannot_grant(self, client, db, keypair):
        r = client.post(
            "/agent-permissions/grants",
            json={"action_class": "payment.charge", "cap_cents": 999_999_00},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        # Nothing was written: the money class is not armed.
        d = service.check_permission(db, ORG, "payment.charge", amount_cents=1)
        assert not d.allowed and d.reason == "no_grant"

    def test_staff_token_cannot_deny(self, client, db, keypair):
        service.grant_permission(db, ORG, "email.send", actor=OWNER_USER)
        r = client.post(
            "/agent-permissions/denies",
            json={"action_class": "email.send"},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        assert service.check_permission(db, ORG, "email.send").allowed

    def test_staff_token_cannot_revoke(self, client, db, keypair):
        service.grant_permission(db, ORG, "email.send", actor=OWNER_USER)
        r = client.post(
            "/agent-permissions/revoke",
            json={"strength": "all"},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        assert service.check_permission(db, ORG, "email.send").allowed
        assert service.list_revocations(db, ORG) == []

    def test_roleless_token_cannot_mutate(self, client, db, keypair):
        token = tokens.mint_access_token(keypair, user_id="user-x", org_id=ORG)
        r = client.post(
            "/agent-permissions/grants",
            json={"action_class": "email.send"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_owner_token_can_grant_deny_and_revoke(self, client, db, keypair):
        headers = _headers(keypair, OWNER_USER, "owner")
        r = client.post(
            "/agent-permissions/grants",
            json={"action_class": "payment.charge", "cap_cents": 5000},
            headers=headers,
        )
        assert r.status_code == 200
        assert service.check_permission(
            db, ORG, "payment.charge", amount_cents=5000
        ).allowed

        r = client.post(
            "/agent-permissions/denies",
            json={"action_class": "sms.send"},
            headers=headers,
        )
        assert r.status_code == 200
        assert not service.check_permission(db, ORG, "sms.send").allowed

        r = client.post(
            "/agent-permissions/revoke",
            json={"strength": "all"},
            headers=headers,
        )
        assert r.status_code == 200
        d = service.check_permission(db, ORG, "payment.charge", amount_cents=1)
        assert not d.allowed and d.reason == "revoked"

    def test_admin_token_can_mutate(self, client, db, keypair):
        r = client.post(
            "/agent-permissions/grants",
            json={"action_class": "email.send"},
            headers=_headers(keypair, "user-admin", "admin"),
        )
        assert r.status_code == 200
        assert service.check_permission(db, ORG, "email.send").allowed


class TestReadsStayMemberVisible:
    def test_staff_token_can_read_the_ledger(self, client, db, keypair):
        service.grant_permission(db, ORG, "email.send", actor=OWNER_USER)
        headers = _headers(keypair, STAFF_USER, "member")
        r = client.get("/agent-permissions/grants", headers=headers)
        assert r.status_code == 200
        assert r.json()["grants"][0]["action_class"] == "email.send"
        r = client.get("/agent-permissions/history", headers=headers)
        assert r.status_code == 200

    def test_missing_token_is_401(self, client):
        r = client.get("/agent-permissions/grants")
        assert r.status_code == 401

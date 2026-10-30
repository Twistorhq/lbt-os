"""TW-800 - toolgateway router: the approval-decision role gate.

Same shape as TW-779's agentperms test: real Identity tokens are
minted here (owner, member) and the member token must be refused on
deciding a parked call while the owner token settles it. Invokes
stay member-visible - the agent acts under the caller's identity
and the policy + ledger do the gating - but "yes, run this one" is
an owner act.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_toolgateway_service import FakeDb, Recorder

from app.database import get_db
from app.identity import keys, tokens
from app.identity import service as idn_service
from app.toolgateway import router as gateway_router
from app.toolgateway import service

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
    app.include_router(gateway_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _headers(keypair, user_id, role):
    token = tokens.mint_access_token(keypair, user_id=user_id, org_id=ORG, role=role)
    return {"Authorization": f"Bearer {token}"}


def _park(db):
    out = service.invoke(
        db,
        ORG,
        "flows.trigger",
        {"workflow_id": "w1"},
        actor="agent-1",
        executor=Recorder(),
    )
    assert out.status == "pending_approval"
    return out.approval_id


class TestDecideRoleGate:
    def test_member_token_cannot_decide(self, client, db, keypair):
        approval_id = _park(db)
        r = client.post(
            f"/agent-tools/approvals/{approval_id}/decide",
            json={"approve": True},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        # Nothing settled: the call is still parked.
        assert [p["id"] for p in service.list_approvals(db, ORG)] == [approval_id]

    def test_owner_token_decides(self, client, db, keypair):
        approval_id = _park(db)
        r = client.post(
            f"/agent-tools/approvals/{approval_id}/decide",
            json={"approve": False},
            headers=_headers(keypair, OWNER_USER, "owner"),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "denied"
        assert body["reason"] == "approval_denied"
        assert service.list_approvals(db, ORG) == []

    def test_member_token_can_invoke_and_read(self, client, db, keypair):
        r = client.post(
            "/agent-tools/call",
            json={"tool": "nope.tool", "args": {}},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "denied"
        r = client.get(
            "/agent-tools/calls", headers=_headers(keypair, STAFF_USER, "member")
        )
        assert r.status_code == 200
        assert len(r.json()["calls"]) == 2

    def test_missing_token_is_401(self, client):
        r = client.post("/agent-tools/call", json={"tool": "flows.list"})
        assert r.status_code == 401

"""TW-787 fix round / TW-789 / TW-790 — identity router: the
owner/admin role gate on the roster mutations.

Rosa's review and Rex's Grim pass (TH-010 class, the TW-779 defect
moved to billing) found TW-787's three new mutation endpoints —
POST /agents (hire), POST /agents/{id}/role, POST /agents/{id}/
archetype — authorized by org membership alone: a MEMBER token
hired an agent through the router and a real 2500-cent agent.hire
toll posted. VER-008 style, as in test_agentperms_router: real
Identity tokens are minted here (owner, admin, member, role-less)
and the member token must be refused on every mutation with
nothing written — no identity row, no toll row, no role or
archetype change — while owner and admin succeed end to end.
Reads stay member-visible per Spec 6.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_identity import router as agt_router
from app.agent_identity import service
from app.config import AGENT_HIRE_FEE_CENTS
from app.database import get_db
from app.identity import keys, tokens
from app.identity import service as idn_service
from tests.test_agent_identity_service import ORG_A, FakeDB

OWNER_USER = "user-owner"
STAFF_USER = "user-staff"


@pytest.fixture()
def keypair():
    return keys.generate_keypair(kid="tw-test")


@pytest.fixture()
def db():
    fake = FakeDB()
    fake.tables["toll_events"] = []
    return fake


@pytest.fixture()
def client(db, keypair, monkeypatch):
    monkeypatch.setattr(idn_service, "get_verification_keypairs", lambda: [keypair])
    app = FastAPI()
    app.include_router(agt_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _headers(keypair, user_id, role):
    token = tokens.mint_access_token(keypair, user_id=user_id, org_id=ORG_A, role=role)
    return {"Authorization": f"Bearer {token}"}


def _included(db):
    return service.provision_identity(db, ORG_A, "Sam", actor=OWNER_USER)


def _hired(db):
    return service.hire_agent(db, ORG_A, "Max", actor=OWNER_USER)


def _hire_tolls(db):
    return [r for r in db.tables["toll_events"] if r["event_type"] == "agent.hire"]


class TestMutationRoleGate:
    def test_member_token_cannot_hire(self, client, db, keypair):
        _included(db)
        r = client.post(
            "/agent-identity/agents",
            json={"agent_name": "Max"},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        # Nothing was written: no new identity row, no toll row.
        assert len(db.tables["agt_identities"]) == 1
        assert _hire_tolls(db) == []

    def test_roleless_token_cannot_hire(self, client, db, keypair):
        _included(db)
        token = tokens.mint_access_token(keypair, user_id="user-x", org_id=ORG_A)
        r = client.post(
            "/agent-identity/agents",
            json={"agent_name": "Max"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        assert len(db.tables["agt_identities"]) == 1
        assert _hire_tolls(db) == []

    def test_member_token_cannot_assign_role(self, client, db, keypair):
        _included(db)
        hired = _hired(db)
        r = client.post(
            f"/agent-identity/agents/{hired.id}/role",
            json={"role": "sales"},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        assert service.get_agent(db, ORG_A, hired.id).role == "general"

    def test_member_token_cannot_assign_archetype(self, client, db, keypair):
        _included(db)
        hired = _hired(db)
        r = client.post(
            f"/agent-identity/agents/{hired.id}/archetype",
            json={"archetype_key": "sales-pro"},
            headers=_headers(keypair, STAFF_USER, "member"),
        )
        assert r.status_code == 403
        assert service.get_agent(db, ORG_A, hired.id).archetype_key is None

    def test_roleless_token_cannot_assign_role_or_archetype(self, client, db, keypair):
        _included(db)
        hired = _hired(db)
        token = tokens.mint_access_token(keypair, user_id="user-x", org_id=ORG_A)
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post(
            f"/agent-identity/agents/{hired.id}/role",
            json={"role": "sales"},
            headers=headers,
        )
        assert r.status_code == 403
        r = client.post(
            f"/agent-identity/agents/{hired.id}/archetype",
            json={"archetype_key": "sales-pro"},
            headers=headers,
        )
        assert r.status_code == 403
        agent = service.get_agent(db, ORG_A, hired.id)
        assert agent.role == "general"
        assert agent.archetype_key is None

    def test_owner_token_can_hire_and_assign_end_to_end(self, client, db, keypair):
        _included(db)
        headers = _headers(keypair, OWNER_USER, "owner")
        r = client.post(
            "/agent-identity/agents",
            json={"agent_name": "Max"},
            headers=headers,
        )
        assert r.status_code == 200
        hired_id = r.json()["id"]
        tolls = _hire_tolls(db)
        assert len(tolls) == 1
        assert tolls[0]["amount_cents"] == AGENT_HIRE_FEE_CENTS

        r = client.post(
            f"/agent-identity/agents/{hired_id}/role",
            json={"role": "sales"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["role"] == "sales"

        r = client.post(
            f"/agent-identity/agents/{hired_id}/archetype",
            json={"archetype_key": "sales-pro"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["archetype_key"] == "sales-pro"

    def test_admin_token_can_hire_and_assign(self, client, db, keypair):
        _included(db)
        headers = _headers(keypair, "user-admin", "admin")
        r = client.post(
            "/agent-identity/agents",
            json={"agent_name": "Max"},
            headers=headers,
        )
        assert r.status_code == 200
        hired_id = r.json()["id"]
        assert len(_hire_tolls(db)) == 1
        r = client.post(
            f"/agent-identity/agents/{hired_id}/role",
            json={"role": "hr"},
            headers=headers,
        )
        assert r.status_code == 200
        r = client.post(
            f"/agent-identity/agents/{hired_id}/archetype",
            json={"archetype_key": "people-ops"},
            headers=headers,
        )
        assert r.status_code == 200


class TestReadsStayMemberVisible:
    def test_member_token_can_read_the_roster(self, client, db, keypair):
        included = _included(db)
        hired = _hired(db)
        headers = _headers(keypair, STAFF_USER, "member")
        r = client.get("/agent-identity/agents", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 2
        r = client.get(f"/agent-identity/agents/{hired.id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == hired.id
        r = client.get("/agent-identity/me", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == included.id

    def test_missing_token_is_401(self, client, db):
        _included(db)
        r = client.post("/agent-identity/agents", json={"agent_name": "Max"})
        assert r.status_code == 401
        assert _hire_tolls(db) == []

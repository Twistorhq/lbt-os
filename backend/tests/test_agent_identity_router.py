"""TW-767 Twistor Agent Identity — router: registry + inbound webhook.

The webhook tests pin the fail-closed contract: unconfigured secret
refuses (503), bad signature is 401, unknown recipient queues nothing
and still answers 200 (no address enumeration), known recipient
queues exactly one task in exactly one org.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_identity import router as agt_router
from app.agent_identity import service
from app.database import get_db
from tests.test_agent_identity_service import ORG_A, ORG_B, FakeDB

KEY = b"router-test-signing-key"
SECRET = "whsec_" + base64.b64encode(KEY).decode()


def _sign(msg_id, timestamp, body):
    content = f"{msg_id}.{timestamp}.".encode() + body
    return (
        "v1,"
        + base64.b64encode(hmac.new(KEY, content, hashlib.sha256).digest()).decode()
    )


@pytest.fixture()
def fake_db():
    return FakeDB()


def _client(fake_db, org=ORG_A):
    app = FastAPI()
    app.include_router(agt_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[agt_router._require_org] = lambda: org
    app.dependency_overrides[agt_router._require_actor] = lambda: "owner-1"
    return TestClient(app)


def _bare_client(fake_db):
    app = FastAPI()
    app.include_router(agt_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: fake_db
    return TestClient(app)


class TestRegistrySurface:
    def test_me_returns_fail_closed_view_before_provisioning(self, fake_db):
        client = _client(fake_db)
        res = client.get("/api/v1/agent-identity/me")
        assert res.status_code == 200
        body = res.json()
        assert body["provisioned"] is False
        assert body["status"] == "unprovisioned"
        assert body["coverage"]["mode"] == "unconfigured"

    def test_provision_then_me_round_trip(self, fake_db):
        client = _client(fake_db)
        res = client.post("/api/v1/agent-identity", json={"agent_name": "Sam"})
        assert res.status_code == 200
        created = res.json()
        assert created["email_address"] == "summit-air-11111111@agents.twistor.co"
        me = client.get("/api/v1/agent-identity/me").json()
        assert me["provisioned"] is True
        assert me["agent_name"] == "Sam"

    def test_provision_is_scoped_to_the_callers_org(self, fake_db):
        client_b = _client(fake_db, org=ORG_B)
        client_b.post("/api/v1/agent-identity", json={"agent_name": "Jo"})
        client_a = _client(fake_db, org=ORG_A)
        me_a = client_a.get("/api/v1/agent-identity/me").json()
        assert me_a["provisioned"] is False  # org B's identity is invisible to org A

    def test_no_token_is_401(self, fake_db):
        client = _bare_client(fake_db)
        assert client.get("/api/v1/agent-identity/me").status_code == 401

    def test_token_without_org_is_403(self, fake_db):
        app = FastAPI()
        app.include_router(agt_router.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[agt_router._verified_claims] = lambda: {"sub": "u1"}
        client = TestClient(app)
        assert client.get("/api/v1/agent-identity/me").status_code == 403

    def test_persona_patch_and_channel_reads(self, fake_db):
        client = _client(fake_db)
        client.post("/api/v1/agent-identity", json={"agent_name": "Sam"})
        res = client.patch(
            "/api/v1/agent-identity/persona", json={"patch": {"tone": "plain and warm"}}
        )
        assert res.status_code == 200
        assert res.json()["persona"]["tone"] == "plain and warm"
        blocks = set()
        for channel in ("phone", "email", "sms"):
            cfg = client.get(f"/api/v1/agent-identity/channels/{channel}").json()
            blocks.add(json.dumps(cfg["identity"], sort_keys=True))
        assert len(blocks) == 1  # byte-identical across channels

    def test_invalid_persona_patch_is_400(self, fake_db):
        client = _client(fake_db)
        client.post("/api/v1/agent-identity", json={"agent_name": "Sam"})
        res = client.patch(
            "/api/v1/agent-identity/persona",
            json={"patch": {"rapport": {"cadence": "chatty"}}},
        )
        assert res.status_code == 400

    def test_mutation_before_provisioning_is_404(self, fake_db):
        client = _client(fake_db)
        res = client.patch(
            "/api/v1/agent-identity/persona", json={"patch": {"tone": "x"}}
        )
        assert res.status_code == 404


class TestInboundWebhook:
    def _event(self, to, msg_id="evt-1"):
        return json.dumps(
            {
                "type": "email.received",
                "data": {
                    "email_id": msg_id,
                    "from": "customer@example.com",
                    "to": [to],
                    "subject": "Need a quote",
                },
            }
        ).encode()

    def _post(self, client, body, msg_id="m-1", secret=SECRET):
        ts = str(int(time.time()))
        headers = {
            "svix-id": msg_id,
            "svix-timestamp": ts,
            "svix-signature": _sign(msg_id, ts, body),
            "content-type": "application/json",
        }
        return client.post(
            "/api/v1/agent-identity/inbound/email", content=body, headers=headers
        )

    def test_unconfigured_secret_refuses(self, fake_db, monkeypatch):
        monkeypatch.setattr(agt_router.settings, "agent_inbound_webhook_secret", None)
        client = _client(fake_db)
        res = self._post(client, self._event("x@agents.twistor.co"))
        assert res.status_code == 503
        assert fake_db.tables["agt_inbound_messages"] == []

    def test_bad_signature_is_401(self, fake_db, monkeypatch):
        monkeypatch.setattr(agt_router.settings, "agent_inbound_webhook_secret", SECRET)
        client = _client(fake_db)
        body = self._event("x@agents.twistor.co")
        res = client.post(
            "/api/v1/agent-identity/inbound/email",
            content=body,
            headers={
                "svix-id": "m-1",
                "svix-timestamp": str(int(time.time())),
                "svix-signature": "v1,AAAA",
                "content-type": "application/json",
            },
        )
        assert res.status_code == 401
        assert fake_db.tables["agt_inbound_messages"] == []

    def test_known_recipient_queues_one_task_in_one_org(self, fake_db, monkeypatch):
        monkeypatch.setattr(agt_router.settings, "agent_inbound_webhook_secret", SECRET)
        identity = service.provision_identity(fake_db, ORG_A, "Sam", actor="owner-1")
        client = _client(fake_db)
        res = self._post(client, self._event(identity.email_address))
        assert res.status_code == 200
        assert res.json()["queued"] is True
        rows = fake_db.tables["agt_inbound_messages"]
        assert len(rows) == 1
        assert rows[0]["org_id"] == ORG_A
        assert rows[0]["status"] == "queued"

    def test_unknown_recipient_queues_nothing_but_answers_200(
        self, fake_db, monkeypatch
    ):
        monkeypatch.setattr(agt_router.settings, "agent_inbound_webhook_secret", SECRET)
        service.provision_identity(fake_db, ORG_A, "Sam", actor="owner-1")
        client = _client(fake_db)
        res = self._post(client, self._event("ghost@agents.twistor.co"))
        assert res.status_code == 200
        assert res.json()["queued"] is False
        assert fake_db.tables["agt_inbound_messages"] == []

    def test_non_received_event_is_ignored(self, fake_db, monkeypatch):
        monkeypatch.setattr(agt_router.settings, "agent_inbound_webhook_secret", SECRET)
        client = _client(fake_db)
        body = json.dumps({"type": "email.sent", "data": {}}).encode()
        res = self._post(client, body)
        assert res.status_code == 200
        assert res.json()["ignored"] is True

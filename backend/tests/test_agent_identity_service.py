"""TW-767 Twistor Agent Identity — service: registry behavior.

Acceptance proof from the TW-764 Spec 1: two orgs hold distinct agent
addresses and numbers; mail to an agent's address creates queued work
in that org ONLY; a cross-org probe resolves to the addressed org or
fails closed, never both; the persona is byte-identical across the
phone, email, and text configuration reads.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.agent_identity import service
from app.agent_identity.models import IdentityError

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
NUM_A = "+13035550101"
NUM_B = "+13035550202"


class _Result:
    def __init__(self, data):
        self.data = data


class _Chain:
    """A supabase query chain over an in-memory row list. eq filters for
    real — cross-org isolation is only proven if the filter is honest."""

    def __init__(self, rows, op, payload=None):
        self._rows = rows
        self._op = op
        self._payload = payload
        self._filters = []
        self._limit = None

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matched(self):
        out = list(self._rows)
        for col, val in self._filters:
            out = [r for r in out if r.get(col) == val]
        if self._limit is not None:
            out = out[: self._limit]
        return out

    def execute(self):
        if self._op == "select":
            return _Result([dict(r) for r in self._matched()])
        if self._op == "insert":
            row = dict(self._payload)
            self._rows.append(row)
            return _Result([dict(row)])
        if self._op == "update":
            updated = []
            for r in self._matched():
                r.update(self._payload)
                updated.append(dict(r))
            return _Result(updated)
        raise AssertionError(f"unknown op {self._op}")


class _Table:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return _Chain(self._rows, "select")

    def insert(self, payload):
        return _Chain(self._rows, "insert", payload)

    def update(self, payload):
        return _Chain(self._rows, "update", payload)


class FakeDB:
    def __init__(self):
        self.tables = {
            "organizations": [
                {"id": ORG_A, "name": "Summit Air"},
                {"id": ORG_B, "name": "Rocky Plumbing"},
            ],
            "org_phone_numbers": [
                {"org_id": ORG_A, "phone_number": NUM_A},
                {"org_id": ORG_B, "phone_number": NUM_B},
            ],
            "agt_identities": [],
            "agt_inbound_messages": [],
            "agt_audit": [],
        }
        self._seq = 0

    def table(self, name):
        return _Table(self.tables[name])


@pytest.fixture()
def db():
    return FakeDB()


def _provision(db, org=ORG_A, name="Sam"):
    return service.provision_identity(db, org, name, actor="owner-1")


class TestProvisioning:
    def test_provision_allocates_identity_with_fail_closed_defaults(self, db):
        identity = _provision(db)
        assert identity.provisioned is True
        assert identity.agent_name == "Sam"
        assert identity.status == "unprovisioned"
        assert identity.coverage.mode == "unconfigured"
        assert identity.phone_number is None
        assert identity.email_address == "summit-air-11111111@agents.twistor.co"

    def test_two_orgs_hold_distinct_addresses(self, db):
        a = _provision(db, ORG_A, "Sam")
        b = _provision(db, ORG_B, "Jo")
        assert a.email_address != b.email_address
        assert b.email_address == "rocky-plumbing-22222222@agents.twistor.co"

    def test_provision_is_idempotent(self, db):
        first = _provision(db)
        second = _provision(db, name="Different Name")
        assert second.email_address == first.email_address
        assert second.agent_name == "Sam"  # never silently re-minted
        assert len(db.tables["agt_identities"]) == 1

    def test_provision_unknown_org_fails_loud(self, db):
        with pytest.raises(IdentityError):
            service.provision_identity(db, "no-such-org", "Sam", actor="owner-1")

    def test_provision_empty_name_fails_loud(self, db):
        with pytest.raises(IdentityError):
            service.provision_identity(db, ORG_A, "  ", actor="owner-1")

    def test_provision_writes_audit(self, db):
        _provision(db)
        actions = [r["action"] for r in db.tables["agt_audit"]]
        assert actions == ["provision"]


class TestReadsAndIsolation:
    def test_get_identity_is_org_scoped(self, db):
        _provision(db, ORG_A)
        assert service.get_identity(db, ORG_B) is None
        assert service.get_identity(db, ORG_A).agent_name == "Sam"

    def test_unprovisioned_org_gets_fail_closed_view(self, db):
        view = service.identity_view(db, ORG_B)
        assert view["provisioned"] is False
        assert view["status"] == "unprovisioned"
        assert view["email_address"] is None
        assert view["coverage"]["mode"] == "unconfigured"
        assert view["persona"]["rapport"]["cadence"] == "low"


class TestPersonaConsistency:
    def test_persona_byte_identical_across_channels(self, db):
        _provision(db)
        service.update_persona(
            db,
            ORG_A,
            {
                "tone": "plain and warm",
                "rapport": {"cadence": "standard", "max_checkins_per_week": 3},
            },
            actor="owner-1",
        )
        blocks = []
        for channel in ("phone", "email", "sms"):
            cfg = service.channel_config(db, ORG_A, channel)
            assert cfg["channel"] == channel
            blocks.append(json.dumps(cfg["identity"], sort_keys=True))
        assert blocks[0] == blocks[1] == blocks[2]
        assert json.loads(blocks[0])["persona"]["tone"] == "plain and warm"

    def test_unknown_channel_fails_loud(self, db):
        _provision(db)
        with pytest.raises(IdentityError):
            service.channel_config(db, ORG_A, "fax")

    def test_persona_patch_merges_nested_and_bumps_version(self, db):
        identity = _provision(db)
        updated = service.update_persona(
            db,
            ORG_A,
            {"rapport": {"cadence": "off", "max_checkins_per_week": 0}},
            actor="owner-1",
        )
        assert updated.persona.rapport.cadence == "off"
        assert updated.persona.tone == identity.persona.tone  # untouched fields survive
        assert updated.version == identity.version + 1

    def test_invalid_persona_patch_writes_nothing(self, db):
        _provision(db)
        before = service.get_identity(db, ORG_A).model_dump()
        with pytest.raises(IdentityError):
            service.update_persona(
                db, ORG_A, {"rapport": {"cadence": "chatty"}}, actor="owner-1"
            )
        after = service.get_identity(db, ORG_A).model_dump()
        assert before == after

    def test_coverage_update_validates(self, db):
        _provision(db)
        updated = service.update_coverage(
            db,
            ORG_A,
            {
                "mode": "windows",
                "timezone": "America/Denver",
                "windows": [
                    {"days": [0, 1, 2, 3, 4], "start": "07:00", "end": "19:00"}
                ],
            },
            actor="owner-1",
        )
        assert updated.coverage.mode == "windows"
        with pytest.raises(IdentityError):
            service.update_coverage(
                db, ORG_A, {"mode": "windows", "windows": []}, actor="owner-1"
            )

    def test_mutation_on_unprovisioned_org_is_not_found(self, db):
        with pytest.raises(service.IdentityNotFound):
            service.update_persona(db, ORG_A, {"tone": "x"}, actor="owner-1")


class TestPhoneBinding:
    def test_bind_own_number(self, db):
        _provision(db, ORG_A)
        identity = service.bind_phone_number(db, ORG_A, NUM_A, actor="owner-1")
        assert identity.phone_number == NUM_A

    def test_two_orgs_hold_distinct_numbers(self, db):
        _provision(db, ORG_A)
        _provision(db, ORG_B)
        service.bind_phone_number(db, ORG_A, NUM_A, actor="owner-1")
        service.bind_phone_number(db, ORG_B, NUM_B, actor="owner-1")
        assert service.resolve_phone(db, NUM_A).org_id == ORG_A
        assert service.resolve_phone(db, NUM_B).org_id == ORG_B

    def test_bind_another_orgs_number_refused(self, db):
        _provision(db, ORG_A)
        _provision(db, ORG_B)
        with pytest.raises(IdentityError):
            service.bind_phone_number(db, ORG_A, NUM_B, actor="owner-1")
        assert service.get_identity(db, ORG_A).phone_number is None

    def test_bind_unowned_number_refused(self, db):
        _provision(db, ORG_A)
        with pytest.raises(IdentityError):
            service.bind_phone_number(db, ORG_A, "+19995550199", actor="owner-1")

    def test_bind_is_idempotent(self, db):
        _provision(db, ORG_A)
        service.bind_phone_number(db, ORG_A, NUM_A, actor="owner-1")
        again = service.bind_phone_number(db, ORG_A, NUM_A, actor="owner-1")
        assert again.phone_number == NUM_A


class TestInboundRouting:
    def test_mail_to_agent_queues_in_that_org_only(self, db):
        a = _provision(db, ORG_A)
        _provision(db, ORG_B)
        row = service.enqueue_inbound_email(
            db,
            to_address=a.email_address,
            from_address="customer@example.com",
            subject="Rocky Plumbing invoice question",  # org B content, org A address
            body_text="...",
            provider_message_id="msg-1",
        )
        assert row is not None
        assert row["org_id"] == ORG_A  # the address is the authority, never the content
        assert row["status"] == "queued"
        queued_b = service.list_inbound(db, ORG_B)
        assert queued_b == []

    def test_unknown_recipient_queues_nothing(self, db):
        _provision(db, ORG_A)
        row = service.enqueue_inbound_email(
            db,
            to_address="nobody@agents.twistor.co",
            from_address="customer@example.com",
            subject="hi",
            body_text="...",
            provider_message_id="msg-2",
        )
        assert row is None
        assert db.tables["agt_inbound_messages"] == []

    def test_suspended_agent_stops_resolving(self, db):
        a = _provision(db, ORG_A)
        service.set_status(db, ORG_A, "suspended", actor="owner-1")
        assert service.resolve_email_recipient(db, a.email_address) is None
        row = service.enqueue_inbound_email(
            db,
            to_address=a.email_address,
            from_address="customer@example.com",
            subject="hi",
            body_text="...",
            provider_message_id="msg-3",
        )
        assert row is None
        assert db.tables["agt_inbound_messages"] == []

    def test_recipient_match_is_case_insensitive(self, db):
        a = _provision(db, ORG_A)
        found = service.resolve_email_recipient(db, a.email_address.upper())
        assert found is not None and found.org_id == ORG_A

    def test_redelivery_is_idempotent(self, db):
        a = _provision(db, ORG_A)
        kwargs = dict(
            to_address=a.email_address,
            from_address="customer@example.com",
            subject="hi",
            body_text="...",
            provider_message_id="msg-4",
        )
        first = service.enqueue_inbound_email(db, **kwargs)
        second = service.enqueue_inbound_email(db, **kwargs)
        assert first["id"] == second["id"] if "id" in first else first == second
        assert len(db.tables["agt_inbound_messages"]) == 1

    def test_same_message_to_two_orgs_queues_once_per_org(self, db):
        a = _provision(db, ORG_A)
        b = _provision(db, ORG_B)
        for identity in (a, b):
            service.enqueue_inbound_email(
                db,
                to_address=identity.email_address,
                from_address="customer@example.com",
                subject="hi",
                body_text="...",
                provider_message_id="msg-5",
            )
        rows = db.tables["agt_inbound_messages"]
        assert len(rows) == 2
        assert {r["org_id"] for r in rows} == {ORG_A, ORG_B}

    def test_inbound_list_never_exposes_bronze(self, db):
        a = _provision(db, ORG_A)
        service.enqueue_inbound_email(
            db,
            to_address=a.email_address,
            from_address="customer@example.com",
            subject="secret subject",
            body_text="secret body",
            provider_message_id="msg-6",
        )
        listed = service.list_inbound(db, ORG_A)
        assert len(listed) == 1
        assert "from_address" not in listed[0]
        assert "subject" not in listed[0]
        assert "body_text" not in listed[0]
        assert (
            listed[0]["from_address_hash"]
            == hashlib.sha256(b"customer@example.com").hexdigest()
        )

    def test_enqueue_requires_provider_message_id(self, db):
        a = _provision(db, ORG_A)
        with pytest.raises(IdentityError):
            service.enqueue_inbound_email(
                db,
                to_address=a.email_address,
                from_address="customer@example.com",
                subject="hi",
                body_text="...",
                provider_message_id="",
            )


class TestInboundSignature:
    KEY = b"unit-test-signing-key"
    SECRET = "whsec_" + base64.b64encode(KEY).decode()

    def _sign(self, msg_id, timestamp, body):
        content = f"{msg_id}.{timestamp}.".encode() + body
        return (
            "v1,"
            + base64.b64encode(
                hmac.new(self.KEY, content, hashlib.sha256).digest()
            ).decode()
        )

    def test_valid_signature_verifies(self):
        body = b'{"type":"email.received"}'
        ts = str(int(time.time()))
        sig = self._sign("msg_1", ts, body)
        assert service.verify_inbound_signature(self.SECRET, "msg_1", ts, sig, body)

    def test_tampered_body_fails(self):
        body = b'{"type":"email.received"}'
        ts = str(int(time.time()))
        sig = self._sign("msg_1", ts, body)
        assert not service.verify_inbound_signature(
            self.SECRET, "msg_1", ts, sig, body + b" "
        )

    def test_stale_timestamp_fails(self):
        body = b"{}"
        ts = str(int(time.time()) - 3600)
        sig = self._sign("msg_1", ts, body)
        assert not service.verify_inbound_signature(self.SECRET, "msg_1", ts, sig, body)

    def test_missing_secret_or_headers_fail_closed(self):
        assert not service.verify_inbound_signature(None, "m", "1", "v1,x", b"{}")
        assert not service.verify_inbound_signature(
            self.SECRET, None, "1", "v1,x", b"{}"
        )
        assert not service.verify_inbound_signature(self.SECRET, "m", "1", None, b"{}")

    def test_malformed_secret_fails_closed(self):
        ts = str(int(time.time()))
        assert not service.verify_inbound_signature(
            "whsec_!!!not-base64!!!", "m", ts, "v1,x", b"{}"
        )

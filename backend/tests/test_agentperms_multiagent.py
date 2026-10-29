"""TW-787 Twistor permission ledger — per-agent scoping.

TW-768's ledger was org-shaped: one agent per shop, grants keyed
(org, action class) with agent_identity_id carried by value. The
TW-787 roster makes scoping real:

- a grant may be org-wide (agent_identity_id NULL: every agent) or
  per-agent; the per-agent row wins for that agent,
- revoking ONE agent flips/shadows only that agent's grants — a
  roster-mate is never touched,
- an org revoke-all still halts EVERY agent,
- caps are read from the row that governs the acting agent,
- retained copies schedule per agent on a per-agent revocation.
"""

import pytest

from app.agentperms import service
from tests.test_agentperms_service import FakeDb

ORG = "org-1"
OWNER = "owner-1"
AGENT_A = "agent-a"
AGENT_B = "agent-b"


@pytest.fixture()
def db():
    return FakeDb()


def _grant(db, action_class, *, agent=None, cap_cents=None):
    return service.grant_permission(
        db,
        ORG,
        action_class,
        actor=OWNER,
        cap_cents=cap_cents,
        agent_identity_id=agent,
    )


def _check(db, action_class, *, agent=None, amount_cents=None):
    return service.check_permission(
        db, ORG, action_class, amount_cents=amount_cents, agent_identity_id=agent
    )


class TestGrantScoping:
    def test_org_wide_grant_covers_every_agent(self, db):
        _grant(db, "email.send")
        assert _check(db, "email.send", agent=AGENT_A).allowed
        assert _check(db, "email.send", agent=AGENT_B).allowed
        assert _check(db, "email.send").allowed

    def test_per_agent_grant_covers_only_that_agent(self, db):
        _grant(db, "email.send", agent=AGENT_A)
        assert _check(db, "email.send", agent=AGENT_A).allowed
        decision_b = _check(db, "email.send", agent=AGENT_B)
        assert not decision_b.allowed
        assert decision_b.reason == "no_grant"

    def test_per_agent_row_wins_over_org_wide(self, db):
        _grant(db, "payment.charge", cap_cents=5000)
        _grant(db, "payment.charge", agent=AGENT_A, cap_cents=1000)
        assert not _check(
            db, "payment.charge", agent=AGENT_A, amount_cents=2000
        ).allowed
        assert _check(db, "payment.charge", agent=AGENT_B, amount_cents=2000).allowed

    def test_per_agent_deny_shadows_org_wide_grant(self, db):
        _grant(db, "sms.send")
        service.deny_permission(
            db, ORG, "sms.send", actor=OWNER, agent_identity_id=AGENT_A
        )
        assert _check(db, "sms.send", agent=AGENT_A).reason == "denied"
        assert _check(db, "sms.send", agent=AGENT_B).allowed


class TestPerAgentRevocation:
    def test_revoke_one_agent_never_touches_the_other(self, db):
        _grant(db, "email.send")
        _grant(db, "booking.create")
        service.revoke(
            db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT_A
        )
        for action_class in ("email.send", "booking.create"):
            assert _check(db, action_class, agent=AGENT_A).reason == "revoked"
            assert _check(db, action_class, agent=AGENT_B).allowed
        assert service.agent_status(db, ORG, agent_identity_id=AGENT_A) == "revoked"
        assert service.agent_status(db, ORG, agent_identity_id=AGENT_B) == "active"
        assert service.agent_status(db, ORG) == "active"
        with pytest.raises(service.AgentWorkRefused):
            service.assert_accepting_work(db, ORG, agent_identity_id=AGENT_A)
        service.assert_accepting_work(db, ORG, agent_identity_id=AGENT_B)

    def test_revoke_class_for_one_agent(self, db):
        _grant(db, "email.send")
        _grant(db, "sms.send")
        service.revoke(
            db,
            ORG,
            {"strength": "class", "target": "email.send"},
            actor=OWNER,
            agent_identity_id=AGENT_A,
        )
        assert _check(db, "email.send", agent=AGENT_A).reason == "revoked"
        assert _check(db, "sms.send", agent=AGENT_A).allowed
        assert _check(db, "email.send", agent=AGENT_B).allowed

    def test_regrant_after_per_agent_revoke_restores_only_that_agent(self, db):
        _grant(db, "email.send")
        service.revoke(
            db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT_A
        )
        _grant(db, "email.send", agent=AGENT_A)
        assert _check(db, "email.send", agent=AGENT_A).allowed
        assert service.agent_status(db, ORG, agent_identity_id=AGENT_A) == "active"

    def test_org_revoke_all_halts_every_agent(self, db):
        _grant(db, "email.send")
        _grant(db, "sms.send", agent=AGENT_A)
        _grant(db, "booking.create", agent=AGENT_B)
        service.revoke(db, ORG, {"strength": "all"}, actor=OWNER)
        for agent in (AGENT_A, AGENT_B):
            for action_class in ("email.send", "sms.send", "booking.create"):
                assert not _check(db, action_class, agent=agent).allowed
            assert service.agent_status(db, ORG, agent_identity_id=agent) == "revoked"
            with pytest.raises(service.AgentWorkRefused):
                service.assert_accepting_work(db, ORG, agent_identity_id=agent)
        assert service.agent_status(db, ORG) == "revoked"

    def test_per_agent_revoke_schedules_only_that_agents_copies(self, db):
        _grant(db, "email.send")
        copy_a = service.register_retained_copy(
            db,
            ORG,
            copy_kind="inbox-export",
            locator="blob:a",
            registered_by=OWNER,
            agent_identity_id=AGENT_A,
        )
        copy_b = service.register_retained_copy(
            db,
            ORG,
            copy_kind="inbox-export",
            locator="blob:b",
            registered_by=OWNER,
            agent_identity_id=AGENT_B,
        )
        service.revoke(
            db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT_A
        )
        copies = {c["id"]: c for c in service.list_retained_copies(db, ORG)}
        assert copies[copy_a["id"]]["purge_after"] is not None
        assert copies[copy_b["id"]]["purge_after"] is None

    def test_late_copy_for_revoked_agent_lands_on_its_clock(self, db):
        _grant(db, "email.send")
        service.revoke(
            db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT_A
        )
        late = service.register_retained_copy(
            db,
            ORG,
            copy_kind="call-recording",
            locator="blob:late",
            registered_by=OWNER,
            agent_identity_id=AGENT_A,
        )
        assert late["purge_after"] is not None
        on_time = service.register_retained_copy(
            db,
            ORG,
            copy_kind="call-recording",
            locator="blob:fine",
            registered_by=OWNER,
            agent_identity_id=AGENT_B,
        )
        assert on_time["purge_after"] is None


class TestGateScoping:
    def test_ledger_fence_is_per_agent(self, db):
        from app.agentperms.gate import LedgerFence

        _grant(db, "email.send")
        service.revoke(
            db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT_A
        )
        fence_a = LedgerFence(db, ORG, agent_identity_id=AGENT_A)
        fence_b = LedgerFence(db, ORG, agent_identity_id=AGENT_B)
        with pytest.raises(service.PermissionDenied):
            fence_a.check("email.send")
        assert fence_b.check("email.send").allowed
        with pytest.raises(service.AgentWorkRefused):
            fence_a.assert_accepting_work()
        fence_b.assert_accepting_work()

"""TW-787 Twistor Agent Identity — multi-agent roster (Spec 1 amendment).

Justynn's caveat to Spec 1, recorded the day Spec 1 gated: a shop may
run MORE THAN ONE agent. The subscription includes ONE agent (the
everything agent, full capability); additional agents are hires at a
one-time agent.hire fee, each the SAME established agent shape pushed
into the account, then assigned a role and (TW-788) an archetype.

Pinned here:
- the first agent per org is included and posts NO hire toll,
- a hire posts the agent.hire toll exactly once, retries included,
- email allocation stays deterministic, extended per agent slug,
- every agent keeps its OWN identity and persona (TW-766): the
  channel reads are byte-identical per agent across its channels,
  and one agent's mutations never smear onto a roster-mate,
- role and archetype assignment never touch the identity fields
  (one email, one number, per TW-766).
"""

import json

import pytest

from app.agent_identity import service
from app.agent_identity.models import IdentityError
from tests.test_agent_identity_service import NUM_A, ORG_A, ORG_B, FakeDB

NUM_A2 = "+13035550199"


@pytest.fixture()
def db():
    fake = FakeDB()
    fake.tables["toll_events"] = []
    fake.tables["org_phone_numbers"].append({"org_id": ORG_A, "phone_number": NUM_A2})
    return fake


def _included(db, org=ORG_A, name="Sam"):
    return service.provision_identity(db, org, name, actor="owner-1")


def _hire(db, org=ORG_A, name="Max", **kwargs):
    return service.hire_agent(db, org, name, actor="owner-1", **kwargs)


def _hire_tolls(db):
    return [r for r in db.tables["toll_events"] if r["event_type"] == "agent.hire"]


def _hired_id(db, org=ORG_A):
    return [
        a for a in service.list_agents(db, org) if not a.included_with_subscription
    ][0].id


class TestRosterProvisioning:
    def test_first_agent_is_included_and_posts_no_toll(self, db):
        agent = _included(db)
        assert agent.included_with_subscription is True
        assert agent.role == "everything"
        assert agent.agent_slug == "primary"
        assert agent.archetype_key is None
        assert agent.email_address == "summit-air-11111111@agents.twistor.co"
        assert _hire_tolls(db) == []

    def test_hire_before_the_included_agent_fails_loud(self, db):
        with pytest.raises(IdentityError, match="included agent first"):
            _hire(db)

    def test_hire_creates_a_second_full_agent(self, db):
        _included(db)
        hired = _hire(db, name="Max", role="sales")
        assert hired.included_with_subscription is False
        assert hired.role == "sales"
        assert hired.agent_slug == "max"
        # Deterministic per-agent extension of the org's address.
        assert hired.email_address == "summit-air-11111111-max@agents.twistor.co"
        assert hired.status == "unprovisioned"
        roster = service.list_agents(db, ORG_A)
        assert [a.agent_slug for a in roster] == ["primary", "max"]

    def test_same_name_hires_get_distinct_slugs_and_addresses(self, db):
        _included(db)
        first = _hire(db, name="Max")
        second = _hire(db, name="Max")
        assert first.agent_slug == "max"
        assert second.agent_slug == "max-2"
        assert first.email_address != second.email_address
        assert len(_hire_tolls(db)) == 2

    def test_roster_is_org_scoped(self, db):
        _included(db)
        _included(db, org=ORG_B, name="Jo")
        _hire(db, name="Max")
        assert len(service.list_agents(db, ORG_A)) == 2
        assert len(service.list_agents(db, ORG_B)) == 1
        assert service.get_agent(db, ORG_B, _hired_id(db)) is None


class TestHireTollExactlyOnce:
    def test_hire_posts_one_toll_at_the_configured_fee(self, db):
        from app.config import AGENT_HIRE_FEE_CENTS

        _included(db)
        hired = _hire(db, name="Max")
        tolls = _hire_tolls(db)
        assert len(tolls) == 1
        toll = tolls[0]
        assert toll["org_id"] == ORG_A
        assert toll["amount_cents"] == AGENT_HIRE_FEE_CENTS
        assert toll["unit_rate_cents"] == AGENT_HIRE_FEE_CENTS
        assert toll["idempotency_key"] == f"agent.hire:{hired.id}"
        assert toll["source_type"] == "agent_identity"
        assert toll["source_id"] == hired.id

    def test_ensure_hire_toll_is_idempotent(self, db):
        _included(db)
        hired = _hire(db, name="Max")
        service.ensure_hire_toll(db, ORG_A, hired.id)
        service.ensure_hire_toll(db, ORG_A, hired.id)
        assert len(_hire_tolls(db)) == 1

    def test_ensure_hire_toll_never_charges_the_included_agent(self, db):
        included = _included(db)
        assert service.ensure_hire_toll(db, ORG_A, included.id) is None
        assert _hire_tolls(db) == []

    def test_failed_toll_post_is_recoverable_via_ensure(self, db, monkeypatch):
        from app.services import toll_service

        _included(db)
        real = toll_service.record_toll_event
        state = {"fail": True}

        def flaky(*args, **kwargs):
            if state["fail"]:
                state["fail"] = False
                raise RuntimeError("ledger unavailable")
            return real(*args, **kwargs)

        monkeypatch.setattr(toll_service, "record_toll_event", flaky)
        with pytest.raises(IdentityError, match="hire toll"):
            _hire(db, name="Max")
        # The agent exists (the hire happened); the toll did not post.
        assert _hire_tolls(db) == []
        hired_id = _hired_id(db)
        service.ensure_hire_toll(db, ORG_A, hired_id)
        service.ensure_hire_toll(db, ORG_A, hired_id)
        assert len(_hire_tolls(db)) == 1


class TestPerAgentPersona:
    def test_channel_config_byte_identical_per_agent(self, db):
        _included(db)
        service.update_persona(db, ORG_A, {"tone": "calm, bookish"}, actor="o")
        hired = _hire(db, name="Max")
        service.update_persona(
            db, ORG_A, {"tone": "brisk, sales-forward"}, actor="o", agent_id=hired.id
        )
        for agent_id in (None, hired.id):
            blocks = [
                service.channel_config(db, ORG_A, ch, agent_id=agent_id)["identity"]
                for ch in ("phone", "email", "sms")
            ]
            dumped = [json.dumps(b, sort_keys=True) for b in blocks]
            assert dumped[0] == dumped[1] == dumped[2]
        primary_block = service.channel_config(db, ORG_A, "email")["identity"]
        hired_block = service.channel_config(db, ORG_A, "email", agent_id=hired.id)[
            "identity"
        ]
        assert primary_block["persona"]["tone"] == "calm, bookish"
        assert hired_block["persona"]["tone"] == "brisk, sales-forward"
        assert primary_block["email_address"] != hired_block["email_address"]

    def test_status_and_coverage_are_per_agent(self, db):
        _included(db)
        hired = _hire(db, name="Max")
        service.set_status(db, ORG_A, "suspended", actor="o", agent_id=hired.id)
        roster = {a.agent_slug: a for a in service.list_agents(db, ORG_A)}
        assert roster["max"].status == "suspended"
        assert roster["primary"].status == "unprovisioned"
        # A suspended hire stops resolving; the included agent still does.
        assert service.resolve_email_recipient(db, hired.email_address) is None
        primary = roster["primary"]
        assert (
            service.resolve_email_recipient(db, primary.email_address).id == primary.id
        )

    def test_phone_binding_is_per_agent_and_exclusive(self, db):
        _included(db)
        hired = _hire(db, name="Max")
        service.bind_phone_number(db, ORG_A, NUM_A, actor="o")
        # The number is the included agent's now: the hire cannot take it.
        with pytest.raises(IdentityError, match="another agent"):
            service.bind_phone_number(db, ORG_A, NUM_A, actor="o", agent_id=hired.id)
        bound = service.bind_phone_number(
            db, ORG_A, NUM_A2, actor="o", agent_id=hired.id
        )
        assert bound.phone_number == NUM_A2
        assert service.resolve_phone(db, NUM_A2).id == hired.id
        assert service.resolve_phone(db, NUM_A).agent_slug == "primary"


class TestNoRosterCap:
    """Justynn's ruling (Oct 29, 2026): NO LIMIT on agent count.
    Same service at 1, 100, or 1,000 agents — no max-agents
    constant, tier gate, or plan check anywhere in provisioning."""

    def test_many_hires_all_provision_and_toll(self, db):
        _included(db)
        for i in range(12):
            _hire(db, name=f"Agent {i}")
        roster = service.list_agents(db, ORG_A)
        assert len(roster) == 13
        assert len(_hire_tolls(db)) == 12
        assert len({a.email_address for a in roster}) == 13

    def test_no_cap_constant_or_gate_in_the_registry_sources(self):
        from pathlib import Path

        import app.agent_identity as pkg

        pkg_dir = Path(pkg.__file__).parent
        for name in ("service.py", "models.py", "router.py"):
            text = (pkg_dir / name).read_text(encoding="utf-8").lower()
            for forbidden in ("max_agents", "agent_limit", "roster_cap", "plan"):
                assert forbidden not in text, f"{forbidden} found in {name}"


class TestIncludedAgentPower:
    """Justynn's ruling (Oct 29, 2026): the included agent is built
    to 10-agent power. Its task budget is 10 standard agent units;
    a hire's baseline is 1. Hires buy concurrency and volume above
    the baseline, never capability the first agent lacks."""

    def test_power_budget_constants(self):
        from app.config import INCLUDED_AGENT_POWER_UNITS, STANDARD_AGENT_POWER_UNITS

        assert STANDARD_AGENT_POWER_UNITS == 1
        assert INCLUDED_AGENT_POWER_UNITS == 10

    def test_included_agent_carries_10_units_hires_carry_1(self, db):
        included = _included(db)
        hired = _hire(db, name="Max")
        assert included.power_units == 10
        assert hired.power_units == 1
        views = {v["agent_slug"]: v for v in service.roster_view(db, ORG_A)}
        assert views["primary"]["power_units"] == 10
        assert views["max"]["power_units"] == 1


class TestRoleAndArchetype:
    def test_assign_role_validates_and_persists(self, db):
        _included(db)
        hired = _hire(db, name="Max")
        updated = service.assign_role(db, ORG_A, hired.id, "hr", actor="owner-1")
        assert updated.role == "hr"
        with pytest.raises(IdentityError, match="unknown agent role"):
            service.assign_role(db, ORG_A, hired.id, "overlord", actor="owner-1")
        actions = [r["action"] for r in db.tables["agt_audit"]]
        assert "role_assign" in actions

    def test_archetype_load_and_reassign_never_touch_identity(self, db):
        _included(db)
        hired = _hire(db, name="Max")
        loaded = service.assign_archetype(db, ORG_A, hired.id, "hr-lead", actor="o")
        assert loaded.archetype_key == "hr-lead"
        assert loaded.email_address == hired.email_address
        assert loaded.agent_name == hired.agent_name
        assert loaded.phone_number == hired.phone_number
        reassigned = service.assign_archetype(
            db, ORG_A, hired.id, "sales-closer", actor="o"
        )
        assert reassigned.archetype_key == "sales-closer"
        assert reassigned.email_address == hired.email_address
        cleared = service.assign_archetype(db, ORG_A, hired.id, None, actor="o")
        assert cleared.archetype_key is None
        with pytest.raises(IdentityError, match="archetype key"):
            service.assign_archetype(db, ORG_A, hired.id, "Not A Key!", actor="o")
        actions = [r["action"] for r in db.tables["agt_audit"]]
        assert actions.count("archetype_assign") == 3

    def test_hire_can_carry_role_and_archetype(self, db):
        _included(db)
        hired = _hire(db, name="Max", role="marketing", archetype_key="ads-runner")
        assert hired.role == "marketing"
        assert hired.archetype_key == "ads-runner"

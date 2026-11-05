"""TW-954 fix round — Rex hard-gate findings F1-F4 (tip 2f2cfb5 FAIL).

Each test here failed against the round-1 code for the reason in
its docstring; the probe scripts were /tmp/rex_tw954_probes.py and
/tmp/rex_tw954_probes2.py (Rex, 2026-11-05).

F1 (policy.py): the floor + ledger layers ran only when an action
class was named, so action_class=None waved a revoked agent, an
approvals-off claim, and a raw-PII projection straight through
B7's own evaluation surface; omitted job/tool classes were
vacuously permitted in unattended contexts.

F2 (service.py, a B1 persistence file riding this branch):
check_permission treated any grant status other than exactly
'denied'/'revoked' as granted, so a raw agt_grants row written
past GrantStatement with status 'bypass'/'Granted'/'active'/''/
'revoked ' acted as a full grant.

F3 (composed.py): build_stop_signal()'s default path computed
the epoch from the unresolved agent parameter instead of the
revocation's agent.

F4 (policy.py): _mask_detail masked string values only — PII
planted as a dict key or an int leaf survived verbatim into the
append-only agt_permission_events history.
"""

import pytest

from app.agentperms import composed, floor, models, policy, service

ORG = "org-1"
AGENT = "agent-1"
OTHER_AGENT = "agent-2"
OWNER = "owner-1"


# -- stateful fake Supabase client (house pattern, policy suite) --


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, mode, payload=None):
        self._db = db
        self._table = table
        self._mode = mode
        self._payload = payload
        self._filters = []
        self._order = None
        self._limit = None

    def eq(self, col, value):
        self._filters.append((col, value))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matching(self):
        rows = self._db.tables.get(self._table, [])
        return [
            r for r in rows if all(r.get(col) == value for col, value in self._filters)
        ]

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", self._db.next_id(self._table))
            self._db.tables.setdefault(self._table, []).append(row)
            return _Result([dict(row)])
        if self._mode == "update":
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return _Result(updated)
        rows = [dict(r) for r in self._matching()]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _Table:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def select(self, _cols="*"):
        return _Query(self._db, self._name, "select")

    def insert(self, row):
        return _Query(self._db, self._name, "insert", row)

    def update(self, payload):
        return _Query(self._db, self._name, "update", payload)


class FakeDb:
    def __init__(self):
        self.tables = {}
        self._counters = {}

    def next_id(self, table):
        self._counters[table] = self._counters.get(table, 0) + 1
        return f"{table}-{self._counters[table]}"

    def table(self, name):
        return _Table(self, name)


# -- helpers ---------------------------------------------------------------


def make_policy(**overrides):
    data = {
        "enabled": True,
        "permitted_job_classes": ["followup.chase"],
        "permitted_tool_classes": ["external.send"],
        "permitted_action_classes": ["email.send"],
        "mandate_scope": {"markets": ["denver"]},
    }
    data.update(overrides)
    return policy.PolicyStatement(**data)


def permissive_policy(db):
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(
            permitted_job_classes=list(policy.JOB_CLASSES),
            permitted_tool_classes=list(policy.TOOL_CLASSES),
            permitted_action_classes=list(models.ACTION_CLASSES),
        ),
        actor=OWNER,
    )


def grant_email(db, *, extends=True):
    return service.grant_permission(
        db,
        ORG,
        "email.send",
        actor=OWNER,
        agent_identity_id=AGENT,
        extends_unattended=extends,
    )


def raw_grant(db, action_class, status, *, cap_cents=None, extends=True):
    """A grant row written straight into the table, past
    GrantStatement — the hostile-row route F2 is about."""
    row = {
        "org_id": ORG,
        "agent_identity_id": AGENT,
        "action_class": action_class,
        "scope": {},
        "cap_cents": cap_cents,
        "extends_unattended": extends,
        "version": 1,
    }
    if status is not ...:
        row["status"] = status
    return db.table("agt_grants").insert(row).execute().data[0]


# -- F1: the floor is global; absent dimensions deny -------------------------


def test_f1_revoked_agent_with_no_action_class_is_floor_blocked():
    """Rex C2: a REVOKED agent evaluated with action_class=None
    returned unattended_permitted on the round-1 code."""
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db)
    service.revoke(db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class=None,
        job_class="followup.chase",
        tool_class="external.send",
    )
    assert decision.allowed is False
    assert decision.floor_rule == "revocation_supremacy"
    assert decision.reason == "floor:revocation_supremacy"
    blocked = [e for e in service.list_history(db, ORG) if e["kind"] == "floor_blocked"]
    assert len(blocked) == 1


def test_f1_approvals_off_with_no_action_class_is_floor_blocked():
    """Rex C3 (approvals leg): floor rule 2 bypassed via None."""
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class=None,
        job_class="followup.chase",
        tool_class="external.send",
        approvals_off=True,
    )
    assert decision.allowed is False
    assert decision.floor_rule == "no_approvals_off"


def test_f1_raw_pii_with_no_action_class_is_floor_blocked():
    """Rex C3 (PII leg): floor rule 3 bypassed via None."""
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="background",
        action_class=None,
        job_class="followup.chase",
        tool_class="external.send",
        pii_raw_in_projection=True,
    )
    assert decision.allowed is False
    assert decision.floor_rule == "pii_bronze_only"


@pytest.mark.parametrize("context", ["scheduled", "event", "background", "delegated"])
def test_f1_unattended_no_action_class_denied_when_facts_clean(context):
    """Rex C1: no action class means no grant leg exists to check;
    unattended evaluation refuses, in every unattended context."""
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context=context,
        action_class=None,
        job_class="followup.chase",
        tool_class="external.send",
    )
    assert decision.allowed is False
    assert decision.reason == "action_class_required"


def test_f1_attended_no_action_class_denied():
    """Rex C4: attended + action_class=None returned 'granted'
    with no ledger check at all on the round-1 code."""
    db = FakeDb()
    grant_email(db)
    decision = policy.evaluate_action(
        db, ORG, AGENT, context="attended", action_class=None
    )
    assert decision.allowed is False
    assert decision.reason == "action_class_required"


def test_f1_unattended_omitted_job_class_denied():
    """Rex C5 (job leg): naming no job class must not vacuously
    pass a policy dimension the action should be checked on."""
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(
            permitted_job_classes=["research.scan"],
            permitted_tool_classes=["read.only"],
        ),
        actor=OWNER,
    )
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="email.send",
        tool_class="external.send",
        toll_event="agent.email_action",
    )
    assert decision.allowed is False
    assert decision.reason == "job_class_required"


def test_f1_unattended_omitted_tool_class_denied():
    """Rex C5 (tool leg)."""
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(
            permitted_job_classes=["research.scan"],
            permitted_tool_classes=["read.only"],
        ),
        actor=OWNER,
    )
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="email.send",
        job_class="followup.chase",
        toll_event="agent.email_action",
    )
    assert decision.allowed is False
    assert decision.reason == "tool_class_required"


def test_f1_unattended_omitted_job_and_tool_denied():
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="email.send",
        toll_event="agent.email_action",
    )
    assert decision.allowed is False
    assert decision.reason == "job_class_required"


def test_f1_unattended_all_dimensions_omitted_denied():
    db = FakeDb()
    permissive_policy(db)
    grant_email(db)
    decision = policy.evaluate_action(db, ORG, AGENT, context="delegated")
    assert decision.allowed is False
    assert decision.reason == "action_class_required"


def test_f1_floor_and_ledger_still_run_before_missing_dimension_denial():
    """Layer order is unchanged: a revoked agent omitting its job
    class is floor-blocked (not merely dimension-denied), and a
    ledger-denied class omitting its job class reads as the
    ledger denial."""
    db = FakeDb()
    permissive_policy(db)
    grant_email(db)
    service.revoke(db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT)
    decision = policy.evaluate_action(
        db, ORG, AGENT, context="scheduled", action_class="email.send"
    )
    assert decision.allowed is False
    assert decision.floor_rule == "revocation_supremacy"

    db = FakeDb()
    permissive_policy(db)
    service.deny_permission(
        db, ORG, "booking.create", actor=OWNER, agent_identity_id=AGENT
    )
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="booking.create",
        toll_event="agent.booking_confirmed",
    )
    assert decision.allowed is False
    assert decision.reason == "denied"


def test_f1_attended_omitted_job_and_tool_still_granted():
    """The absent-dimension law is an unattended law: attended
    work names no job/tool class by design and still faces only
    floor + ledger, exactly as before TW-954."""
    db = FakeDb()
    grant_email(db)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="attended",
        action_class="email.send",
        toll_event="agent.email_action",
    )
    assert decision.allowed is True
    assert decision.reason == "granted"


def test_f1_check_floor_global_facts_need_no_action_class():
    """The floor's global rules as a class-free check: each one
    blocks on its own, and clean facts pass."""
    assert floor.check_floor_global(revoked=True).rule == "revocation_supremacy"
    assert floor.check_floor_global(approvals_off=True).rule == "no_approvals_off"
    assert (
        floor.check_floor_global(pii_raw_in_projection=True).rule == "pii_bronze_only"
    )
    assert floor.check_floor_global().allowed is True


# -- F2: unknown grant status fails closed (B1 service.py) -------------------

HOSTILE_STATUSES = [
    "bypass",
    "Granted",
    "GRANTED",
    "active",
    "",
    "revoked ",
    " granted",
]


@pytest.mark.parametrize("status", HOSTILE_STATUSES)
def test_f2_check_permission_unknown_grant_status_denies(status):
    """Rex probes round 2: every one of these statuses acted as a
    full grant through check_permission on the round-1 code."""
    db = FakeDb()
    raw_grant(db, "booking.create", status)
    decision = service.check_permission(
        db, ORG, "booking.create", agent_identity_id=AGENT
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_grant_status"


def test_f2_check_permission_missing_status_denies():
    db = FakeDb()
    raw_grant(db, "booking.create", ...)
    decision = service.check_permission(
        db, ORG, "booking.create", agent_identity_id=AGENT
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_grant_status"


@pytest.mark.parametrize("action_class", ["payment.charge", "purchase"])
def test_f2_forged_money_grant_cannot_charge_under_its_own_cap(action_class):
    """Rex verified live: payment.charge amount 100 sailed through
    under a forged 'bypass' row carrying a 500 cap."""
    db = FakeDb()
    raw_grant(db, action_class, "bypass", cap_cents=50000)
    decision = service.check_permission(
        db, ORG, action_class, amount_cents=10000, agent_identity_id=AGENT
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_grant_status"


@pytest.mark.parametrize("status", HOSTILE_STATUSES)
def test_f2_evaluate_action_forged_grant_status_denies(status):
    """The grant table gets the hostile-row coverage the policy
    table already had: a fully permissive policy cannot rescue a
    grant whose status is not exactly 'granted'."""
    db = FakeDb()
    permissive_policy(db)
    raw_grant(db, "booking.create", status)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="booking.create",
        job_class="followup.chase",
        tool_class="external.send",
        toll_event="agent.booking_confirmed",
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_grant_status"


def test_f2_evaluate_action_forged_payment_grant_denies():
    db = FakeDb()
    permissive_policy(db)
    raw_grant(db, "payment.charge", "bypass", cap_cents=50000)
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="payment.charge",
        job_class="followup.chase",
        tool_class="external.send",
        amount_cents=10000,
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_grant_status"


def test_f2_known_statuses_still_read_exactly():
    """The allowlist is exact-match, not a new vocabulary: the
    three statuses GrantStatement can state keep their meanings."""
    db = FakeDb()
    service.grant_permission(
        db, ORG, "booking.create", actor=OWNER, agent_identity_id=AGENT
    )
    decision = service.check_permission(
        db, ORG, "booking.create", agent_identity_id=AGENT
    )
    assert decision.allowed is True and decision.reason == "granted"

    db = FakeDb()
    service.deny_permission(
        db, ORG, "booking.create", actor=OWNER, agent_identity_id=AGENT
    )
    decision = service.check_permission(
        db, ORG, "booking.create", agent_identity_id=AGENT
    )
    assert decision.allowed is False and decision.reason == "denied"

    db = FakeDb()
    raw_grant(db, "booking.create", "revoked")
    decision = service.check_permission(
        db, ORG, "booking.create", agent_identity_id=AGENT
    )
    assert decision.allowed is False and decision.reason == "revoked"


# -- F3: build_stop_signal default path resolves the agent first -------------


def test_f3_build_stop_signal_default_call_for_per_agent_revocation():
    """Rex F5: the default call raised LedgerError 'epoch must be
    >= 1, got 0' for a per-agent revocation on the round-1 code;
    the round-1 test masked it by always passing the agent."""
    db = FakeDb()
    grant_email(db)
    revocation = service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT
    )
    signal = composed.build_stop_signal(db, revocation)
    assert signal.agent_identity_id == AGENT
    assert signal.epoch == composed.current_epoch(db, ORG, AGENT) == 1
    assert signal.revocation_id == revocation["id"]


def test_f3_build_stop_signal_default_epoch_with_orgwide_revocations_present():
    """With org-wide revocations in the table, the unresolved
    parameter would silently stamp the ORG epoch onto a per-agent
    signal. The default call must use the revocation's own scope,
    for both revocation shapes."""
    db = FakeDb()
    grant_email(db)
    org_revocation = service.revoke(db, ORG, {"strength": "all"}, actor=OWNER)
    agent_revocation = service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT
    )
    assert composed.current_epoch(db, ORG, AGENT) == 2
    assert composed.current_epoch(db, ORG, None) == 1

    agent_signal = composed.build_stop_signal(db, agent_revocation)
    assert agent_signal.agent_identity_id == AGENT
    assert agent_signal.epoch == 2

    org_signal = composed.build_stop_signal(db, org_revocation)
    assert org_signal.agent_identity_id is None
    assert org_signal.epoch == 1


def test_f3_build_stop_signal_explicit_agent_still_wins():
    """The explicit-argument path the round-1 tests used keeps its
    meaning: a caller fanning an org-wide stop out per agent can
    still name the agent."""
    db = FakeDb()
    grant_email(db)
    revocation = service.revoke(db, ORG, {"strength": "all"}, actor=OWNER)
    signal = composed.build_stop_signal(db, revocation, agent_identity_id=OTHER_AGENT)
    assert signal.agent_identity_id == OTHER_AGENT
    assert signal.epoch == composed.current_epoch(db, ORG, OTHER_AGENT)


# -- F4: audit masking covers keys and non-string leaves ----------------------


def _policy_set_event(db):
    events = [
        e
        for e in service.list_history(db, ORG)
        if e["kind"] == "policy_set" and e.get("agent_identity_id") == AGENT
    ]
    assert len(events) == 1
    return events[0]


def test_f4_policy_set_event_masks_email_planted_as_dict_key():
    """Rex G1b: the raw email survived verbatim as a mandate_scope
    KEY in the stored policy_set event on the round-1 code."""
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(mandate_scope={"owner@example.com": "x"}),
        actor=OWNER,
    )
    event = _policy_set_event(db)
    assert "owner@example.com" not in str(event["detail"])


def test_f4_policy_set_event_masks_phone_planted_as_int_leaf():
    """Rex G1b: a 10-digit phone planted as an INT leaf survived
    unmasked on the round-1 code."""
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(mandate_scope={"phone_int": 3035550142}),
        actor=OWNER,
    )
    event = _policy_set_event(db)
    assert "3035550142" not in str(event["detail"])


def test_f4_policy_set_event_masks_nested_containers():
    """PII planted deep: in a nested list, a nested dict's key,
    and a nested int leaf — none of it reaches the history raw."""
    db = FakeDb()
    scope = {
        "outer": [{"deep": "call 303-555-0142"}],
        "list": [3035550142, {"owner@example.com": 1234567890}],
    }
    policy.set_policy(db, ORG, AGENT, make_policy(mandate_scope=scope), actor=OWNER)
    blob = str(_policy_set_event(db)["detail"])
    assert "owner@example.com" not in blob
    assert "303-555-0142" not in blob
    assert "3035550142" not in blob
    assert "1234567890" not in blob


def test_f4_mask_detail_recursion_directly():
    masked = policy._mask_detail(
        {
            "owner@example.com": 3035550142,
            "nested": {"phone": "303-555-0142", "tags": ["a@b.com", 1234567]},
            "version": 2,
            "enabled": True,
            "note": None,
        }
    )
    blob = str(masked)
    assert "owner@example.com" not in blob
    assert "3035550142" not in blob
    assert "303-555-0142" not in blob
    assert "a@b.com" not in blob
    assert "1234567" not in blob
    # Non-PII structure survives untouched: small ints keep their
    # type and value, bools and None pass through.
    assert masked["version"] == 2
    assert masked["enabled"] is True
    assert masked["note"] is None


def test_f4_masking_does_not_regress_values_or_event_shape():
    """Values masked correctly before the fix and still do; the
    policy_set event keeps its ordinary fields readable."""
    db = FakeDb()
    saved = policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(mandate_scope={"contact": "owner@example.com", "seats": 3}),
        actor=OWNER,
    )
    event = _policy_set_event(db)
    assert "owner@example.com" not in str(event["detail"])
    assert event["detail"]["version"] == saved["version"]
    assert event["detail"]["mandate_scope"]["seats"] == 3
    assert event["detail"]["permitted_action_classes"] == ["email.send"]

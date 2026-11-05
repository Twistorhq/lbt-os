"""TW-954 (TW-948-B7) — unattended policy, evaluation, composed stop.

Deny-by-default unattended operation, on the B1 scope discipline
(org, agent, derived workspace on every query):

- no policy row means no unattended action, ever;
- an attended grant does NOT extend to unattended work unless the
  grant itself says so AND a policy permits the class;
- a hostile policy row written past the model layer (straight
  into the table, floor-disable keys and all) still cannot relax
  the hard floor — evaluation consults the floor in code;
- cross-scope policy reads/writes are invisible or refused;
- policy changes and floor-blocked actions write masked audit
  events into the same agt_permission_events history;
- the composed-stop contract: an epoch fence a queued consumer
  can check, and a status read that honestly reports which of
  the four stop targets have confirmed (propagation into B4/B5
  is the second half of B7 and is NOT wired here).
"""

import pytest

from app.agentperms import composed, models, policy, service

ORG = "org-1"
OTHER_ORG = "org-2"
AGENT = "agent-1"
OTHER_AGENT = "agent-2"
OWNER = "owner-1"


# -- stateful fake Supabase client (house pattern, test_agentperms_service) --


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


def grant_email(db, *, extends=False):
    return service.grant_permission(
        db,
        ORG,
        "email.send",
        actor=OWNER,
        agent_identity_id=AGENT,
        extends_unattended=extends,
    )


def evaluate(db, **overrides):
    kwargs = {
        "context": "scheduled",
        "action_class": "email.send",
        "job_class": "followup.chase",
        "tool_class": "external.send",
        "toll_event": "agent.email_action",
    }
    kwargs.update(overrides)
    return policy.evaluate_action(db, ORG, AGENT, **kwargs)


# -- policy model: no surface can state a floor relaxation -------------------


def test_policy_statement_rejects_floor_override_keys_in_mandate_scope():
    for key in ("dnc_override", "floor_disabled", "approvals_off", "bypass_floor"):
        with pytest.raises(models.LedgerError):
            make_policy(mandate_scope={key: True})
    with pytest.raises(models.LedgerError):
        make_policy(mandate_scope={"nested": {"floor": {"dnc": "off"}}})


def test_policy_statement_rejects_approvals_off_field_outright():
    with pytest.raises(models.LedgerError):
        policy.PolicyStatement(enabled=True, approvals_off=True)


def test_policy_statement_rejects_unknown_vocabulary():
    with pytest.raises(models.LedgerError):
        make_policy(permitted_job_classes=["job.fly_to_moon"])
    with pytest.raises(models.LedgerError):
        make_policy(permitted_tool_classes=["tool.root_shell"])
    with pytest.raises(models.LedgerError):
        make_policy(permitted_action_classes=["email.blast_everyone"])


# -- storage: B1 scope discipline -------------------------------------------


def test_policy_roundtrip_is_scoped_to_org_agent_workspace():
    db = FakeDb()
    saved = policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    assert saved["org_id"] == ORG
    assert saved["agent_identity_id"] == AGENT
    assert saved["workspace_id"] == f"{ORG}/{AGENT}"
    assert policy.get_policy(db, ORG, AGENT)["id"] == saved["id"]
    # Cross-scope reads see nothing.
    assert policy.get_policy(db, OTHER_ORG, AGENT) is None
    assert policy.get_policy(db, ORG, OTHER_AGENT) is None


def test_policy_workspace_override_mismatch_is_refused():
    db = FakeDb()
    with pytest.raises(models.LedgerError):
        policy.set_policy(
            db, ORG, AGENT, make_policy(), actor=OWNER, workspace_id="org-9/agent-9"
        )
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    with pytest.raises(models.LedgerError):
        policy.get_policy(db, ORG, AGENT, workspace_id="org-9/agent-9")


def test_policy_change_writes_a_masked_audit_event():
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(mandate_scope={"contact": "owner@example.com"}),
        actor=OWNER,
    )
    events = [
        e
        for e in service.list_history(db, ORG)
        if e["kind"] == "policy_set" and e.get("agent_identity_id") == AGENT
    ]
    assert len(events) == 1
    # The B1 masking pipeline ran over the detail: no raw email survives.
    assert "owner@example.com" not in str(events[0]["detail"])


def test_disable_policy_denies_and_is_audited():
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db, extends=True)
    assert evaluate(db).allowed is True
    policy.disable_policy(db, ORG, AGENT, actor=OWNER)
    decision = evaluate(db)
    assert decision.allowed is False and decision.reason == "policy_disabled"
    kinds = [e["kind"] for e in service.list_history(db, ORG)]
    assert "policy_disabled" in kinds


# -- evaluation: deny by default, layered ------------------------------------


def test_no_policy_row_denies_unattended_even_with_extended_grant():
    db = FakeDb()
    grant_email(db, extends=True)
    decision = evaluate(db)
    assert decision.allowed is False and decision.reason == "no_policy"


def test_attended_grant_does_not_extend_to_unattended_by_itself():
    """The headline acceptance: a scheduled context attempting an
    action the owner granted for attended work denies, because the
    grant does not extend to unattended work."""
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db, extends=False)
    decision = evaluate(db)
    assert decision.allowed is False
    assert decision.reason == "grant_not_extended_unattended"


def test_policy_must_also_permit_the_class():
    db = FakeDb()
    policy.set_policy(
        db,
        ORG,
        AGENT,
        make_policy(permitted_action_classes=["booking.create"]),
        actor=OWNER,
    )
    grant_email(db, extends=True)
    decision = evaluate(db)
    assert decision.allowed is False
    assert decision.reason == "action_class_not_permitted"


def test_job_and_tool_classes_must_be_permitted():
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db, extends=True)
    by_job = evaluate(db, job_class="research.scan")
    assert by_job.allowed is False and by_job.reason == "job_class_not_permitted"
    by_tool = evaluate(db, tool_class="payment.initiate")
    assert by_tool.allowed is False and by_tool.reason == "tool_class_not_permitted"


def test_fully_permitted_unattended_action_is_allowed():
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant = grant_email(db, extends=True)
    decision = evaluate(db)
    assert decision.allowed is True
    assert decision.reason == "unattended_permitted"
    assert decision.grant_id == grant["id"]


def test_attended_context_needs_no_policy_but_still_faces_ledger_and_floor():
    db = FakeDb()
    service.grant_permission(db, ORG, "email.send", actor=OWNER, agent_identity_id=AGENT)
    decision = evaluate(db, context="attended")
    assert decision.allowed is True and decision.reason == "granted"
    no_grant = policy.evaluate_action(
        db,
        ORG,
        OTHER_AGENT,
        context="attended",
        action_class="email.send",
        toll_event="agent.email_action",
    )
    assert no_grant.allowed is False and no_grant.reason == "no_grant"


def test_unknown_context_is_a_construction_error():
    db = FakeDb()
    with pytest.raises(models.LedgerError):
        evaluate(db, context="whenever")


def test_unattended_contexts_are_everything_but_attended():
    assert policy.UNATTENDED_CONTEXTS == frozenset(
        {"scheduled", "event", "background", "delegated"}
    )


# -- the floor cannot be configured away --------------------------------------


def hostile_policy_row(**overrides):
    """A policy row written straight into the table, past every
    model: floor-disable keys, every class permitted."""
    row = {
        "org_id": ORG,
        "agent_identity_id": AGENT,
        "workspace_id": f"{ORG}/{AGENT}",
        "enabled": True,
        "permitted_job_classes": list(policy.JOB_CLASSES),
        "permitted_tool_classes": list(policy.TOOL_CLASSES),
        "permitted_action_classes": list(models.ACTION_CLASSES),
        "mandate_scope": {"floor_disabled": True, "dnc_override": True},
        "version": 1,
    }
    row.update(overrides)
    return row


def test_hostile_policy_row_cannot_relax_the_dnc_floor():
    db = FakeDb()
    db.table("agt_unattended_policies").insert(hostile_policy_row()).execute()
    service.grant_permission(
        db,
        ORG,
        "call.outbound",
        actor=OWNER,
        agent_identity_id=AGENT,
        extends_unattended=True,
    )
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="call.outbound",
        job_class="followup.chase",
        tool_class="external.send",
        lane="tw884_dialer",
        dnc_status="listed",
    )
    assert decision.allowed is False
    assert decision.reason == "floor:no_dnc_override"
    assert decision.floor_rule == "no_dnc_override"


def test_hostile_policy_row_cannot_open_the_phone_lane():
    db = FakeDb()
    db.table("agt_unattended_policies").insert(hostile_policy_row()).execute()
    service.grant_permission(
        db,
        ORG,
        "call.outbound",
        actor=OWNER,
        agent_identity_id=AGENT,
        extends_unattended=True,
    )
    decision = policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="background",
        action_class="call.outbound",
        lane="direct_pstn",
        dnc_status="verified_clean",
    )
    assert decision.allowed is False
    assert decision.floor_rule == "phone_lane_only"


def test_hostile_policy_row_cannot_waive_toll_metering():
    db = FakeDb()
    db.table("agt_unattended_policies").insert(hostile_policy_row()).execute()
    grant_email(db, extends=True)
    decision = evaluate(db, toll_event=None)
    assert decision.allowed is False
    assert decision.floor_rule == "toll_metering_required"


def test_floor_blocked_action_writes_a_masked_audit_event():
    db = FakeDb()
    db.table("agt_unattended_policies").insert(hostile_policy_row()).execute()
    service.grant_permission(
        db,
        ORG,
        "call.outbound",
        actor=OWNER,
        agent_identity_id=AGENT,
        extends_unattended=True,
    )
    policy.evaluate_action(
        db,
        ORG,
        AGENT,
        context="scheduled",
        action_class="call.outbound",
        lane="tw884_dialer",
        dnc_status="listed",
        detail_note="call +1 303-555-0142 now",
    )
    blocked = [
        e for e in service.list_history(db, ORG) if e["kind"] == "floor_blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0]["detail"]["floor_rule"] == "no_dnc_override"
    assert "303-555-0142" not in str(blocked[0]["detail"])
    assert "+1" not in str(blocked[0]["detail"])


# -- revocation supremacy + the composed-stop contract -------------------------


def test_revocation_denies_unattended_work_on_the_next_evaluation():
    db = FakeDb()
    policy.set_policy(db, ORG, AGENT, make_policy(), actor=OWNER)
    grant_email(db, extends=True)
    assert evaluate(db).allowed is True
    service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT
    )
    decision = evaluate(db)
    assert decision.allowed is False
    assert decision.floor_rule == "revocation_supremacy"


def test_epoch_fence_catches_revocation_issued_while_work_is_queued():
    """Rex probe shape: a consumer snapshots the epoch when work
    queues; a revocation landing afterwards must fence its next
    step check, and a fresh snapshot after re-grant passes."""
    db = FakeDb()
    grant_email(db, extends=True)
    epoch_at_queue = composed.current_epoch(db, ORG, AGENT)
    composed.assert_not_fenced(db, ORG, AGENT, epoch_seen=epoch_at_queue)
    revocation = service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT
    )
    with pytest.raises(composed.ComposedStopFenced):
        composed.assert_not_fenced(db, ORG, AGENT, epoch_seen=epoch_at_queue)
    signal = composed.build_stop_signal(db, revocation, agent_identity_id=AGENT)
    assert signal.epoch == epoch_at_queue + 1
    assert signal.targets == composed.STOP_TARGETS
    assert signal.revocation_id == revocation["id"]


def test_composed_stop_status_reports_targets_honestly():
    """Propagation into jobs/triggers/delegated children is the
    second half of B7: until a target writes its receipt, the
    status read must say so, never claim a complete stop."""
    db = FakeDb()
    grant_email(db, extends=True)
    revocation = service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=AGENT
    )
    status = composed.composed_stop_status(
        db, ORG, revocation["id"], agent_identity_id=AGENT
    )
    assert status["ledger"] is True  # the existing revocation receipt
    assert status["jobs"] is False
    assert status["triggers"] is False
    assert status["delegated_children"] is False
    assert status["complete"] is False
    # A target receipt landing (the propagation half's write shape)
    # flips exactly that target.
    service._record_event(
        db,
        ORG,
        composed.TARGET_RECEIPT_KIND,
        "system",
        detail={"revocation_id": revocation["id"], "target": "jobs"},
        agent_identity_id=AGENT,
    )
    status = composed.composed_stop_status(
        db, ORG, revocation["id"], agent_identity_id=AGENT
    )
    assert status["jobs"] is True and status["complete"] is False


def test_epoch_is_scoped_per_agent():
    db = FakeDb()
    service.grant_permission(
        db, ORG, "email.send", actor=OWNER, agent_identity_id=OTHER_AGENT
    )
    service.revoke(
        db, ORG, {"strength": "all"}, actor=OWNER, agent_identity_id=OTHER_AGENT
    )
    assert composed.current_epoch(db, ORG, AGENT) == 0
    assert composed.current_epoch(db, ORG, OTHER_AGENT) == 1
    composed.assert_not_fenced(db, ORG, AGENT, epoch_seen=0)

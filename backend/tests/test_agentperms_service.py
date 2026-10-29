"""TW-768 Twistor Trades agent permission ledger — service behavior.

Runs against a stateful in-memory Supabase stand-in so grants,
revocations, and purges are exercised as state transitions, not
mock call counts. The behaviors pinned here are the TW-764 Spec 2
contract:

- the execution-time check fails closed (no grant, deny, revoked,
  over cap, money action with no amount),
- revocation is one motion: grants flip, tokens cut, retained
  copies scheduled on a stated clock, system receipt in history,
- a fully revoked agent refuses new work on the very next call,
- delete-after-revoke actually purges, and the receipt exists.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.agentperms import models, service

ORG = "org-1"
OTHER_ORG = "org-2"
OWNER = "owner-1"


# -- stateful fake Supabase client ---------------------------------------


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


@pytest.fixture
def db():
    return FakeDb()


def _insert_grant_row(db, org, action_class, *, status="granted", cap_cents=None):
    """Write a grant row directly, past GrantStatement.

    Models the write paths that bypass the model layer's cap law —
    a future Spec 3/5 writer, an ops repair, a data fix. The
    execution gate and the schema are the last lines; these rows
    are how the fix-round tests prove both hold.
    """
    return (
        db.table("agt_grants")
        .insert(
            {
                "org_id": org,
                "agent_identity_id": None,
                "action_class": action_class,
                "status": status,
                "scope": {},
                "cap_cents": cap_cents,
                "granted_by": "ops-repair",
                "granted_at": "2026-10-29T00:00:00+00:00",
                "updated_at": "2026-10-29T00:00:00+00:00",
                "version": 1,
            }
        )
        .execute()
        .data[0]
    )


def _insert_copy_row(db, org, *, purge_after=None):
    """Write a retained-copy row directly, past register_retained_copy
    (a racing runtime write that lands unscheduled)."""
    return (
        db.table("agt_retained_copies")
        .insert(
            {
                "org_id": org,
                "agent_identity_id": None,
                "copy_kind": "email_index",
                "locator": "store://org-1/late-landing",
                "registered_by": "spec3-runtime",
                "created_at": "2026-10-29T00:00:00+00:00",
                "purge_after": purge_after,
                "purged_at": None,
            }
        )
        .execute()
        .data[0]
    )


def _grant_all(db, org=ORG):
    service.grant_permission(db, org, "email.send", actor=OWNER)
    service.grant_permission(db, org, "sms.send", actor=OWNER)
    service.grant_permission(db, org, "call.outbound", actor=OWNER)
    service.grant_permission(db, org, "booking.create", actor=OWNER)
    service.grant_permission(db, org, "booking.reschedule", actor=OWNER)
    service.grant_permission(db, org, "payment.link_send", actor=OWNER)
    service.grant_permission(db, org, "payment.charge", actor=OWNER, cap_cents=50_00)
    service.grant_permission(db, org, "purchase", actor=OWNER, cap_cents=10_00)


# -- the check fails closed ----------------------------------------------


class TestCheckFailsClosed:
    def test_no_grant_denies(self, db):
        d = service.check_permission(db, ORG, "email.send")
        assert not d.allowed and d.reason == "no_grant" and d.grant_id is None

    def test_granted_allows_and_carries_grant_reference(self, db):
        row = service.grant_permission(db, ORG, "email.send", actor=OWNER)
        d = service.check_permission(db, ORG, "email.send")
        assert d.allowed and d.reason == "granted" and d.grant_id == row["id"]

    def test_explicit_deny_denies(self, db):
        service.deny_permission(db, ORG, "sms.send", actor=OWNER)
        d = service.check_permission(db, ORG, "sms.send")
        assert not d.allowed and d.reason == "denied"

    def test_grants_are_per_org(self, db):
        service.grant_permission(db, ORG, "email.send", actor=OWNER)
        d = service.check_permission(db, OTHER_ORG, "email.send")
        assert not d.allowed and d.reason == "no_grant"

    def test_cap_boundary(self, db):
        service.grant_permission(db, ORG, "payment.charge", actor=OWNER, cap_cents=5000)
        assert service.check_permission(
            db, ORG, "payment.charge", amount_cents=5000
        ).allowed
        over = service.check_permission(db, ORG, "payment.charge", amount_cents=5001)
        assert not over.allowed and over.reason == "cap_exceeded"

    def test_money_action_without_amount_denies(self, db):
        service.grant_permission(db, ORG, "purchase", actor=OWNER, cap_cents=1000)
        d = service.check_permission(db, ORG, "purchase")
        assert not d.allowed and d.reason == "amount_required"

    def test_require_raises_permanent_denial(self, db):
        with pytest.raises(service.PermissionDenied) as err:
            service.require_permission(db, ORG, "call.outbound")
        assert err.value.decision.reason == "no_grant"
        # The durable-turns retry classifier must never retry a denial.
        assert err.value._twistor_permanent is True

    def test_regrant_after_deny_restores(self, db):
        service.deny_permission(db, ORG, "email.send", actor=OWNER)
        service.grant_permission(db, ORG, "email.send", actor=OWNER)
        assert service.check_permission(db, ORG, "email.send").allowed


# -- the cap law holds at the gate, past the model layer -------------------


class TestCapLawAtTheGate:
    """Rosa's fix-round probes, as tests: rows written past
    GrantStatement (an ops repair, a future writer) must not turn a
    cap-less money grant into an uncapped, amount-optional one. The
    gate branches on the money class, not on cap presence."""

    def test_capless_money_grant_denies_any_amount(self, db):
        _insert_grant_row(db, ORG, "payment.charge", cap_cents=None)
        for amount in (1, 1_000_000_000):
            d = service.check_permission(db, ORG, "payment.charge", amount_cents=amount)
            assert not d.allowed and d.reason == "cap_missing", amount

    def test_capless_money_grant_denies_no_amount(self, db):
        # Both defects present (no cap, no amount): the denial is
        # what the gate owes; cap_missing takes precedence because
        # the broken grant is the primary fact — the row denies for
        # every request shape, amount or not. (amount_required is
        # the reason for a properly capped row presented without
        # an amount; see TestCheckFailsClosed.)
        _insert_grant_row(db, ORG, "purchase", cap_cents=None)
        d = service.check_permission(db, ORG, "purchase")
        assert not d.allowed and d.reason == "cap_missing"

    def test_capless_grant_raises_permanent_denial_at_require(self, db):
        _insert_grant_row(db, ORG, "purchase", cap_cents=None)
        with pytest.raises(service.PermissionDenied) as err:
            service.require_permission(db, ORG, "purchase", amount_cents=100)
        assert err.value.decision.reason == "cap_missing"
        assert err.value._twistor_permanent is True

    def test_capped_money_row_past_the_model_still_enforces_cap(self, db):
        _insert_grant_row(db, ORG, "payment.charge", cap_cents=2500)
        assert service.check_permission(
            db, ORG, "payment.charge", amount_cents=2500
        ).allowed
        over = service.check_permission(db, ORG, "payment.charge", amount_cents=2501)
        assert not over.allowed and over.reason == "cap_exceeded"

    def test_negative_amount_is_a_denial_never_a_raise(self, db):
        service.grant_permission(db, ORG, "purchase", actor=OWNER, cap_cents=1000)
        d = service.check_permission(db, ORG, "purchase", amount_cents=-1)
        assert not d.allowed and d.reason == "invalid_amount"
        with pytest.raises(service.PermissionDenied) as err:
            service.require_permission(db, ORG, "purchase", amount_cents=-1)
        assert err.value.decision.reason == "invalid_amount"
        assert err.value._twistor_permanent is True

    def test_negative_amount_denied_on_uncapped_class_too(self, db):
        service.grant_permission(db, ORG, "email.send", actor=OWNER)
        d = service.check_permission(db, ORG, "email.send", amount_cents=-5)
        assert not d.allowed and d.reason == "invalid_amount"


# -- revocation: one motion ------------------------------------------------


class TestRevocation:
    def test_revoke_class_flips_only_that_class(self, db):
        _grant_all(db)
        receipt = service.revoke(
            db,
            ORG,
            models.RevokeStatement(strength="class", target="sms.send"),
            actor=OWNER,
        )
        assert not service.check_permission(db, ORG, "sms.send").allowed
        assert service.check_permission(db, ORG, "sms.send").reason == "revoked"
        assert service.check_permission(db, ORG, "email.send").allowed
        assert receipt["receipt"]["detail"]["grants_revoked"] == ["sms.send"]

    def test_revoke_channel_flips_whole_channel(self, db):
        _grant_all(db)
        service.revoke(
            db,
            ORG,
            models.RevokeStatement(strength="channel", target="payment"),
            actor=OWNER,
        )
        assert not service.check_permission(db, ORG, "payment.link_send").allowed
        assert not service.check_permission(
            db, ORG, "payment.charge", amount_cents=1
        ).allowed
        assert service.check_permission(db, ORG, "booking.create").allowed

    def test_revoke_all_denies_every_class(self, db):
        _grant_all(db)
        service.revoke(db, ORG, models.RevokeStatement(strength="all"), actor=OWNER)
        for action_class in models.ACTION_CLASSES:
            d = service.check_permission(db, ORG, action_class, amount_cents=0)
            assert not d.allowed, action_class

    def test_receipt_is_system_generated_and_in_history(self, db):
        _grant_all(db)
        service.revoke(db, ORG, models.RevokeStatement(strength="all"), actor=OWNER)
        history = service.list_history(db, ORG)
        receipts = [e for e in history if e["kind"] == "revocation_receipt"]
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt["actor"] == service.SYSTEM_ACTOR
        assert receipt["detail"]["revoked_by"] == OWNER
        assert receipt["detail"]["strength"] == "all"
        assert set(receipt["detail"]["classes_affected"]) == set(models.ACTION_CLASSES)
        assert receipt["detail"]["purge_due_at"]

    def test_token_cutter_hook_runs_and_is_stamped(self, db):
        _grant_all(db)
        cuts = []
        revocation = service.revoke(
            db,
            ORG,
            models.RevokeStatement(strength="all"),
            actor=OWNER,
            token_cutter=lambda org, identity: cuts.append((org, identity)),
        )
        assert cuts == [(ORG, None)]
        assert revocation["tokens_cut_at"] is not None
        assert revocation["receipt"]["detail"]["token_cut"] == "cut"

    def test_no_token_hook_is_stated_not_claimed(self, db):
        _grant_all(db)
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        assert revocation["tokens_cut_at"] is None
        assert revocation["receipt"]["detail"]["token_cut"] == "no_hook_registered"

    def test_full_history_retained(self, db):
        service.grant_permission(db, ORG, "email.send", actor=OWNER)
        service.deny_permission(db, ORG, "sms.send", actor=OWNER)
        service.revoke(
            db,
            ORG,
            models.RevokeStatement(strength="class", target="email.send"),
            actor=OWNER,
        )
        kinds = [e["kind"] for e in service.list_history(db, ORG)]
        assert "grant" in kinds and "deny" in kinds and "revoke" in kinds
        assert "revocation_receipt" in kinds


# -- intake halt ------------------------------------------------------------


class TestIntakeHalt:
    def test_revoked_agent_refuses_work_immediately(self, db):
        _grant_all(db)
        service.assert_accepting_work(db, ORG)  # active: no raise
        started = datetime.now(timezone.utc)
        service.revoke(db, ORG, models.RevokeStatement(strength="all"), actor=OWNER)
        with pytest.raises(service.AgentWorkRefused):
            service.assert_accepting_work(db, ORG)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        # Spec: a revoked address stops accepting work within one
        # minute. The check is synchronous state, so refusal lands
        # on the first call after revocation — far inside the bound.
        assert elapsed < 60

    def test_regrant_after_revoke_all_reopens_intake(self, db):
        _grant_all(db)
        service.revoke(db, ORG, models.RevokeStatement(strength="all"), actor=OWNER)
        assert service.agent_status(db, ORG) == "revoked"
        service.grant_permission(db, ORG, "email.send", actor=OWNER)
        assert service.agent_status(db, ORG) == "active"
        service.assert_accepting_work(db, ORG)

    def test_partial_revocation_keeps_intake_open(self, db):
        _grant_all(db)
        service.revoke(
            db,
            ORG,
            models.RevokeStatement(strength="class", target="purchase"),
            actor=OWNER,
        )
        assert service.agent_status(db, ORG) == "active"


# -- retained copies + purge -------------------------------------------------


class TestPurge:
    def test_delete_after_revoke_purges_and_receipt_exists(self, db):
        _grant_all(db)
        service.register_retained_copy(
            db,
            ORG,
            copy_kind="email_index",
            locator="store://org-1/mail-index",
            registered_by="spec3-runtime",
        )
        service.register_retained_copy(
            db,
            ORG,
            copy_kind="task_scratch",
            locator="store://org-1/scratch",
            registered_by="spec3-runtime",
        )
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        copies = service.list_retained_copies(db, ORG)
        assert all(c["purge_after"] is not None for c in copies)
        assert all(c["purged_at"] is None for c in copies)

        due = datetime.fromisoformat(revocation["purge_due_at"])
        result = service.run_purge(db, now=due + timedelta(seconds=1))
        assert len(result["purged"]) == 2 and result["failures"] == []
        copies = service.list_retained_copies(db, ORG)
        assert all(c["purged_at"] is not None for c in copies)

        history = service.list_history(db, ORG)
        kinds = [e["kind"] for e in history]
        assert "purge_scheduled" in kinds
        assert kinds.count("purge_completed") == 2
        assert "revocation_receipt" in kinds
        stored = service.list_revocations(db, ORG)[0]
        assert stored["purge_completed_at"] is not None
        assert stored["receipt_event_id"] == revocation["receipt_event_id"]

    def test_purge_before_deadline_does_nothing(self, db):
        _grant_all(db)
        service.register_retained_copy(
            db,
            ORG,
            copy_kind="email_index",
            locator="store://org-1/mail-index",
            registered_by="spec3-runtime",
        )
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        due = datetime.fromisoformat(revocation["purge_due_at"])
        result = service.run_purge(db, now=due - timedelta(hours=1))
        assert result["purged"] == []

    def test_failing_purge_hook_leaves_copy_unpurged(self, db):
        _grant_all(db)
        service.register_retained_copy(
            db,
            ORG,
            copy_kind="email_index",
            locator="store://org-1/mail-index",
            registered_by="spec3-runtime",
        )
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        due = datetime.fromisoformat(revocation["purge_due_at"])

        def _boom(_copy):
            raise RuntimeError("store unreachable")

        result = service.run_purge(db, now=due + timedelta(seconds=1), purge_hook=_boom)
        assert result["purged"] == [] and len(result["failures"]) == 1
        copy = service.list_retained_copies(db, ORG)[0]
        assert copy["purged_at"] is None

    def test_zero_copy_revocation_completes(self, db):
        # The pilot-common case Rosa probed: revoke-all with no
        # retained copies at all. Completion must not wait for an
        # org to be "touched" by a purge — 72h past due it completes.
        _grant_all(db)
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        due = datetime.fromisoformat(revocation["purge_due_at"])
        result = service.run_purge(db, now=due + timedelta(hours=72))
        assert revocation["id"] in result["completed_revocations"]
        stored = service.list_revocations(db, ORG)[0]
        assert stored["purge_completed_at"] is not None

    def test_copy_registered_after_revocation_gets_the_clock(self, db):
        # In-flight work landing late (Spec 3's normal case): the
        # copy registers onto the covering revocation's stated
        # clock, purges on it, and the revocation completes.
        _grant_all(db)
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        copy = service.register_retained_copy(
            db,
            ORG,
            copy_kind="task_scratch",
            locator="store://org-1/late-scratch",
            registered_by="spec3-runtime",
        )
        assert copy["purge_after"] == revocation["purge_due_at"]
        due = datetime.fromisoformat(revocation["purge_due_at"])
        result = service.run_purge(db, now=due + timedelta(seconds=1))
        assert copy["id"] in result["purged"]
        assert revocation["id"] in result["completed_revocations"]
        stored = service.list_retained_copies(db, ORG)[0]
        assert stored["purged_at"] is not None

    def test_unscheduled_copy_of_revoked_org_is_swept(self, db):
        # A copy row that lands unscheduled through any write path
        # (inserted past register_retained_copy) is swept onto the
        # clock by run_purge, then purged on it.
        _grant_all(db)
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        copy = _insert_copy_row(db, ORG)
        assert copy["purge_after"] is None
        due = datetime.fromisoformat(revocation["purge_due_at"])
        result = service.run_purge(db, now=due + timedelta(seconds=1))
        assert copy["id"] in result["swept"]
        assert copy["id"] in result["purged"]
        stored = service.list_retained_copies(db, ORG)[0]
        assert stored["purge_after"] == revocation["purge_due_at"]
        assert stored["purged_at"] is not None

    def test_purge_completed_event_records_hook_presence(self, db):
        # The audit feed must never overstate a registry-level
        # purge as physical deletion: the event says whether a
        # purge hook was wired (Finding 6).
        _grant_all(db)
        service.register_retained_copy(
            db,
            ORG,
            copy_kind="email_index",
            locator="store://org-1/mail-index",
            registered_by="spec3-runtime",
        )
        revocation = service.revoke(
            db, ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        due = datetime.fromisoformat(revocation["purge_due_at"])
        service.run_purge(db, now=due + timedelta(seconds=1))
        events = [
            e for e in service.list_history(db, ORG) if e["kind"] == "purge_completed"
        ]
        assert len(events) == 1 and events[0]["detail"]["purge_hook"] == "none"

        _grant_all(db, org=OTHER_ORG)
        service.register_retained_copy(
            db,
            OTHER_ORG,
            copy_kind="email_index",
            locator="store://org-2/mail-index",
            registered_by="spec3-runtime",
        )
        revocation2 = service.revoke(
            db, OTHER_ORG, models.RevokeStatement(strength="all"), actor=OWNER
        )
        due2 = datetime.fromisoformat(revocation2["purge_due_at"])
        deleted = []
        service.run_purge(
            db,
            now=due2 + timedelta(seconds=1),
            purge_hook=lambda copy: deleted.append(copy["id"]),
        )
        events2 = [
            e
            for e in service.list_history(db, OTHER_ORG)
            if e["kind"] == "purge_completed"
        ]
        assert len(deleted) == 1
        assert len(events2) == 1 and events2[0]["detail"]["purge_hook"] == "wired"

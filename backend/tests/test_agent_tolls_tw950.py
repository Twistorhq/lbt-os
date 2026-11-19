"""TW-950 (TW-948-B3): Section 11 agent toll vocabulary, prove-it tests.

Covers the acceptance set from build map Section 4 (B3):
- each of the five fixed agent events tolls at exactly its Section 11 rate
- duplicate emission with the same idempotency key returns the existing
  row and the ledger total does not move
- booking.recovered rejects amounts outside the 2 to 4 percent band
  (anchor 3 percent) at emission
- an unknown event still fails loud
- the two unnamed gaps (per-call toll, standalone background-review
  toll) stay unnamed and unpriced
"""

from __future__ import annotations

import pytest

from app.config import TOLL_RATES
from app.services import agent_tolls, toll_service


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal supabase-client-shaped chain over an in-memory row list."""

    def __init__(self, store: list[dict]):
        self._store = store
        self._filters: list[tuple[str, object]] = []
        self._gte: tuple[str, object] | None = None
        self._pending_insert: dict | None = None

    def insert(self, row: dict) -> "_Query":
        self._pending_insert = dict(row)
        return self

    def select(self, *_args, **_kwargs) -> "_Query":
        return self

    def eq(self, column: str, value: object) -> "_Query":
        self._filters.append((column, value))
        return self

    def gte(self, column: str, value: object) -> "_Query":
        self._gte = (column, value)
        return self

    def maybe_single(self) -> "_Query":
        return self

    def _matches(self, row: dict) -> bool:
        if any(row.get(col) != val for col, val in self._filters):
            return False
        if self._gte is not None:
            col, val = self._gte
            if str(row.get(col, "")) < str(val):
                return False
        return True

    def execute(self) -> _Result:
        if self._pending_insert is not None:
            row = self._pending_insert
            key = row.get("idempotency_key")
            if key is not None and any(
                r.get("idempotency_key") == key for r in self._store
            ):
                raise Exception(
                    "duplicate key value violates unique constraint "
                    '"toll_events_idempotency_key_key"'
                )
            row.setdefault("occurred_at", "2026-11-05T00:00:00+00:00")
            self._store.append(row)
            return _Result([row])
        matched = [r for r in self._store if self._matches(r)]
        # maybe_single path in toll_service reads .data as a single row.
        if any(col == "idempotency_key" for col, _ in self._filters):
            return _Result(matched[0] if matched else None)
        return _Result(matched)


class FakeDB:
    """In-memory stand-in for the supabase client toll_service uses."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def table(self, _name: str) -> _Query:
        return _Query(self.rows)

    def total_cents(self) -> int:
        return sum(r["amount_cents"] for r in self.rows)


ORG = "org_tw950"

FIXED_EVENTS = {
    "agent.email_action": 5,
    "agent.task_completed": 25,
    "agent.booking_confirmed": 50,
    "agent.invoice_chase_step": 5,
    "agent.estimate_chase_step": 5,
}


def test_five_fixed_events_present_at_exact_section_11_rates():
    for event, cents in FIXED_EVENTS.items():
        assert TOLL_RATES[event] == cents, event


def test_agent_event_vocabulary_is_exactly_the_decided_set():
    agent_events = {k for k in TOLL_RATES if k.startswith("agent.")}
    assert agent_events == {"agent.hire", *FIXED_EVENTS}


def test_unnamed_gaps_stay_unnamed_and_unpriced():
    # No per-call toll and no standalone background-review toll may be
    # invented by this ticket (spec Sections 8.4 and 11 name both gaps).
    for key in TOLL_RATES:
        assert "background" not in key
        assert "review" not in key
    assert not hasattr(agent_tolls, "emit_call")
    assert not hasattr(agent_tolls, "emit_background_review")


@pytest.mark.parametrize(
    ("helper_name", "event", "cents"),
    [
        ("emit_email_action", "agent.email_action", 5),
        ("emit_task_completed", "agent.task_completed", 25),
        ("emit_booking_confirmed", "agent.booking_confirmed", 50),
        ("emit_invoice_chase_step", "agent.invoice_chase_step", 5),
        ("emit_estimate_chase_step", "agent.estimate_chase_step", 5),
    ],
)
def test_each_helper_tolls_at_exactly_its_rate(helper_name, event, cents):
    db = FakeDB()
    helper = getattr(agent_tolls, helper_name)
    row = helper(db, ORG, "task-akeem-1")
    assert row["event_type"] == event
    assert row["unit_rate_cents"] == cents
    assert row["amount_cents"] == cents
    assert row["idempotency_key"] == f"{event}:task-akeem-1"
    assert db.total_cents() == cents


def test_helper_idempotency_root_accepts_fire_id():
    db = FakeDB()
    row = agent_tolls.emit_task_completed(db, ORG, "fire-zuri-7")
    assert row["idempotency_key"] == "agent.task_completed:fire-zuri-7"


def test_duplicate_emission_returns_existing_row_and_total_does_not_move():
    db = FakeDB()
    first = agent_tolls.emit_email_action(db, ORG, "task-akeem-2")
    total_after_first = db.total_cents()
    second = agent_tolls.emit_email_action(db, ORG, "task-akeem-2")
    assert second is first or second == first
    assert len(db.rows) == 1
    assert db.total_cents() == total_after_first == 5
    summary = toll_service.get_toll_summary(db, ORG, since="2026-01-01T00:00:00+00:00")
    assert summary["agent.email_action"] == {"count": 1, "amount_cents": 5}


def test_distinct_roots_toll_separately():
    db = FakeDB()
    agent_tolls.emit_invoice_chase_step(db, ORG, "task-akeem-3")
    agent_tolls.emit_invoice_chase_step(db, ORG, "task-akeem-4")
    assert len(db.rows) == 2
    assert db.total_cents() == 10


def test_booking_recovered_anchor_three_percent():
    db = FakeDB()
    row = agent_tolls.emit_booking_recovered(
        db, ORG, "task-akeem-5", recovered_value_cents=10_000
    )
    # 3 percent of $100.00 is $3.00.
    assert row["event_type"] == "booking.recovered"
    assert row["amount_cents"] == 300
    assert row["idempotency_key"] == "booking.recovered:task-akeem-5"


@pytest.mark.parametrize("percent", [2.0, 2.5, 3.0, 3.5, 4.0])
def test_booking_recovered_inside_band_accepted(percent):
    db = FakeDB()
    row = agent_tolls.emit_booking_recovered(
        db, ORG, "task-band-ok", recovered_value_cents=10_000, percent=percent
    )
    assert row["amount_cents"] == round(10_000 * percent / 100)


@pytest.mark.parametrize("percent", [0.0, 1.0, 1.99, 4.01, 5.0, 10.0])
def test_booking_recovered_outside_band_rejected(percent):
    db = FakeDB()
    with pytest.raises(ValueError):
        agent_tolls.emit_booking_recovered(
            db, ORG, "task-band-bad", recovered_value_cents=10_000, percent=percent
        )
    assert db.rows == []


def test_booking_recovered_direct_emission_out_of_band_rejected():
    # Band validation lives at the ledger emission point, not only in
    # the helper: a caller bypassing the helper still cannot toll an
    # out-of-band recovered amount.
    db = FakeDB()
    with pytest.raises(ValueError, match="band"):
        toll_service.record_toll_event(
            db,
            org_id=ORG,
            event_type="booking.recovered",
            amount_cents=1_000,  # 10 percent of the recovered value
            base_value_cents=10_000,
            idempotency_key="booking.recovered:task-direct-1",
        )
    assert db.rows == []


def test_booking_recovered_direct_emission_in_band_accepted():
    db = FakeDB()
    row = toll_service.record_toll_event(
        db,
        org_id=ORG,
        event_type="booking.recovered",
        amount_cents=250,  # 2.5 percent of the recovered value
        base_value_cents=10_000,
        idempotency_key="booking.recovered:task-direct-2",
    )
    assert row["amount_cents"] == 250
    assert row["unit_rate_cents"] == 0


def test_booking_recovered_without_base_value_fails_loud():
    # Without the recovered value the band cannot be checked, so the
    # emission must refuse rather than toll an unverifiable amount.
    db = FakeDB()
    with pytest.raises(ValueError):
        toll_service.record_toll_event(
            db,
            org_id=ORG,
            event_type="booking.recovered",
            amount_cents=300,
            idempotency_key="booking.recovered:task-direct-3",
        )
    assert db.rows == []


def test_booking_recovered_is_not_a_fixed_rate_event():
    # Percent-of-value events stay out of the fixed rate table; the
    # emitter computes the amount (config contract, TW-550).
    assert "booking.recovered" not in TOLL_RATES


def test_unknown_event_still_fails_loud():
    db = FakeDB()
    with pytest.raises(ValueError, match="Unknown toll event type"):
        toll_service.record_toll_event(
            db,
            org_id=ORG,
            event_type="agent.something_invented",
            idempotency_key="idem-unknown-1",
        )
    assert db.rows == []

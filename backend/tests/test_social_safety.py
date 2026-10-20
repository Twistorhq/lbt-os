"""TW-650 Twistor Social — safety pure functions (TDD RED).

Spec Section 6.6: the consequence ladder (warning → restriction → ban,
with purge as the integrity lifecycle's terminal sanction) and the
appeals workflow (24-hour acknowledgement, 7-day decision by a fresh
reviewer). TW-648 design: serious categories (threats, sexual
misconduct, identity fraud) bypass ambassadors entirely at intake.
"""

from datetime import datetime, timedelta, timezone

from app.social import safety

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class TestAppealSla:
    def test_ack_due_24h(self):
        sla = safety.appeal_sla(T0)
        assert sla.ack_due_at == T0 + timedelta(hours=24)

    def test_decision_due_7d(self):
        sla = safety.appeal_sla(T0)
        assert sla.decision_due_at == T0 + timedelta(days=7)


class TestAmbassadorBypass:
    def test_serious_categories_bypass(self):
        for cat in ("threats", "sexual_misconduct", "identity_fraud"):
            assert safety.ambassador_may_triage(cat) is False, cat

    def test_routine_categories_may_be_triaged(self):
        for cat in ("harassment", "fraud", "age_misrating", "other_serious"):
            assert safety.ambassador_may_triage(cat) is True, cat

    def test_unknown_category_fails_closed_to_staff(self):
        assert safety.ambassador_may_triage("mystery_category") is False


class TestLadder:
    def test_first_offense_warns(self):
        assert safety.next_sanction(None) == "warning"

    def test_repeat_escalates(self):
        assert safety.next_sanction("warning") == "restriction"
        assert safety.next_sanction("restriction") == "purge"

    def test_purge_is_terminal(self):
        assert safety.next_sanction("purge") == "purge"

    def test_severe_skips_the_warning(self):
        assert safety.next_sanction(None, severe=True) == "restriction"
        assert safety.next_sanction("warning", severe=True) == "purge"

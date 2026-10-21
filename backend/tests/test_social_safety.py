"""TW-650 Twistor Social — safety pure functions (TDD RED).

Spec Section 6.6: the consequence ladder is warning → restriction →
identity-ban decision. Purge is NOT a rung on that ladder: it is the
terminal outcome of the Section 6.7 integrity pipeline (flag →
liveness challenge → 14-day grace → purge), because a ban answers
misconduct and a purge answers an identity the platform can no longer
stand behind. Severe offenses leave the ladder entirely for a
temporary safety suspension and the ban decision. Appeals: 24-hour
acknowledgement, 7-day decision by a fresh reviewer. TW-648 design:
serious categories (threats, sexual misconduct, identity fraud) bypass
ambassadors entirely at intake.
"""

from datetime import datetime, timedelta, timezone

import pytest

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


class TestMisconductLadder:
    def test_ladder_is_warning_then_restriction(self):
        assert safety.MISCONDUCT_LADDER == ("warning", "restriction")

    def test_first_offense_warns(self):
        assert safety.next_misconduct_outcome(None) == "warning"

    def test_repeat_restricts(self):
        assert safety.next_misconduct_outcome("warning") == "restriction"

    def test_top_rung_is_a_ban_decision_not_a_level(self):
        # The ban itself is an rsn_bans instrument (Section 6.2); the
        # misconduct ladder only routes to the decision state.
        assert safety.next_misconduct_outcome("restriction") == "ban_decision"
        assert safety.next_misconduct_outcome("ban_decision") == "ban_decision"

    def test_purge_is_not_a_misconduct_state(self):
        with pytest.raises(ValueError):
            safety.next_misconduct_outcome("purge")

    def test_unknown_level_rejected(self):
        with pytest.raises(ValueError):
            safety.next_misconduct_outcome("time_out")

    def test_misconduct_never_produces_purge(self):
        for current in (None, "warning", "restriction", "ban_decision"):
            assert safety.next_misconduct_outcome(current) != "purge"


class TestSevereRoute:
    def test_severe_leaves_the_ladder(self):
        route = safety.severe_offense_route()
        assert route.ladder_applies is False
        assert route.disposition == "temporary_safety_suspension"
        assert route.decision == "ban_decision"

    def test_suspension_is_capped_at_72_hours(self):
        # Section 6.2: maximum 72 hours without a full decision.
        assert safety.severe_offense_route().max_suspension_hours == 72


class TestPurgePipeline:
    def test_stages_in_due_process_order(self):
        assert safety.PURGE_PIPELINE_STAGES == (
            "flagged",
            "challenge_issued",
            "grace_window",
            "purged",
        )

    def test_pipeline_advances_in_order(self):
        assert safety.next_purge_stage("flagged") == "challenge_issued"
        assert safety.next_purge_stage("challenge_issued") == "grace_window"
        assert safety.next_purge_stage("grace_window") == "purged"

    def test_purge_is_terminal(self):
        assert safety.next_purge_stage("purged") == "purged"

    def test_grace_window_is_14_days(self):
        assert safety.PURGE_GRACE_WINDOW == timedelta(days=14)

    def test_unknown_stage_rejected(self):
        with pytest.raises(ValueError):
            safety.next_purge_stage("warned")

"""TW-767 Twistor Agent Identity — models: persona, coverage, record.

Fail-closed defaults are the contract under test: a default persona is
the TW-766 employee (rapport on, low volume); a default coverage
authorizes NOTHING until the shop configures hours.
"""

import pytest

from app.agent_identity.models import (
    AgentIdentity,
    Coverage,
    CoverageWindow,
    IdentityError,
    Persona,
    RapportSettings,
    VoiceSettings,
)


class TestPersonaDefaults:
    def test_default_persona_is_the_standard_employee(self):
        persona = Persona()
        assert persona.voice.style == "warm_professional"
        assert persona.voice.voice_id is None
        assert persona.tone  # non-empty direction for prompt assembly
        assert persona.sign_off == ""  # empty = sign with the agent name

    def test_rapport_defaults_on_at_low_volume(self):
        rapport = RapportSettings()
        assert rapport.enabled is True
        assert rapport.cadence == "low"
        assert rapport.max_checkins_per_week == 1

    def test_rapport_can_be_tuned_off(self):
        rapport = RapportSettings(enabled=False, cadence="off", max_checkins_per_week=0)
        assert rapport.enabled is False

    def test_rapport_off_cadence_must_carry_zero_cap(self):
        with pytest.raises(IdentityError):
            RapportSettings(cadence="off", max_checkins_per_week=2)

    def test_rapport_cap_out_of_range_fails_loud(self):
        with pytest.raises(IdentityError):
            RapportSettings(max_checkins_per_week=8)

    def test_unknown_cadence_fails_loud(self):
        with pytest.raises(IdentityError):
            RapportSettings(cadence="chatty")

    def test_empty_tone_fails_loud(self):
        with pytest.raises(IdentityError):
            Persona(tone="  ")

    def test_empty_voice_style_fails_loud(self):
        with pytest.raises(IdentityError):
            VoiceSettings(style="")

    def test_unknown_field_fails_loud(self):
        with pytest.raises(IdentityError):
            Persona(nickname="Buddy")


class TestCoverageDefaults:
    def test_default_coverage_is_unconfigured_and_closed(self):
        coverage = Coverage()
        assert coverage.mode == "unconfigured"
        assert coverage.windows == []

    def test_unconfigured_cannot_smuggle_windows(self):
        window = CoverageWindow(days=[0], start="08:00", end="17:00")
        with pytest.raises(IdentityError):
            Coverage(mode="unconfigured", windows=[window])

    def test_windows_mode_requires_a_window(self):
        with pytest.raises(IdentityError):
            Coverage(mode="windows", windows=[])

    def test_windows_mode_accepts_real_hours(self):
        coverage = Coverage(
            mode="windows",
            timezone="America/Denver",
            windows=[CoverageWindow(days=[0, 1, 2, 3, 4], start="07:00", end="19:00")],
        )
        assert coverage.mode == "windows"
        assert coverage.windows[0].days == [0, 1, 2, 3, 4]

    def test_always_mode_takes_no_windows(self):
        window = CoverageWindow(days=[0], start="08:00", end="17:00")
        with pytest.raises(IdentityError):
            Coverage(mode="always", windows=[window])

    def test_unknown_mode_fails_loud(self):
        with pytest.raises(IdentityError):
            Coverage(mode="sometimes")


class TestCoverageWindow:
    def test_start_must_precede_end(self):
        with pytest.raises(IdentityError):
            CoverageWindow(days=[1], start="17:00", end="08:00")

    def test_bad_time_format_fails_loud(self):
        with pytest.raises(IdentityError):
            CoverageWindow(days=[1], start="8am", end="17:00")

    def test_day_out_of_range_fails_loud(self):
        with pytest.raises(IdentityError):
            CoverageWindow(days=[7], start="08:00", end="17:00")

    def test_empty_days_fail_loud(self):
        with pytest.raises(IdentityError):
            CoverageWindow(days=[], start="08:00", end="17:00")

    def test_repeated_days_fail_loud(self):
        with pytest.raises(IdentityError):
            CoverageWindow(days=[1, 1], start="08:00", end="17:00")


class TestAgentIdentityRecord:
    def test_defaults_are_fail_closed(self):
        record = AgentIdentity(org_id="org-1", agent_name="Sam")
        assert record.status == "unprovisioned"
        assert record.email_address is None
        assert record.phone_number is None
        assert record.provisioned is False
        assert record.coverage.mode == "unconfigured"

    def test_empty_name_fails_loud(self):
        with pytest.raises(IdentityError):
            AgentIdentity(org_id="org-1", agent_name=" ")

    def test_unknown_status_fails_loud(self):
        with pytest.raises(IdentityError):
            AgentIdentity(org_id="org-1", agent_name="Sam", status="loose")

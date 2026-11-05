"""TW-954 (TW-948-B7) — the hard floor, as pure code.

The floor is code-level, not data-level: check_floor takes facts
about an action and nothing else. There is no policy parameter,
no settings parameter, no flag — a caller cannot hand it a
configuration that relaxes a rule, and these tests pin each rule
by trying to walk an action past it.

Floor rules (spec Sections 8.1/8.2, build map Section 2.9):
- revocation supremacy: a revoked agent acts in no context,
- no approvals-off mode exists for customer Toris,
- PII stays bronze: a raw-PII retrieval projection never ships,
- no DNC override: outbound contact requires verified-clean
  consent/DNC status (TW-943),
- phone stays in the gated lane: call.outbound only via the
  TW-884 dialer lane (TW-885 legal gates ride that lane),
- no external send without mandate,
- no payment initiation beyond mandate (cap law),
- toll metering on every tolled action (M1 vocabulary; classes
  with no named toll event meter at zero per spec Section 8.4).
"""

from app.agentperms import floor


def clean_facts(**overrides):
    facts = {
        "action_class": "booking.create",
        "revoked": False,
        "approvals_off": False,
        "pii_raw_in_projection": False,
        "dnc_status": "verified_clean",
        "lane": None,
        "mandate_allowed": True,
        "amount_cents": None,
        "cap_cents": None,
        "toll_event": "agent.booking_confirmed",
    }
    facts.update(overrides)
    return facts


def test_clean_action_passes_the_floor():
    verdict = floor.check_floor(**clean_facts())
    assert verdict.allowed is True
    assert verdict.rule is None


def test_floor_advertises_no_approvals_off_mode():
    assert floor.APPROVALS_OFF_PERMITTED is False
    assert "no_approvals_off" in floor.FLOOR_RULES


def test_revocation_supremacy_blocks_every_class():
    for action_class in (
        "email.send",
        "booking.create",
        "payment.charge",
        "call.outbound",
    ):
        verdict = floor.check_floor(
            **clean_facts(action_class=action_class, revoked=True)
        )
        assert verdict.allowed is False, action_class
        assert verdict.rule == "revocation_supremacy"


def test_approvals_off_claim_is_blocked():
    verdict = floor.check_floor(**clean_facts(approvals_off=True))
    assert verdict.allowed is False
    assert verdict.rule == "no_approvals_off"


def test_raw_pii_projection_is_blocked():
    verdict = floor.check_floor(**clean_facts(pii_raw_in_projection=True))
    assert verdict.allowed is False
    assert verdict.rule == "pii_bronze_only"


def test_dnc_status_must_be_verified_clean_for_calls_and_texts():
    for action_class in ("call.outbound", "sms.send"):
        for status in ("listed", "unknown", "unverified", None):
            verdict = floor.check_floor(
                **clean_facts(
                    action_class=action_class,
                    dnc_status=status,
                    lane="tw884_dialer" if action_class == "call.outbound" else None,
                    toll_event=None,
                )
            )
            assert verdict.allowed is False, (action_class, status)
            assert verdict.rule == "no_dnc_override"
        ok = floor.check_floor(
            **clean_facts(
                action_class=action_class,
                dnc_status="verified_clean",
                lane="tw884_dialer" if action_class == "call.outbound" else None,
                toll_event=None,
            )
        )
        assert ok.allowed is True, action_class


def test_phone_only_flows_through_the_gated_dialer_lane():
    for lane in (None, "direct_pstn", "policy_opened", "twilio_raw", ""):
        verdict = floor.check_floor(
            **clean_facts(action_class="call.outbound", lane=lane, toll_event=None)
        )
        assert verdict.allowed is False, lane
        assert verdict.rule == "phone_lane_only"
    ok = floor.check_floor(
        **clean_facts(
            action_class="call.outbound", lane="tw884_dialer", toll_event=None
        )
    )
    assert ok.allowed is True
    assert floor.ALLOWED_PHONE_LANES == frozenset({"tw884_dialer"})


def test_external_send_without_mandate_is_blocked():
    for action_class in (
        "email.send",
        "sms.send",
        "call.outbound",
        "payment.link_send",
    ):
        verdict = floor.check_floor(
            **clean_facts(
                action_class=action_class,
                mandate_allowed=False,
                lane="tw884_dialer" if action_class == "call.outbound" else None,
                toll_event=(
                    "agent.email_action" if action_class == "email.send" else None
                ),
            )
        )
        assert verdict.allowed is False, action_class
        assert verdict.rule == "no_external_send_without_mandate"


def test_payment_beyond_mandate_is_blocked():
    for action_class in ("payment.charge", "purchase"):
        no_cap = floor.check_floor(
            **clean_facts(action_class=action_class, amount_cents=100, cap_cents=None)
        )
        assert no_cap.allowed is False and no_cap.rule == "no_payment_beyond_mandate"
        no_amount = floor.check_floor(
            **clean_facts(action_class=action_class, amount_cents=None, cap_cents=500)
        )
        assert (
            no_amount.allowed is False and no_amount.rule == "no_payment_beyond_mandate"
        )
        over = floor.check_floor(
            **clean_facts(action_class=action_class, amount_cents=501, cap_cents=500)
        )
        assert over.allowed is False and over.rule == "no_payment_beyond_mandate"
        within = floor.check_floor(
            **clean_facts(action_class=action_class, amount_cents=500, cap_cents=500)
        )
        assert within.allowed is True, action_class


def test_tolled_actions_must_declare_their_m1_toll_event():
    missing = floor.check_floor(
        **clean_facts(action_class="email.send", toll_event=None)
    )
    assert missing.allowed is False
    assert missing.rule == "toll_metering_required"
    wrong = floor.check_floor(
        **clean_facts(action_class="email.send", toll_event="agent.task_completed")
    )
    assert wrong.allowed is False and wrong.rule == "toll_metering_required"
    ok = floor.check_floor(
        **clean_facts(action_class="email.send", toll_event="agent.email_action")
    )
    assert ok.allowed is True
    # Spec 8.4: calls carry no named toll event and meter at zero —
    # the floor does not invent a price for them.
    call = floor.check_floor(
        **clean_facts(
            action_class="call.outbound", lane="tw884_dialer", toll_event=None
        )
    )
    assert call.allowed is True


def test_floor_check_accepts_no_policy_or_settings_argument():
    """The signature itself is the guarantee: there is no surface
    through which a policy row or a setting could reach the floor."""
    import inspect

    params = set(inspect.signature(floor.check_floor).parameters)
    assert not (params & {"policy", "settings", "config", "overrides", "db"})

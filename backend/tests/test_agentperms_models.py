"""TW-768 Twistor Trades agent permission ledger — model validation.

The vocabulary and the cap rules are the fail-closed posture in
miniature: unknown classes/channels/strengths cannot be stated, a
money grant cannot be stated without a cap, and a non-money class
cannot carry one. Every violation is a raw LedgerError.
"""

import pytest

from app.agentperms import models


class TestVocabulary:
    def test_eight_classes_in_spec_order(self):
        assert models.ACTION_CLASSES == (
            "email.send",
            "sms.send",
            "call.outbound",
            "booking.create",
            "booking.reschedule",
            "payment.link_send",
            "payment.charge",
            "purchase",
        )

    def test_channel_mapping(self):
        assert models.channel_of("booking.reschedule") == "booking"
        assert models.channel_of("payment.charge") == "payment"
        assert models.classes_for_channel("payment") == (
            "payment.link_send",
            "payment.charge",
        )

    def test_unknown_class_rejected(self):
        with pytest.raises(models.LedgerError):
            models.channel_of("email.delete_everything")

    def test_unknown_channel_rejected(self):
        with pytest.raises(models.LedgerError):
            models.classes_for_channel("telepathy")


class TestGrantStatement:
    def test_plain_grant_ok(self):
        g = models.GrantStatement(action_class="email.send")
        assert g.status == "granted" and g.cap_cents is None

    def test_money_grant_requires_cap(self):
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="payment.charge")
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="purchase")

    def test_money_grant_with_cap_ok(self):
        g = models.GrantStatement(action_class="payment.charge", cap_cents=25_00)
        assert g.cap_cents == 2500

    def test_negative_cap_rejected(self):
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="purchase", cap_cents=-1)

    def test_non_money_grant_with_cap_rejected(self):
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="sms.send", cap_cents=100)

    def test_payment_link_send_takes_no_cap(self):
        # Links only: the agent never holds card data, so there is
        # nothing to cap. A cap here would be a lie about the shape.
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="payment.link_send", cap_cents=100)

    def test_unknown_class_rejected(self):
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="fax.send")

    def test_unknown_status_rejected(self):
        with pytest.raises(models.LedgerError):
            models.GrantStatement(action_class="email.send", status="maybe")


class TestRevokeStatement:
    def test_class_strength(self):
        r = models.RevokeStatement(strength="class", target="sms.send")
        assert r.affected_classes() == ("sms.send",)

    def test_channel_strength(self):
        r = models.RevokeStatement(strength="channel", target="booking")
        assert r.affected_classes() == ("booking.create", "booking.reschedule")

    def test_all_strength(self):
        r = models.RevokeStatement(strength="all")
        assert r.affected_classes() == models.ACTION_CLASSES

    def test_class_strength_requires_class_target(self):
        with pytest.raises(models.LedgerError):
            models.RevokeStatement(strength="class", target="booking")
        with pytest.raises(models.LedgerError):
            models.RevokeStatement(strength="class")

    def test_channel_strength_requires_channel_target(self):
        with pytest.raises(models.LedgerError):
            models.RevokeStatement(strength="channel", target="email.send")

    def test_all_strength_takes_no_target(self):
        with pytest.raises(models.LedgerError):
            models.RevokeStatement(strength="all", target="email")

    def test_unknown_strength_rejected(self):
        with pytest.raises(models.LedgerError):
            models.RevokeStatement(strength="everything", target=None)

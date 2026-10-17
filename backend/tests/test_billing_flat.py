"""TW-550 M1 flat billing engine: TDD prove-it tests (RED first)."""
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.auth import PLAN_ORDER, AuthContext, require_plan
from app.services import stripe_service, toll_service
from app.services.ai_audit import PLAN_AUDIT_LIMITS


def _fake_db():
    """Minimal supabase-client-shaped mock: table().insert/update/select...().execute()."""
    db = MagicMock()
    result = MagicMock()
    result.data = []
    tbl = db.table.return_value
    tbl.insert.return_value.execute.return_value = result
    tbl.update.return_value.eq.return_value.execute.return_value = result
    tbl.select.return_value.eq.return_value.execute.return_value = result
    return db


class FlatPlanCatalogTests(unittest.TestCase):
    def test_checkout_trades_starter_uses_flat_price_and_trial(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            session = MagicMock()
            session.url = "https://checkout.stripe.com/test"
            session.id = "cs_test_1"
            return session

        with patch.dict(stripe_service.PLAN_PRICE_MAP, {"trades_starter": "price_starter_123"}), \
             patch.object(stripe_service.stripe.checkout.Session, "create", side_effect=fake_create), \
             patch.object(stripe_service, "get_or_create_stripe_customer", return_value="cus_1"):
            stripe_service.create_checkout_session(
                _fake_db(), "org_1", "Shop", "a@b.c", "trades_starter",
                "https://app/success", "https://app/cancel",
            )
        self.assertEqual(captured["line_items"], [{"price": "price_starter_123", "quantity": 1}])
        self.assertEqual(captured["subscription_data"]["trial_period_days"], 14)

    def test_checkout_legacy_basic_plan_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            stripe_service._configured_price_id("basic")
        self.assertIn("Unknown plan", str(ctx.exception))

    def test_unlimited_techs_on_both_flat_tiers(self):
        self.assertTrue(stripe_service.plan_allows_techs("trades_starter", tech_count=50))
        self.assertTrue(stripe_service.plan_allows_techs("trades_pro", tech_count=500))

    def test_webhook_plan_change_syncs_new_flat_plan(self):
        db = _fake_db()
        sub = MagicMock()
        sub.metadata = {"lbt_org_id": "org_9", "plan": "trades_pro"}
        sub.status = "active"
        sub.id = "sub_9"
        stripe_service.sync_subscription(db, sub)
        update_kwargs = db.table.return_value.update.call_args[0][0]
        self.assertEqual(update_kwargs["plan"], "trades_pro")
        self.assertEqual(update_kwargs["subscription_status"], "active")

    def test_verify_checkout_returns_payload_when_sync_throws(self):
        """TW-208: a failed subscription sync must not 500 a paid checkout."""
        fake_session = MagicMock()
        fake_session.id = "cs_test_2"
        fake_session.client_reference_id = "org_2"
        fake_session.metadata = {"lbt_org_id": "org_2", "plan": "trades_pro"}
        fake_session.subscription = MagicMock()
        fake_session.status = "complete"
        fake_session.payment_status = "paid"
        with patch.object(stripe_service.stripe.checkout.Session, "retrieve", return_value=fake_session), \
             patch.object(stripe_service, "sync_subscription", side_effect=RuntimeError("db down")):
            result = stripe_service.verify_checkout_session(_fake_db(), "org_2", "cs_test_2")
        self.assertEqual(result["plan"], "trades_pro")
        self.assertEqual(result["status"], "complete")


class PlanEntitlementTests(unittest.TestCase):
    """Rosa blockers 1-2: the flat tiers must be honored by the entitlement layer."""

    def test_plan_order_maps_flat_tiers_explicitly(self):
        self.assertGreater(PLAN_ORDER["trades_starter"], PLAN_ORDER["basic"])
        self.assertGreater(PLAN_ORDER["trades_pro"], PLAN_ORDER["trades_starter"])
        self.assertEqual(PLAN_ORDER["trades_pro"], PLAN_ORDER["premium"])

    def test_require_plan_pro_allows_trades_pro_customer(self):
        check = require_plan("pro")
        auth = AuthContext(user_id="u1", org_id="o1", plan="trades_pro")
        self.assertIs(check(auth=auth), auth)

    def test_require_plan_premium_blocks_trades_starter(self):
        check = require_plan("premium")
        auth = AuthContext(user_id="u1", org_id="o1", plan="trades_starter")
        with self.assertRaises(HTTPException) as ctx:
            check(auth=auth)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_audit_caps_explicit_for_flat_tiers(self):
        self.assertEqual(PLAN_AUDIT_LIMITS["trades_starter"], 20)
        self.assertIsNone(PLAN_AUDIT_LIMITS["trades_pro"])


class TollLedgerTests(unittest.TestCase):
    def test_record_toll_event_computes_amount_from_rates(self):
        db = _fake_db()
        with patch.dict(toll_service.TOLL_RATES, {"missed_call.detected": 10}):
            toll_service.record_toll_event(
                db, org_id="org_1", event_type="missed_call.detected",
                quantity=3, idempotency_key="idem-1",
            )
        inserted = db.table.return_value.insert.call_args[0][0]
        self.assertEqual(inserted["org_id"], "org_1")
        self.assertEqual(inserted["unit_rate_cents"], 10)
        self.assertEqual(inserted["amount_cents"], 30)
        self.assertEqual(inserted["idempotency_key"], "idem-1")

    def test_duplicate_idempotency_key_does_not_double_toll(self):
        db = _fake_db()
        # Simulate unique-violation on insert.
        db.table.return_value.insert.return_value.execute.side_effect = Exception(
            'duplicate key value violates unique constraint "toll_events_idempotency_key_key"'
        )
        existing = {"id": "existing-row", "amount_cents": 10}
        db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = existing
        with patch.dict(toll_service.TOLL_RATES, {"missed_call.detected": 10}):
            row = toll_service.record_toll_event(
                db, org_id="org_1", event_type="missed_call.detected",
                quantity=1, idempotency_key="idem-dup",
            )
        self.assertEqual(row, existing)

    def test_unknown_event_type_fails_loud(self):
        with self.assertRaises(ValueError) as ctx:
            toll_service.record_toll_event(
                _fake_db(), org_id="org_1", event_type="nope.unknown",
                quantity=1, idempotency_key="idem-x",
            )
        self.assertIn("Unknown toll event type", str(ctx.exception))

    def test_get_toll_summary_aggregates_per_event_type(self):
        db = _fake_db()
        rows = [
            {"event_type": "sms.auto_reply", "quantity": 2, "amount_cents": 10},
            {"event_type": "sms.auto_reply", "quantity": 1, "amount_cents": 5},
            {"event_type": "missed_call.detected", "quantity": 1, "amount_cents": 10},
        ]
        db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = rows
        summary = toll_service.get_toll_summary(db, "org_1", since="2026-01-01T00:00:00+00:00")
        self.assertEqual(summary["sms.auto_reply"], {"count": 3, "amount_cents": 15})
        self.assertEqual(summary["missed_call.detected"], {"count": 1, "amount_cents": 10})


if __name__ == "__main__":
    unittest.main()

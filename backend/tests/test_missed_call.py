"""Tests for the TW-556 M2 missed-call layer (Zeke Okafor).

Money feature: Twilio voice webhooks -> call_logs -> missed-call detection ->
instant auto-SMS (business hours) or callback lead (after hours). PII doctrine
(TW-552): raw numbers bronze-only, masked in every API response.
"""
import base64
import hashlib
import hmac
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-auth-token")

AUTH_TOKEN = "test-auth-token"
ORG_ID = "org-m2-test"
SHOP_NUMBER = "+13035550142"
CALLER = "+13035550199"
CALL_SID = "CA" + "a" * 32


def _twilio_signature(url, params):
    """Reproduce Twilio's HMAC-SHA1 signature for forging valid test requests."""
    s = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(AUTH_TOKEN.encode(), s.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._rows = list(db._tables.get(table, []))
        self._pending_insert = None
        self._pending_update = None

    # -- reads --
    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def in_(self, col, values):
        self._rows = [r for r in self._rows if r.get(col) in values]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def single(self):
        return self

    def maybe_single(self):
        return self

    # -- writes --
    def insert(self, row):
        self._pending_insert = dict(row)
        return self

    def update(self, values):
        self._pending_update = dict(values)
        return self

    def execute(self):
        if self._pending_insert is not None:
            row = self._pending_insert
            row.setdefault("id", f"row-{len(self._db._tables[self._table])}")
            self._db._tables[self._table].append(row)
            self._pending_insert = None
            return FakeResult([row])
        if self._pending_update is not None:
            # apply update to rows matched by the accumulated eq filters
            # (FakeQuery.eq narrows self._rows to live row dicts)
            for r in self._rows:
                r.update(self._pending_update)
            self._pending_update = None
            return FakeResult(self._rows)
        return FakeResult(self._rows)


class FakeDB:
    def __init__(self):
        self._tables = {
            "organizations": [],
            "org_phone_numbers": [],
            "call_logs": [],
            "missed_call_recoveries": [],
            "leads": [],
            "customers": [],
            "toll_events": [],
            "appointments": [],
        }

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self, name)


def _seed_org(db):
    db._tables["organizations"].append({
        "id": ORG_ID, "name": "Test HVAC", "state": "CO",
        "plan": "trades_pro", "subscription_status": "active",
    })
    db._tables["org_phone_numbers"].append({
        "org_id": ORG_ID, "phone_number": SHOP_NUMBER,
    })


def _voice_params(**over):
    p = {
        "CallSid": CALL_SID,
        "From": CALLER,
        "To": SHOP_NUMBER,
        "Direction": "inbound",
        "CallStatus": "no-answer",
        "DialCallDuration": "0",
    }
    p.update(over)
    return p


class MissedCallTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.auth import get_auth, AuthContext

        self.db = FakeDB()
        _seed_org(self.db)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_auth] = lambda: AuthContext(
            user_id="user-1", org_id=ORG_ID, role="owner",
        )
        self.client = TestClient(app, raise_server_exceptions=False)
        self._overrides = (app, get_db, get_auth)

    def tearDown(self):
        app, get_db, get_auth = self._overrides
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth, None)

    def _post_voice(self, params, signed=True):
        url = "http://testserver/api/v1/calls/voice-webhook"
        headers = {}
        if signed:
            headers["X-Twilio-Signature"] = _twilio_signature(url, params)
        return self.client.post(url, data=params, headers=headers)

    # -- webhook security --
    def test_unsigned_webhook_rejected_401(self):
        resp = self._post_voice(_voice_params(), signed=False)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(self.db._tables["call_logs"]), 0)

    def test_unknown_to_number_404_not_403(self):
        params = _voice_params(To="+19998887777")
        url = "http://testserver/api/v1/calls/voice-webhook"
        sig = _twilio_signature(url, params)
        resp = self.client.post(url, data=params,
                                headers={"X-Twilio-Signature": sig})
        self.assertEqual(resp.status_code, 404)

    # -- call log + toll --
    def test_signed_webhook_creates_call_log_and_detection_toll(self):
        with mock.patch("app.services.call_service._in_business_hours",
                         return_value=True), \
             mock.patch("app.services.call_service._ten_dlc_configured",
                        return_value=True), \
             mock.patch("app.services.call_service._send_recovery_sms") as sms:
            resp = self._post_voice(_voice_params())
        self.assertEqual(resp.status_code, 200)
        logs = self.db._tables["call_logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(
            logs[0]["from_number_hash"],
            hashlib.sha256(CALLER.encode()).hexdigest(),
        )
        tolls = self.db._tables["toll_events"]
        types = [t["event_type"] for t in tolls]
        self.assertIn("missed_call.detected", types)
        self.assertIn("sms.auto_reply", types)
        sms.assert_called_once()

    def test_duplicate_webhook_delivery_is_idempotent(self):
        params = _voice_params()
        with mock.patch("app.services.call_service._in_business_hours",
                         return_value=True), \
             mock.patch("app.services.call_service._ten_dlc_configured",
                        return_value=True), \
             mock.patch("app.services.call_service._send_recovery_sms"):
            self._post_voice(params)
            resp = self._post_voice(params)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.db._tables["call_logs"]), 1)
        self.assertEqual(
            len([t for t in self.db._tables["toll_events"]
                 if t["event_type"] == "missed_call.detected"]), 1)

    # -- after hours --
    def test_after_hours_creates_callback_lead_no_sms(self):
        with mock.patch("app.services.call_service._in_business_hours",
                         return_value=False), \
             mock.patch("app.services.call_service._send_recovery_sms") as sms:
            resp = self._post_voice(_voice_params())
        self.assertEqual(resp.status_code, 200)
        sms.assert_not_called()
        leads = self.db._tables["leads"]
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["source"], "missed_call")
        self.assertIsNotNone(leads[0]["follow_up_at"])

    # -- money view: masking + tenant isolation --
    def test_missed_view_masks_numbers(self):
        self.db._tables["call_logs"].append({
            "id": "log-1", "org_id": ORG_ID, "twilio_call_sid": CALL_SID,
            "direction": "inbound", "from_number": CALLER,
            "from_number_hash": hashlib.sha256(CALLER.encode()).hexdigest(),
            "to_number": SHOP_NUMBER, "started_at": "2026-10-16T10:00:00Z",
            "status": "no-answer",
        })
        self.db._tables["missed_call_recoveries"].append({
            "id": "rec-1", "org_id": ORG_ID, "call_log_id": "log-1",
            "sms_sent_at": None,
        })
        resp = self.client.get("http://testserver/api/v1/calls/missed?days=7")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertNotIn(CALLER, resp.text)
        self.assertIn("0142", body[0]["from_number_masked"])

    def test_cross_org_missed_call_returns_404(self):
        self.db._tables["call_logs"].append({
            "id": "log-x", "org_id": "other-org", "twilio_call_sid": "CAzzz",
            "direction": "inbound", "from_number": CALLER,
            "from_number_hash": "h", "to_number": SHOP_NUMBER,
            "started_at": "2026-10-16T10:00:00Z", "status": "no-answer",
        })
        resp = self.client.post(
            "http://testserver/api/v1/calls/log-x/recover",
            json={"outcome": "called_back"},
        )
        self.assertEqual(resp.status_code, 404)

    # -- recovery --
    def test_recover_marks_recovered_and_links_appointment(self):
        self.db._tables["call_logs"].append({
            "id": "log-2", "org_id": ORG_ID, "twilio_call_sid": "CArec",
            "direction": "inbound", "from_number": CALLER,
            "from_number_hash": "h", "to_number": SHOP_NUMBER,
            "started_at": "2026-10-16T10:00:00Z", "status": "no-answer",
        })
        self.db._tables["missed_call_recoveries"].append({
            "id": "rec-2", "org_id": ORG_ID, "call_log_id": "log-2",
            "recovered_at": None,
        })
        resp = self.client.post(
            "http://testserver/api/v1/calls/log-2/recover",
            json={"outcome": "booked", "appointment_id": "appt-9",
                  "revenue": 400.00},
        )
        self.assertEqual(resp.status_code, 200)
        rec = self.db._tables["missed_call_recoveries"][0]
        self.assertIsNotNone(rec["recovered_at"])
        self.assertEqual(rec["resulting_appointment_id"], "appt-9")
        self.assertEqual(float(rec["revenue_attributed"]), 400.00)


if __name__ == "__main__":
    unittest.main()

"""TW-1322: signup -> trial -> first-job persistence proof (Dre).

Runs the REAL FastAPI app (app.main) end to end against a stateful
in-memory stand-in for the Supabase PostgREST client. Unlike the
MagicMock call-shape tests elsewhere in this suite, this fake keeps
rows: inserts persist, selects read them back, schema defaults from
supabase/schema.sql are applied on insert, and the schema's UNIQUE
constraints (organizations.clerk_org_id, stripe_events.stripe_event_id)
raise the same duplicate-key errors Postgres raises. That is what makes
this a persistence proof rather than a wiring check: every write is
verified by reading it back through the same routes the product uses.

Covered flow:
  1. Signup: POST /api/v1/organizations (Clerk user, onboarding).
  2. Trial: Stripe sends customer.subscription.created (status
     "trialing", the 14-day TW-550 trial) to POST /api/stripe/billing/
     webhook; sync lands plan/status/subscription id on the org row.
  3. First job: POST /api/v1/leads under the new org, read back via
     GET list + detail (tenant-isolated).

Recovery proofs (the LGCY go-live lesson, TW-1296 class):
  - An insert failure mid-signup leaves NO row behind, and the retry
    succeeds: no orphan, no half-created account locking the user out.
  - A failed trial sync marks the Stripe event "failed" (never
    swallowed), the org stays in its clean pre-trial state, and
    Stripe's redelivery of the same event completes the sync.
  - Duplicate webhook delivery is a no-op (status-gated idempotency).

Auth is stubbed at the JWT seam only (_verify_clerk_jwt); the real
get_user/get_auth dependencies run against the fake store, so org
resolution reads exactly the rows signup wrote.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import stripe
from fastapi.testclient import TestClient

USER_ID = "user_tw1322_signup"
AUTH_HEADERS = {"Authorization": f"Bearer {USER_ID}"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgrestLikeError(Exception):
    """Stands in for postgrest.exceptions.APIError / asyncpg errors."""


# Schema defaults transcribed from supabase/schema.sql (+ migrations).
_TABLE_DEFAULTS = {
    "organizations": {
        "plan": "basic",
        "subscription_status": "inactive",
        "city": "Denver",
        "state": "CO",
        "onboarding_complete": False,
        "industry": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
    },
    "leads": {
        "status": "new",
        "email": None,
        "phone": None,
        "source": None,
        "service_interest": None,
        "estimated_value": None,
        "address": None,
        "notes": None,
        "assigned_to": None,
        "follow_up_at": None,
        "contacted_at": None,
        "converted_at": None,
        "lost_reason": None,
    },
    "stripe_events": {
        "status": "failed",  # schema DEFAULT; writers set it explicitly
    },
}

_UNIQUE_COLUMNS = {
    "organizations": ("clerk_org_id",),
    "stripe_events": ("stripe_event_id",),
}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, op, payload=None):
        self._db = db
        self._table = table
        self._op = op
        self._payload = payload
        self._filters = []
        self._order = None
        self._range = None
        self._limit = None
        self._single = False
        self._maybe_single = False

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _matching(self):
        rows = list(self._db.tables.get(self._table, []))
        for kind, col, val in self._filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif kind == "lt":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) < val]
        return rows

    def execute(self):
        self._db._maybe_fail(self._table, self._op)
        if self._op == "insert":
            return _Result(self._do_insert())
        if self._op == "update":
            return _Result(self._do_update())
        return _Result(self._do_select())

    def _do_insert(self):
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        inserted = []
        for raw in rows:
            row = dict(_TABLE_DEFAULTS.get(self._table, {}))
            row.update(raw)
            row.setdefault("id", str(uuid.uuid4()))
            now = _now_iso()
            row.setdefault("created_at", now)
            row.setdefault("updated_at", now)
            if self._table == "stripe_events":
                row.setdefault("processed_at", now)
            for col in _UNIQUE_COLUMNS.get(self._table, ()):
                for existing in self._db.tables.get(self._table, []):
                    if existing.get(col) == row.get(col):
                        raise PostgrestLikeError(
                            f"duplicate key value violates unique constraint "
                            f'"{self._table}_{col}_key"'
                        )
            self._db.tables.setdefault(self._table, []).append(row)
            inserted.append(dict(row))
        return inserted

    def _do_update(self):
        updated = []
        for row in self._matching():
            row.update(self._payload)
            row["updated_at"] = _now_iso()
            updated.append(dict(row))
        return updated

    def _do_select(self):
        rows = [dict(r) for r in self._matching()]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._single:
            if len(rows) != 1:
                raise PostgrestLikeError(
                    "JSON object requested, multiple (or no) rows returned"
                )
            return rows[0]
        if self._maybe_single:
            if len(rows) > 1:
                raise PostgrestLikeError(
                    "JSON object requested, multiple rows returned"
                )
            return rows[0] if rows else None
        return rows


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


class StatefulFakeDb:
    """In-memory PostgREST stand-in with schema defaults, UNIQUE
    enforcement, and one-shot failure injection for recovery proofs."""

    def __init__(self):
        self.tables = {}
        self._failures = []  # list of (table, op) to fail once

    def table(self, name):
        return _Table(self, name)

    def fail_next(self, table, op):
        self._failures.append((table, op))

    def _maybe_fail(self, table, op):
        if (table, op) in self._failures:
            self._failures.remove((table, op))
            raise PostgrestLikeError("connection reset by peer (injected)")

    def rows(self, table):
        return self.tables.get(table, [])


@pytest.fixture()
def db():
    return StatefulFakeDb()


@pytest.fixture()
def client(db):
    from app.main import app

    def fake_get_db():
        return db

    def fake_verify(_token):
        return {"sub": USER_ID}

    def fake_event(payload, _sig):
        return stripe.Event.construct_from(json.loads(payload), "sk_test_dummy")

    patches = [
        patch("app.auth._verify_clerk_jwt", side_effect=fake_verify),
        patch("app.auth.get_clerk_user_email", return_value=None),
        patch("app.auth.get_db", side_effect=fake_get_db),
        patch("app.routers.organizations.get_db", side_effect=fake_get_db),
        patch("app.routers.leads.get_db", side_effect=fake_get_db),
        patch("app.routers.stripe_webhooks.get_db", side_effect=fake_get_db),
        patch("app.routers.stripe_webhooks.handle_webhook", side_effect=fake_event),
    ]
    for p in patches:
        p.start()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        for p in patches:
            p.stop()


def _signup_row(db):
    """Insert an org row directly (service-level tests skip HTTP)."""
    result = (
        db.table("organizations")
        .insert(
            {
                "clerk_user_id": USER_ID,
                "clerk_org_id": USER_ID,
                "name": "Mile High HVAC",
                "industry": "hvac",
                "city": "Denver",
                "state": "CO",
            }
        )
        .execute()
    )
    return result.data[0]


def _signup(client):
    return client.post(
        "/api/v1/organizations",
        headers=AUTH_HEADERS,
        json={
            "name": "Mile High HVAC",
            "industry": "hvac",
            "city": "Denver",
            "state": "CO",
        },
    )


def _trial_event(org_id, event_id="evt_tw1322_trial"):
    return {
        "id": event_id,
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_tw1322",
                "status": "trialing",
                "metadata": {"lbt_org_id": org_id, "plan": "trades_starter"},
            }
        },
    }


def _post_webhook(client, event):
    return client.post(
        "/api/stripe/billing/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test-sig"},
    )


# ---------------------------------------------------------------------
# 1. Happy path: signup -> trial -> first job, every write read back.
# ---------------------------------------------------------------------


def test_signup_persists_org_with_schema_defaults(client, db):
    resp = _signup(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["clerk_user_id"] == USER_ID
    assert body["plan"] == "basic"  # schema default, pre-trial
    assert body["subscription_status"] == "inactive"  # schema default

    stored = db.rows("organizations")
    assert len(stored) == 1
    assert stored[0]["id"] == body["id"]
    assert stored[0]["name"] == "Mile High HVAC"


def test_signup_is_idempotent_no_duplicate_org(client, db):
    first = _signup(client)
    second = _signup(client)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(db.rows("organizations")) == 1


def test_trial_webhook_syncs_org_and_product_reads_it_back(client, db):
    org = _signup(client).json()

    resp = _post_webhook(client, _trial_event(org["id"]))
    assert resp.status_code == 200, resp.text

    stored = db.rows("organizations")[0]
    assert stored["plan"] == "trades_starter"
    assert stored["subscription_status"] == "active"  # trialing -> active
    assert stored["stripe_subscription_id"] == "sub_tw1322"

    # Read back through the product's own route + auth resolution.
    me = client.get("/api/v1/organizations/me", headers=AUTH_HEADERS)
    assert me.status_code == 200, me.text
    assert me.json()["plan"] == "trades_starter"
    assert me.json()["subscription_status"] == "active"


def test_first_lead_persists_under_org_and_reads_back(client, db):
    org = _signup(client).json()
    _post_webhook(client, _trial_event(org["id"]))

    created = client.post(
        "/api/v1/leads",
        headers=AUTH_HEADERS,
        json={"name": "First Job — Furnace Tune-Up", "source": "google"},
    )
    assert created.status_code == 201, created.text
    lead = created.json()
    assert lead["org_id"] == org["id"]
    assert lead["status"] == "new"

    stored = db.rows("leads")
    assert len(stored) == 1
    assert stored[0]["id"] == lead["id"]

    listed = client.get("/api/v1/leads", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [lead["id"]]

    detail = client.get(f"/api/v1/leads/{lead['id']}", headers=AUTH_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["name"] == "First Job — Furnace Tune-Up"


def test_pre_trial_org_is_usable_not_locked_out(client, db):
    """An org that signed up but has not started a trial yet can still
    sign in and work — the pre-trial state is a state, not a lockout."""
    _signup(client)
    listed = client.get("/api/v1/leads", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert listed.json() == []


def test_sync_subscription_accepts_real_stripe_object(db):
    """TW-1322 defect regression: sync_subscription must work on the
    object stripe-python actually hands it. stripe >= 11 StripeObjects
    dropped dict-style .get(); production passes construct_event /
    Subscription.retrieve results, never plain dicts."""
    from app.services import stripe_service

    org = _signup_row(db)
    sub = stripe.Subscription.construct_from(
        {
            "id": "sub_tw1322",
            "status": "trialing",
            "metadata": {"lbt_org_id": org["id"], "plan": "trades_starter"},
        },
        "sk_test_dummy",
    )
    stripe_service.sync_subscription(db, sub)
    stored = db.rows("organizations")[0]
    assert stored["plan"] == "trades_starter"
    assert stored["subscription_status"] == "active"
    assert stored["stripe_subscription_id"] == "sub_tw1322"


def test_subscription_deleted_event_resets_org(client, db):
    org = _signup(client).json()
    _post_webhook(client, _trial_event(org["id"]))
    assert db.rows("organizations")[0]["plan"] == "trades_starter"

    deleted = {
        "id": "evt_tw1322_deleted",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_tw1322",
                "status": "canceled",
                "metadata": {"lbt_org_id": org["id"], "plan": "trades_starter"},
            }
        },
    }
    resp = _post_webhook(client, deleted)
    assert resp.status_code == 200, resp.text
    stored = db.rows("organizations")[0]
    assert stored["plan"] == "basic"
    assert stored["subscription_status"] == "inactive"


def test_checkout_completed_event_syncs_via_retrieved_subscription(client, db):
    org = _signup(client).json()
    retrieved = stripe.Subscription.construct_from(
        {
            "id": "sub_tw1322",
            "status": "trialing",
            "metadata": {"lbt_org_id": org["id"], "plan": "trades_pro"},
        },
        "sk_test_dummy",
    )
    event = {
        "id": "evt_tw1322_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_tw1322", "subscription": "sub_tw1322"}},
    }
    with patch(
        "app.routers.stripe_webhooks.stripe.Subscription.retrieve",
        return_value=retrieved,
    ):
        resp = _post_webhook(client, event)
    assert resp.status_code == 200, resp.text
    stored = db.rows("organizations")[0]
    assert stored["plan"] == "trades_pro"
    assert stored["subscription_status"] == "active"


# ---------------------------------------------------------------------
# 2. Recovery: failures mid-flow leave no orphans and never lock out.
# ---------------------------------------------------------------------


def test_signup_insert_failure_leaves_no_orphan_and_retry_succeeds(client, db):
    db.fail_next("organizations", "insert")
    failed = _signup(client)
    assert failed.status_code == 500
    assert db.rows("organizations") == []  # nothing half-created

    retry = _signup(client)
    assert retry.status_code == 201, retry.text
    assert len(db.rows("organizations")) == 1


def test_duplicate_trial_webhook_delivery_is_idempotent(client, db):
    org = _signup(client).json()
    event = _trial_event(org["id"])

    first = _post_webhook(client, event)
    second = _post_webhook(client, event)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("duplicate") is True

    assert len(db.rows("stripe_events")) == 1
    assert db.rows("stripe_events")[0]["status"] == "processed"
    assert len(db.rows("organizations")) == 1
    assert db.rows("organizations")[0]["plan"] == "trades_starter"


def test_failed_trial_sync_marks_failed_and_retry_recovers(client, db):
    org = _signup(client).json()
    event = _trial_event(org["id"])

    # The org update inside sync_subscription fails mid-dispatch.
    db.fail_next("organizations", "update")
    failed = _post_webhook(client, event)
    assert failed.status_code == 500  # Stripe will retry

    # No half state: org is still cleanly pre-trial, event marked failed.
    stored = db.rows("organizations")[0]
    assert stored["plan"] == "basic"
    assert stored["subscription_status"] == "inactive"
    assert stored["stripe_subscription_id"] is None
    assert db.rows("stripe_events")[0]["status"] == "failed"

    # Stripe redelivers the SAME event: the failed claim is taken over
    # and the sync completes. The user was never locked out.
    retry = _post_webhook(client, event)
    assert retry.status_code == 200, retry.text
    stored = db.rows("organizations")[0]
    assert stored["plan"] == "trades_starter"
    assert stored["subscription_status"] == "active"
    assert db.rows("stripe_events")[0]["status"] == "processed"
    assert len(db.rows("stripe_events")) == 1

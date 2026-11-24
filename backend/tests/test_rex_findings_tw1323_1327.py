"""TW-1323..TW-1327 — Rex's TW-1322 adversarial findings, as tests.

Each test is shaped like Rex's probe (run-2-tw1322/FINDINGS.md) and
encodes the FIXED behavior; all were RED against the pre-fix code.

  TW-1323 F1: duplicate organizations wedge a Clerk user forever.
  TW-1324 F2: Twistor Identity signup is non-atomic; one transient
              failure wedges the account permanently.
  TW-1325 F3: magic-link signup creates an identity that can never
              reach a workspace and burns the email.
  TW-1326 F4: visitor-events attribution forgeable via the
              unauthenticated x-lbt-user-id header.
  TW-1327 F5: error semantics — duplicate race 500 (want 409),
              magic-link purpose never enforced, validation 401
              (want 400).

The fake DB is PostgREST-faithful: UNIQUE constraints exactly where
the (post-TW-1323) schema defines them, maybe_single raising PGRST116
on multiple rows, one-shot failure injection for the recovery probes.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("CLERK_PUBLISHABLE_KEY", "pk_test_fake")
os.environ.setdefault("CLERK_WEBHOOK_SECRET", "whsec_fake")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_fake")
os.environ.setdefault("STRIPE_PRICE_TRADES_STARTER", "price_starter")
os.environ.setdefault("STRIPE_PRICE_TRADES_PRO", "price_pro")
os.environ.setdefault("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", "true")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

from app import auth as auth_module  # noqa: E402
from app import main as main_module  # noqa: E402
from app.database import get_db  # noqa: E402
from app.identity import router as identity_router_module  # noqa: E402
from app.identity import service as identity_service  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.routers import organizations as organizations_router  # noqa: E402
from app.routers import visitor_events as visitor_router  # noqa: E402
from app.services import demo_data  # noqa: E402


def _api_error(code: str, message: str) -> APIError:
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


# ---------------------------------------------------------------------------
# PostgREST-faithful fake
# ---------------------------------------------------------------------------

ORG_DEFAULTS = {
    "plan": "basic",
    "subscription_status": "inactive",
    "billing_interval": "monthly",
    "max_users": 3,
    "onboarding_complete": False,
    "timezone": "America/Denver",
    "currency": "USD",
}

# UNIQUE constraints as the schema defines them after the TW-1323
# migration: clerk_org_id (schema.sql) and clerk_user_id (partial,
# non-null — migration_org_clerk_user_unique_tw1323.sql).
UNIQUE_COLUMNS = {
    "organizations": ("clerk_org_id", "clerk_user_id"),
    "idn_users": ("email",),
}


class FakePostgrest:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.fail_next_insert: dict[str, Exception] = {}
        self.force_insert_conflict: set[str] = set()
        self._seq = 0

    def table(self, name):
        return _Query(self, name)

    def rows(self, name):
        return self.tables.setdefault(name, [])

    def next_ts(self):
        self._seq += 1
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return (base + timedelta(seconds=self._seq)).isoformat()


class _Query:
    def __init__(self, db: FakePostgrest, table: str):
        self.db = db
        self.table = table
        self.op = "select"
        self.cols = "*"
        self.filters: list = []
        self.payload = None
        self.limit_n = None
        self.order_col = None
        self.want_single = False
        self.want_maybe_single = False

    # -- builders ------------------------------------------------------
    def select(self, cols="*"):
        self.op = "select"
        self.cols = cols
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, None if val == "null" else val))
        return self

    def order(self, col, desc=False):
        self.order_col = (col, desc)
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def single(self):
        self.want_single = True
        return self

    def maybe_single(self):
        self.want_maybe_single = True
        return self

    # -- execution -----------------------------------------------------
    def _matches(self, row):
        for kind, col, val in self.filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "is" and row.get(col) is not val:
                return False
        return True

    def _filtered(self):
        rows = [r for r in self.db.rows(self.table) if self._matches(r)]
        if self.order_col:
            col, desc = self.order_col
            rows = sorted(rows, key=lambda r: r.get(col) or "", reverse=desc)
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return rows

    def execute(self):
        if self.op == "insert":
            return self._do_insert()
        if self.op == "update":
            rows = self._filtered()
            for r in rows:
                r.update(self.payload)
            return _Result([dict(r) for r in rows])
        if self.op == "delete":
            kept, gone = [], []
            for r in self.db.rows(self.table):
                (gone if self._matches(r) else kept).append(r)
            self.db.tables[self.table] = kept
            return _Result([dict(r) for r in gone])
        rows = [dict(r) for r in self._filtered()]
        if self.table == "idn_magic_links" and "idn_users" in str(self.cols):
            for r in rows:
                user = next(
                    (u for u in self.db.rows("idn_users") if u["id"] == r["user_id"]),
                    None,
                )
                r["idn_users"] = dict(user) if user else None
        if self.want_single:
            if len(rows) != 1:
                raise _api_error("PGRST116", "JSON object requested, multiple "
                                 "(or no) rows returned")
            return _Result(rows[0])
        if self.want_maybe_single:
            if len(rows) > 1:
                raise _api_error("PGRST116", "JSON object requested, multiple "
                                 "rows returned")
            return _Result(rows[0] if rows else None)
        return _Result(rows)

    def _do_insert(self):
        if self.table in self.db.fail_next_insert:
            raise self.db.fail_next_insert.pop(self.table)
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        inserted = []
        for item in items:
            row = dict(item)
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", self.db.next_ts())
            if self.table == "organizations":
                for k, v in ORG_DEFAULTS.items():
                    row.setdefault(k, v)
            if self.table == "idn_sessions":
                row.setdefault("family_id", str(uuid.uuid4()))
                row.setdefault("revoked_at", None)
            if self.table in self.db.force_insert_conflict:
                raise _api_error("23505", "duplicate key value violates "
                                 "unique constraint")
            for col in UNIQUE_COLUMNS.get(self.table, ()):
                val = row.get(col)
                if val is None:
                    continue
                probe = val.lower() if self.table == "idn_users" else val
                for existing in self.db.rows(self.table):
                    other = existing.get(col)
                    if other is None:
                        continue
                    if self.table == "idn_users":
                        other = other.lower()
                    if other == probe:
                        raise _api_error(
                            "23505",
                            f'duplicate key value violates unique '
                            f'constraint "{self.table}_{col}_key"',
                        )
            self.db.rows(self.table).append(row)
            inserted.append(dict(row))
        return _Result(inserted)


class _Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data) if isinstance(data, list) else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER = {"user_id": "user_tw1323", "email": "owner@tw1323.example"}


@pytest.fixture()
def fake():
    return FakePostgrest()


@pytest.fixture()
def client(fake, monkeypatch):
    def fake_verify(token):
        if token == "good-token":
            return {"sub": USER["user_id"], "email": USER["email"]}
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(auth_module, "_verify_clerk_jwt", fake_verify)
    main_module.app.dependency_overrides[get_db] = lambda: fake
    # get_auth and the organizations/visitor handlers call their
    # module-level get_db() directly (not via Depends), so the
    # dependency override alone never reaches them.
    monkeypatch.setattr(auth_module, "get_db", lambda: fake)
    monkeypatch.setattr(organizations_router, "get_db", lambda: fake)
    monkeypatch.setattr(visitor_router, "get_db", lambda: fake)
    with TestClient(main_module.app, raise_server_exceptions=False) as c:
        yield c
    main_module.app.dependency_overrides.pop(get_db, None)


AUTH = {"Authorization": "Bearer good-token"}


@pytest.fixture()
def identity_client(fake):
    limiter.enabled = False
    mini = FastAPI()
    mini.include_router(identity_router_module.router, prefix="/api/v1")
    mini.dependency_overrides[get_db] = lambda: fake
    with TestClient(mini, raise_server_exceptions=False) as c:
        yield c
    limiter.enabled = True


# ---------------------------------------------------------------------------
# TW-1323 / F1 — duplicate organizations wedge
# ---------------------------------------------------------------------------


def test_f1_migration_file_present_with_partial_unique_and_dup_guard():
    path = (
        Path(__file__).resolve().parents[2]
        / "supabase"
        / "migration_org_clerk_user_unique_tw1323.sql"
    )
    assert path.exists(), "TW-1323 migration missing"
    sql = path.read_text()
    assert "CREATE UNIQUE INDEX" in sql
    assert "WHERE clerk_user_id IS NOT NULL" in sql
    # Refuses to apply over existing duplicates instead of guessing.
    assert "RAISE EXCEPTION" in sql


def test_f1_raced_second_insert_returns_existing_org(client, fake, monkeypatch):
    r = client.post("/api/v1/organizations", json={"name": "First"}, headers=AUTH)
    assert r.status_code == 201
    org_id = r.json()["id"]

    real_check = organizations_router._get_org_for_user
    calls = {"n": 0}

    def racing_check(db, user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the check raced ahead of the first commit
        return real_check(db, user_id)

    monkeypatch.setattr(organizations_router, "_get_org_for_user", racing_check)
    r = client.post("/api/v1/organizations", json={"name": "Second"}, headers=AUTH)
    assert r.status_code == 201, r.text
    assert r.json()["id"] == org_id
    assert len(fake.rows("organizations")) == 1


def _seed_duplicate_orgs(fake):
    """Legacy pre-migration data: two rows, one clerk_user_id. Seeded
    by direct store writes because the post-TW-1323 schema (correctly)
    refuses to create this state through inserts."""
    rows = fake.rows("organizations")
    for suffix in ("first", "second"):
        row = {
            "id": f"org-{suffix}",
            "clerk_user_id": USER["user_id"],
            "clerk_org_id": f"org_{suffix}",
            "name": "Dup Shop",
            "created_at": fake.next_ts(),
        }
        row.update(ORG_DEFAULTS)
        rows.append(row)
    assert len(rows) == 2
    return rows[0]["id"]  # oldest by created_at


def test_f1_get_auth_survives_preexisting_duplicate_rows(client, fake):
    oldest_id = _seed_duplicate_orgs(fake)
    r = client.get("/api/v1/organizations/me", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == oldest_id


def test_f1_bootstrap_demo_org_survives_duplicate_rows(fake):
    oldest_id = _seed_duplicate_orgs(fake)
    result = demo_data.bootstrap_demo_org(
        fake,
        user_id=USER["user_id"],
        name="Demo HVAC",
        industry="HVAC",
    )
    assert result["organization"]["id"] == oldest_id


# ---------------------------------------------------------------------------
# TW-1324 / F2 — Identity signup atomicity
# ---------------------------------------------------------------------------

PASSWORD = "correct horse battery staple"


def _wedged_user(fake, email="wedged@example.com", with_password=True):
    """The probe-D state: a user row, zero orgs, zero memberships."""
    from app.identity import passwords

    row = {"email": email}
    if with_password:
        row["password_hash"] = passwords.hash_password(PASSWORD)
    return fake.table("idn_users").insert(row).execute().data[0]


def test_f2_org_failure_during_signup_leaves_no_wedged_user(fake):
    fake.fail_next_insert["organizations"] = _api_error(
        "PGRST000", "transient connection failure"
    )
    with pytest.raises(Exception):
        identity_service.signup(fake, "new@example.com", PASSWORD, "New Shop")
    # Compensation: the half-created user row is gone, retry is clean.
    assert fake.rows("idn_users") == []
    result = identity_service.signup(fake, "new@example.com", PASSWORD, "New Shop")
    assert result["org_id"]
    session = identity_service.login(fake, "new@example.com", PASSWORD)
    assert len(session["orgs"]) == 1


def test_f2_signup_resumes_wedged_account_with_matching_password(fake):
    _wedged_user(fake)
    result = identity_service.signup(fake, "wedged@example.com", PASSWORD, "Shop")
    assert result["org_id"]
    session = identity_service.login(fake, "wedged@example.com", PASSWORD)
    assert len(session["orgs"]) == 1


def test_f2_signup_wedged_account_wrong_password_still_rejected(fake):
    _wedged_user(fake)
    with pytest.raises(Exception):
        identity_service.signup(
            fake, "wedged@example.com", "a different password entirely", "Shop"
        )
    assert fake.rows("organizations") == []


def test_f2_login_repairs_wedged_account(fake):
    _wedged_user(fake)
    session = identity_service.login(fake, "wedged@example.com", PASSWORD)
    assert len(session["orgs"]) == 1
    assert session["orgs"][0]["role"] == "owner"


# ---------------------------------------------------------------------------
# TW-1325 / F3 — magic-link dead end
# ---------------------------------------------------------------------------


def test_f3_magic_link_signup_provisions_a_workspace(fake):
    raw = identity_service.issue_magic_link(fake, "fresh@example.com")
    session = identity_service.consume_magic_link(fake, raw)
    assert session["access_token"]
    assert len(session["orgs"]) == 1
    user_id = session["user"]["id"]
    memberships = [
        m for m in fake.rows("idn_org_memberships") if m["user_id"] == user_id
    ]
    assert len(memberships) == 1 and memberships[0]["role"] == "owner"


def test_f3_magic_link_email_not_burned_for_password_signup(fake):
    raw = identity_service.issue_magic_link(fake, "later@example.com")
    identity_service.consume_magic_link(fake, raw)
    # The same person can still set a password on the account.
    identity_service.signup(fake, "later@example.com", PASSWORD)
    session = identity_service.login(fake, "later@example.com", PASSWORD)
    assert len(session["orgs"]) == 1


# ---------------------------------------------------------------------------
# TW-1326 / F4 — visitor-events attribution forgery
# ---------------------------------------------------------------------------


def _visitor_body():
    return {
        "event_type": "page_view",
        "visitor_id": "visitor_1234",
        "session_id": "session_1234",
        "path": "/pricing",
    }


def test_f4_forged_user_header_is_not_persisted(client, fake):
    r = client.post(
        "/api/v1/visitor-events",
        json=_visitor_body(),
        headers={"x-lbt-user-id": "user_victim123"},
    )
    assert r.status_code == 202, r.text
    rows = fake.rows("visitor_events")
    assert len(rows) == 1
    assert rows[0]["clerk_user_id"] is None


def test_f4_verified_bearer_is_attributed(client, fake):
    r = client.post(
        "/api/v1/visitor-events",
        json=_visitor_body(),
        headers=AUTH,
    )
    assert r.status_code == 202, r.text
    rows = fake.rows("visitor_events")
    assert len(rows) == 1
    assert rows[0]["clerk_user_id"] == USER["user_id"]


# ---------------------------------------------------------------------------
# TW-1327 / F5 — error semantics
# ---------------------------------------------------------------------------


def test_f5_duplicate_signup_race_returns_409(identity_client, fake):
    fake.force_insert_conflict.add("idn_users")
    r = identity_client.post(
        "/api/v1/identity/signup",
        json={"email": "race@example.com", "password": PASSWORD},
    )
    assert r.status_code == 409, r.text


def test_f5_fully_registered_duplicate_returns_409(identity_client, fake):
    body = {"email": "dupe@example.com", "password": PASSWORD}
    r = identity_client.post("/api/v1/identity/signup", json=body)
    assert r.status_code == 201
    r = identity_client.post("/api/v1/identity/signup", json=body)
    assert r.status_code == 409, r.text


def test_f5_short_password_returns_400_not_401(identity_client, fake):
    r = identity_client.post(
        "/api/v1/identity/signup",
        json={"email": "short@example.com", "password": "short"},
    )
    assert r.status_code == 400, r.text


def test_f5_verify_purpose_token_mints_no_session(fake):
    identity_service.signup(fake, "verifyme@example.com", PASSWORD)
    raw = identity_service.issue_magic_link(
        fake, "verifyme@example.com", purpose="verify"
    )
    result = identity_service.consume_magic_link(fake, raw)
    assert "access_token" not in result
    assert "refresh_token" not in result
    assert result.get("verified") is True
    user = fake.rows("idn_users")[0]
    assert user.get("email_verified_at")


def test_f5_login_purpose_token_still_mints_session(fake):
    identity_service.signup(fake, "loginme@example.com", PASSWORD)
    raw = identity_service.issue_magic_link(
        fake, "loginme@example.com", purpose="login"
    )
    result = identity_service.consume_magic_link(fake, raw)
    assert result["access_token"]

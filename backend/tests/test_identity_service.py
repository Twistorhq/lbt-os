"""TW-619 service tests with an in-memory supabase-py-compatible fake."""

import uuid
from datetime import datetime, timezone

import pytest

from app.identity import service, tokens


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, store, name):
        self._store = store
        self._name = name
        self._filters = []
        self._limit = None
        self._select_cols = "*"
        self._pending_insert = None
        self._pending_patch = None

    def select(self, cols="*"):
        self._select_cols = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def is_(self, col, val):
        # only used as is_("revoked_at", "null")
        self._filters.append((col, None))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _rows(self):
        rows = list(self._store[self._name].values())
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def execute(self):
        if getattr(self, "_pending_insert", None) is not None:
            rows = self._do_insert()
            self._pending_insert = None
            return _Result(rows)
        if getattr(self, "_pending_patch", None) is not None:
            rows = self._do_update()
            self._pending_patch = None
            return _Result(rows)
        rows = self._rows()
        # magic-link join emulation
        if self._name == "idn_magic_links" and "idn_users" in self._select_cols:
            out = []
            for r in rows:
                r2 = dict(r)
                r2["idn_users"] = self._store["idn_users"][r["user_id"]]
                out.append(r2)
            rows = out
        return _Result(rows)

    def insert(self, row):
        self._pending_insert = dict(row)
        return self

    def update(self, patch):
        self._pending_patch = dict(patch)
        return self

    def _do_insert(self):
        row = self._pending_insert
        row.setdefault("id", uuid.uuid4().hex)
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if self._name == "idn_sessions":
            row.setdefault("family_id", uuid.uuid4().hex)
        self._store[self._name][row["id"]] = row
        return [row]

    def _do_update(self):
        rows = self._rows()
        for r in rows:
            r.update(self._pending_patch)
        return rows


class FakeDB:
    def __init__(self):
        self._store = {
            t: {}
            for t in (
                "idn_users",
                "idn_org_memberships",
                "idn_sessions",
                "idn_magic_links",
                "idn_oauth_accounts",
                "idn_sso_configs",
                "organizations",
            )
        }

    def table(self, name):
        return _Table(self._store, name)


@pytest.fixture
def db():
    return FakeDB()


def _signup(db, email="a@x.co", pw="s3cret!!"):
    return service.signup(db, email, pw, org_name="Acme")


def test_signup_creates_owner_membership(db):
    out = _signup(db)
    assert out["user"]["email"] == "a@x.co"
    mem = db.table("idn_org_memberships").select("*").execute().data
    assert len(mem) == 1 and mem[0]["role"] == "owner"


def test_signup_duplicate_email_rejected(db):
    _signup(db)
    with pytest.raises(tokens.TokenError):
        _signup(db)


def test_login_ok_and_bad_password(db):
    _signup(db)
    sess = service.login(db, "a@x.co", "s3cret!!")
    assert "access_token" in sess and "refresh_token" in sess
    assert sess["orgs"][0]["role"] == "owner"
    with pytest.raises(tokens.TokenError):
        service.login(db, "a@x.co", "nope")


def test_refresh_rotates_and_old_token_dies(db):
    _signup(db)
    s1 = service.login(db, "a@x.co", "s3cret!!")
    s2 = service.refresh(db, s1["refresh_token"])
    assert s2["refresh_token"] != s1["refresh_token"]
    # old token was rotated -> presenting it again triggers reuse detection
    with pytest.raises(tokens.TokenError):
        service.refresh(db, s1["refresh_token"])
    # whole family revoked: even the new token is dead
    with pytest.raises(tokens.TokenError):
        service.refresh(db, s2["refresh_token"])


def test_logout_kills_family(db):
    _signup(db)
    s1 = service.login(db, "a@x.co", "s3cret!!")
    service.logout(db, s1["refresh_token"])
    with pytest.raises(tokens.TokenError):
        service.refresh(db, s1["refresh_token"])


def test_magic_link_consume_marks_used_and_verifies(db):
    _signup(db)
    raw = service.issue_magic_link(db, "a@x.co")
    sess = service.consume_magic_link(db, raw)
    assert sess["user"]["email_verified"] is True
    with pytest.raises(tokens.TokenError):
        service.consume_magic_link(db, raw)  # single use


def test_magic_link_provisions_passwordless_user(db):
    raw = service.issue_magic_link(db, "new@x.co")
    sess = service.consume_magic_link(db, raw)
    assert sess["user"]["email"] == "new@x.co"


def test_switch_org_rejects_nonmember(db):
    _signup(db)
    s1 = service.login(db, "a@x.co", "s3cret!!")
    uid = s1["user"]["id"]
    with pytest.raises(tokens.TokenError):
        service.switch_org(db, uid, "00000000-0000-0000-0000-000000000000")


def test_switch_org_ok(db):
    _signup(db)
    s1 = service.login(db, "a@x.co", "s3cret!!")
    uid, org_id = s1["user"]["id"], s1["orgs"][0]["org_id"]
    out = service.switch_org(db, uid, org_id)
    claims = tokens.verify_access_token(service.get_keypair(), out["access_token"])
    assert claims["org"] == org_id and claims["role"] == "owner"

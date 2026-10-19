"""TW-619 (Zeke) — Google OAuth flow tests (TDD)."""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.identity import oauth
from app.identity import service as identity_service

# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class _R:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []
        self._limit = None
        self._mode = "select"
        self._row = None

    def select(self, cols="*"):
        self._mode = "select"
        return self

    def insert(self, row):
        self._mode = "insert"
        self._row = row
        return self

    def update(self, row):
        self._mode = "update"
        self._row = row
        return self

    def eq(self, k, v):
        self._filters.append((k, v))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        store = self._db.tables[self._table]
        if self._mode == "select":
            rows = [r for r in store
                    if all(r.get(k) == v for k, v in self._filters)]
            if self._limit is not None:
                rows = rows[: self._limit]
            return _R([dict(r) for r in rows])
        if self._mode == "insert":
            row = dict(self._row)
            row.setdefault("id", f"{self._table}-{len(store) + 1}")
            store.append(row)
            return _R([dict(row)])
        rows = []
        for r in store:
            if all(r.get(k) == v for k, v in self._filters):
                r.update(self._row)
                rows.append(dict(r))
        return _R(rows)


class FakeDB:
    def __init__(self):
        self.tables = {
            "idn_users": [],
            "idn_oauth_accounts": [],
            "idn_sso_configs": [],
            "idn_org_memberships": [],
        }

    def table(self, name):
        return _Q(self, name)


class FakeOAuthProvider(oauth.OAuthProvider):
    """Test provider — stands in for Google without any network."""
    name = "fake"
    seen = {}

    def authorization_url(self, *, state, code_challenge, redirect_uri):
        FakeOAuthProvider.seen["challenge"] = code_challenge
        FakeOAuthProvider.seen["redirect_uri"] = redirect_uri
        return (f"https://provider.test/auth?client_id=x&state={state}"
                f"&code_challenge={code_challenge}")

    #: Flip in tests to simulate a provider that did NOT verify email.
    email_verified = True

    def fetch_profile(self, *, code, code_verifier, redirect_uri):
        assert code == "authcode123"
        # The verifier must be the one minted at /start time (from state).
        assert code_verifier == FakeOAuthProvider.seen["verifier"]
        return {"email": "Prof@Test.edu", "provider_user_id": "gp-1",
                "email_verified": type(self).email_verified,
                "name": "Test Prof"}


class UnverifiedFakeOAuthProvider(FakeOAuthProvider):
    """Provider twin whose profile email is NOT verified (B5)."""
    name = "fake-unverified"
    email_verified = False


@pytest.fixture
def fakedb():
    return FakeDB()


@pytest.fixture
def client(fakedb):
    oauth.register_provider(FakeOAuthProvider())
    oauth.register_provider(UnverifiedFakeOAuthProvider())
    app = FastAPI()
    app.include_router(oauth.oauth_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: fakedb
    try:
        yield TestClient(app, raise_server_exceptions=False), fakedb
    finally:
        oauth.PROVIDERS.pop("fake", None)
        oauth.PROVIDERS.pop("fake-unverified", None)


# --------------------------------------------------------------------------
# State signing
# --------------------------------------------------------------------------

def test_state_sign_verify_roundtrip():
    token = oauth._sign_state({"v": "verifier-1", "n": "nonce-1"})
    payload = oauth._verify_state(token)
    assert payload["v"] == "verifier-1"


def test_state_tampered_rejected():
    token = oauth._sign_state({"v": "verifier-1"})
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(oauth.StateError):
        oauth._verify_state(bad)


def test_state_expired_rejected(monkeypatch):
    monkeypatch.setattr(oauth, "STATE_TTL_SECONDS", -1)
    token = oauth._sign_state({"v": "verifier-1"})
    with pytest.raises(oauth.StateError):
        oauth._verify_state(token)


# --------------------------------------------------------------------------
# Provider registry + Google URL
# --------------------------------------------------------------------------

def test_unknown_provider_404(client):
    tc, _ = client
    r = tc.get("/api/v1/identity/oauth/github/start")
    assert r.status_code == 404


def test_google_authorization_url_has_pkce(monkeypatch):
    monkeypatch.setenv("TWISTOR_IDENTITY_GOOGLE_CLIENT_ID", "cid123")
    url = oauth.GoogleProvider().authorization_url(
        state="st8", code_challenge="chal9",
        redirect_uri="https://api.test/cb")
    assert "client_id=cid123" in url
    assert "code_challenge=chal9" in url
    assert "code_challenge_method=S256" in url
    assert "state=st8" in url
    assert "accounts.google.com" in url


def test_google_start_503_without_client_id(monkeypatch, client):
    tc, _ = client
    monkeypatch.delenv("TWISTOR_IDENTITY_GOOGLE_CLIENT_ID", raising=False)
    r = tc.get("/api/v1/identity/oauth/google/start")
    assert r.status_code == 503


def test_start_returns_authorization_url(client):
    tc, _ = client
    r = tc.get("/api/v1/identity/oauth/fake/start")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_url"].startswith("https://provider.test/auth?")
    assert "state=" in body["authorization_url"]
    # The signed state must decode to a verifier the provider saw at /start.
    state = parse_qs(urlparse(body["authorization_url"]).query)["state"][0]
    FakeOAuthProvider.seen["verifier"] = oauth._verify_state(state)["v"]
    assert FakeOAuthProvider.seen["challenge"]


# --------------------------------------------------------------------------
# Callback: find-or-create + session
# --------------------------------------------------------------------------

def _mint_session_capture(monkeypatch, fakedb):
    calls = []
    monkeypatch.setattr(
        identity_service, "_open_session",
        lambda db, user, ip=None, user_agent=None: calls.append(
            {"user": user, "ip": ip}) or {"minted": True})
    return calls


def _do_callback(tc, monkeypatch):
    r = tc.get("/api/v1/identity/oauth/fake/start")
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    FakeOAuthProvider.seen["verifier"] = oauth._verify_state(state)["v"]
    return tc.get("/api/v1/identity/oauth/fake/callback",
                  params={"code": "authcode123", "state": state})


def test_callback_provisions_user_links_account_mints_session(
        client, monkeypatch):
    tc, fakedb = client
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_callback(tc, monkeypatch)
    assert r.status_code == 200, r.text
    assert r.json() == {"minted": True}
    assert len(calls) == 1
    # Email normalized lowercase; Google-verified email marks verified.
    assert calls[0]["user"]["email"] == "prof@test.edu"
    users = fakedb.tables["idn_users"]
    assert len(users) == 1 and users[0]["email_verified_at"]
    accounts = fakedb.tables["idn_oauth_accounts"]
    assert len(accounts) == 1
    assert accounts[0]["provider"] == "fake"
    assert accounts[0]["provider_user_id"] == "gp-1"
    assert accounts[0]["user_id"] == users[0]["id"]


def test_callback_links_existing_user_by_email(client, monkeypatch):
    tc, fakedb = client
    existing = fakedb.table("idn_users").insert(
        {"email": "prof@test.edu"}).execute().data[0]
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_callback(tc, monkeypatch)
    assert r.status_code == 200, r.text
    assert calls[0]["user"]["id"] == existing["id"]
    assert len(fakedb.tables["idn_users"]) == 1  # no duplicate user


def test_callback_existing_oauth_account_wins_over_email(client, monkeypatch):
    # Account linked to user A; profile email belongs to user B.
    # The OAuth account link is authoritative — session opens for user A.
    tc, fakedb = client
    user_a = fakedb.table("idn_users").insert(
        {"email": "a@test.edu"}).execute().data[0]
    fakedb.table("idn_users").insert(
        {"email": "prof@test.edu"}).execute()
    fakedb.table("idn_oauth_accounts").insert(
        {"provider": "fake", "provider_user_id": "gp-1",
         "user_id": user_a["id"]}).execute()
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_callback(tc, monkeypatch)
    assert r.status_code == 200, r.text
    assert calls[0]["user"]["id"] == user_a["id"]


def test_callback_bad_state_rejected(client):
    tc, _ = client
    r = tc.get("/api/v1/identity/oauth/fake/callback",
               params={"code": "authcode123", "state": "garbage"})
    assert r.status_code in (400, 401)


# --------------------------------------------------------------------------
# B5: no email-match auto-link on unverified provider email
# --------------------------------------------------------------------------

def _do_unverified_callback(tc, monkeypatch):
    r = tc.get("/api/v1/identity/oauth/fake-unverified/start")
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    FakeOAuthProvider.seen["verifier"] = oauth._verify_state(state)["v"]
    return tc.get("/api/v1/identity/oauth/fake-unverified/callback",
                  params={"code": "authcode123", "state": state})


def test_callback_rejects_unverified_email_link_to_existing_user(
        client, monkeypatch):
    # B5 prove-it: attacker pre-registers the victim's email; the victim's
    # first SSO login with an UNVERIFIED provider email must NOT merge into
    # the attacker-controlled account.
    tc, fakedb = client
    fakedb.table("idn_users").insert({"email": "prof@test.edu"}).execute()
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_unverified_callback(tc, monkeypatch)
    assert r.status_code == 403, r.text
    assert "verif" in r.json()["detail"].lower()
    assert not calls  # no session minted
    assert len(fakedb.tables["idn_oauth_accounts"]) == 0  # nothing linked
    assert len(fakedb.tables["idn_users"]) == 1  # no duplicate user


def test_callback_unverified_email_provisions_new_user_unverified(
        client, monkeypatch):
    # Unverified provider email with NO existing account: creation is fine
    # (matches no-verification signup policy), but it stays unverified.
    tc, fakedb = client
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_unverified_callback(tc, monkeypatch)
    assert r.status_code == 200, r.text
    users = fakedb.tables["idn_users"]
    assert len(users) == 1
    assert not users[0].get("email_verified_at")


def test_callback_verified_email_stamps_verified_at_on_existing(
        client, monkeypatch):
    tc, fakedb = client
    fakedb.table("idn_users").insert({"email": "prof@test.edu"}).execute()
    calls = _mint_session_capture(monkeypatch, fakedb)
    r = _do_callback(tc, monkeypatch)
    assert r.status_code == 200, r.text
    users = fakedb.tables["idn_users"]
    assert len(users) == 1
    assert users[0].get("email_verified_at")


# --------------------------------------------------------------------------
# M1: ephemeral state secret must fail hard without the explicit dev flag
# --------------------------------------------------------------------------

def _reset_state_secret(monkeypatch):
    monkeypatch.delenv("TWISTOR_IDENTITY_STATE_SECRET", raising=False)
    monkeypatch.delenv("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", raising=False)
    monkeypatch.setattr(oauth, "_ephemeral_state_secret", None)


def test_state_secret_raises_without_config_and_flag_off(monkeypatch):
    _reset_state_secret(monkeypatch)
    with pytest.raises(RuntimeError, match="TWISTOR_IDENTITY_ALLOW_EPHEMERAL"):
        oauth._state_secret()


def test_state_secret_ephemeral_allowed_with_explicit_flag(monkeypatch):
    _reset_state_secret(monkeypatch)
    monkeypatch.setenv("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", "true")
    with pytest.warns(UserWarning, match="EPHEMERAL"):
        secret = oauth._state_secret()
    assert secret  # 32 bytes of hex
    # Cached: second call reuses it, no second warning.
    assert oauth._state_secret() == secret

"""TW-619 (Zeke) — SAML 2.0 SP tests (TDD)."""
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_identity_oauth import FakeDB  # shared fake supabase

from app.database import get_db
from app.identity import saml
from app.identity import service as identity_service


@pytest.fixture(autouse=True)
def saml_key(monkeypatch):
    monkeypatch.setenv("TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY",
                       Fernet.generate_key().decode())


@pytest.fixture
def fakedb():
    return FakeDB()


@pytest.fixture
def client(fakedb, monkeypatch):
    # Admin override for POST /sso/config: user-1 is org-1 owner.
    def _admin():
        return {"sub": "user-1", "org": "org-1", "role": "owner"}

    app = FastAPI()
    app.include_router(saml.saml_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: fakedb
    app.dependency_overrides[saml._require_org_admin] = _admin
    return TestClient(app, raise_server_exceptions=False), fakedb


def _idp_config():
    return {
        "idp_entity_id": "https://idp.test.edu/metadata",
        "idp_sso_url": "https://idp.test.edu/sso",
        "idp_x509_cert": "MIIBFAKECERTDATA",
        "domains": ["test.edu"],
    }


# --------------------------------------------------------------------------
# App-layer encryption
# --------------------------------------------------------------------------

def test_sso_settings_encrypt_decrypt_roundtrip():
    blob = saml.encrypt_sso_settings({"idp_entity_id": "https://idp.test"})
    assert "idp.test" not in blob
    assert saml.decrypt_sso_settings(blob) == {"idp_entity_id": "https://idp.test"}


def test_decrypt_with_wrong_key_fails(monkeypatch):
    blob = saml.encrypt_sso_settings({"a": 1})
    monkeypatch.setenv("TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY",
                       Fernet.generate_key().decode())
    with pytest.raises(Exception):
        saml.decrypt_sso_settings(blob)


# --------------------------------------------------------------------------
# POST /sso/config
# --------------------------------------------------------------------------

def test_config_stores_encrypted_settings(client):
    tc, fakedb = client
    r = tc.post("/api/v1/identity/sso/config",
                json={"org_id": "org-1", **_idp_config()})
    assert r.status_code == 200, r.text
    rows = fakedb.tables["idn_sso_configs"]
    assert len(rows) == 1
    row = rows[0]
    assert row["org_id"] == "org-1"
    assert row["protocol"] == "saml"
    assert row["is_enabled"] is True
    assert row["domains"] == ["test.edu"]
    # Encrypted at rest: plaintext cert must not appear in the stored blob.
    assert "MIIBFAKECERTDATA" not in row["settings_json"]
    assert saml.decrypt_sso_settings(row["settings_json"])["idp_x509_cert"] \
        == "MIIBFAKECERTDATA"


def test_config_upsert_replaces_existing(client):
    tc, fakedb = client
    tc.post("/api/v1/identity/sso/config",
            json={"org_id": "org-1", **_idp_config()})
    cfg = _idp_config()
    cfg["idp_sso_url"] = "https://idp.test.edu/sso2"
    r = tc.post("/api/v1/identity/sso/config",
                json={"org_id": "org-1", **cfg})
    assert r.status_code == 200, r.text
    assert len(fakedb.tables["idn_sso_configs"]) == 1
    stored = saml.decrypt_sso_settings(
        fakedb.tables["idn_sso_configs"][0]["settings_json"])
    assert stored["idp_sso_url"] == "https://idp.test.edu/sso2"


def test_config_requires_org_id_and_cert(client):
    tc, _ = client
    r = tc.post("/api/v1/identity/sso/config", json={"org_id": "org-1"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# GET /sso/saml/{org}/login — SP-initiated redirect (real lib, offline)
# --------------------------------------------------------------------------

def test_login_redirect_contains_saml_request(client, monkeypatch):
    tc, fakedb = client
    monkeypatch.setenv("TWISTOR_IDENTITY_PUBLIC_API_URL",
                       "https://api.test")
    tc.post("/api/v1/identity/sso/config",
            json={"org_id": "org-1", **_idp_config()})
    r = tc.get("/api/v1/identity/sso/saml/org-1/login",
               follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    location = r.headers["location"]
    assert location.startswith("https://idp.test.edu/sso")
    assert "SAMLRequest=" in location


def test_login_unknown_org_404(client):
    tc, _ = client
    r = tc.get("/api/v1/identity/sso/saml/nope/login",
               follow_redirects=False)
    assert r.status_code == 404


# --------------------------------------------------------------------------
# POST /sso/saml/{org}/acs — validate, provision, mint session
# --------------------------------------------------------------------------

class FakeSamlAuth:
    """Stands in for OneLogin_Saml2_Auth — no XML, no network."""
    errors = []
    nameid = "prof@test.edu"
    attributes = {"email": ["prof@test.edu"], "givenName": ["Pat"]}

    def __init__(self, req, settings):
        self.req = req

    def process_response(self):
        pass

    def get_errors(self):
        return list(FakeSamlAuth.errors)

    def is_authenticated(self):
        return not FakeSamlAuth.errors

    def get_nameid(self):
        return FakeSamlAuth.nameid

    def get_attributes(self):
        return dict(FakeSamlAuth.attributes)


@pytest.fixture
def acs_client(fakedb, monkeypatch):
    monkeypatch.setattr(saml, "OneLogin_Saml2_Auth", FakeSamlAuth)
    calls = []
    monkeypatch.setattr(
        identity_service, "_open_session",
        lambda db, user, ip=None, user_agent=None: calls.append(
            {"user": user}) or {"minted": True})
    app = FastAPI()
    app.include_router(saml.saml_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: fakedb
    FakeSamlAuth.errors = []
    FakeSamlAuth.nameid = "prof@test.edu"
    FakeSamlAuth.attributes = {"email": ["prof@test.edu"]}
    # Seed an enabled SSO config for org-1.
    fakedb.table("idn_sso_configs").insert({
        "org_id": "org-1", "protocol": "saml", "domains": ["test.edu"],
        "settings_json": saml.encrypt_sso_settings(_idp_config()),
        "is_enabled": True,
    }).execute()
    return TestClient(app, raise_server_exceptions=False), fakedb, calls


def test_acs_provisions_user_membership_and_mints_session(acs_client):
    tc, fakedb, calls = acs_client
    r = tc.post("/api/v1/identity/sso/saml/org-1/acs",
                data={"SAMLResponse": "fake", "RelayState": "x"})
    assert r.status_code == 200, r.text
    assert r.json() == {"minted": True}
    users = fakedb.tables["idn_users"]
    assert len(users) == 1
    assert users[0]["email"] == "prof@test.edu"
    assert users[0]["email_verified_at"]  # IdP-authenticated email is trusted
    memberships = fakedb.tables["idn_org_memberships"]
    assert len(memberships) == 1
    assert memberships[0]["org_id"] == "org-1"
    assert memberships[0]["role"] == "member"
    assert calls[0]["user"]["id"] == users[0]["id"]


def test_acs_existing_user_gets_membership_not_duplicate(acs_client):
    tc, fakedb, calls = acs_client
    existing = fakedb.table("idn_users").insert(
        {"email": "prof@test.edu"}).execute().data[0]
    fakedb.table("idn_org_memberships").insert(
        {"user_id": existing["id"], "org_id": "org-1",
         "role": "admin"}).execute()
    r = tc.post("/api/v1/identity/sso/saml/org-1/acs",
                data={"SAMLResponse": "fake"})
    assert r.status_code == 200, r.text
    assert calls[0]["user"]["id"] == existing["id"]
    assert len(fakedb.tables["idn_users"]) == 1
    # Existing membership role is preserved, not overwritten.
    assert fakedb.tables["idn_org_memberships"][0]["role"] == "admin"


def test_acs_invalid_response_rejected(acs_client):
    tc, _, _ = acs_client
    FakeSamlAuth.errors = ["invalid_response"]
    r = tc.post("/api/v1/identity/sso/saml/org-1/acs",
                data={"SAMLResponse": "bogus"})
    assert r.status_code == 401


def test_acs_unknown_org_404(acs_client):
    tc, _, _ = acs_client
    r = tc.post("/api/v1/identity/sso/saml/nope/acs",
                data={"SAMLResponse": "fake"})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# B5: SAML ACS — signed assertion from the org's pinned IdP IS the email
# verification, so email-match auto-link is safe; stamp verified_at.
# --------------------------------------------------------------------------

def test_acs_existing_user_gets_email_verified_at_stamped(acs_client):
    tc, fakedb, calls = acs_client
    fakedb.table("idn_users").insert({"email": "prof@test.edu"}).execute()
    r = tc.post("/api/v1/identity/sso/saml/org-1/acs",
                data={"SAMLResponse": "fake"})
    assert r.status_code == 200, r.text
    users = fakedb.tables["idn_users"]
    assert len(users) == 1
    assert users[0].get("email_verified_at")


# --------------------------------------------------------------------------
# M1: ephemeral Fernet key must fail hard without the explicit dev flag
# --------------------------------------------------------------------------

def _reset_fernet(monkeypatch):
    monkeypatch.delenv("TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", raising=False)
    monkeypatch.setattr(saml, "_cached_fernet", None)
    monkeypatch.setattr(saml, "_cached_fernet_key", None)


def test_fernet_raises_without_key_and_flag_off(monkeypatch):
    _reset_fernet(monkeypatch)
    with pytest.raises(RuntimeError, match="TWISTOR_IDENTITY_ALLOW_EPHEMERAL"):
        saml._fernet()


def test_fernet_ephemeral_allowed_with_explicit_flag(monkeypatch):
    _reset_fernet(monkeypatch)
    monkeypatch.setenv("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", "true")
    with pytest.warns(UserWarning, match="EPHEMERAL"):
        f = saml._fernet()
    assert f is saml._fernet()  # cached

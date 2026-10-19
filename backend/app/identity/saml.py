"""TW-619 Twistor Identity — SAML 2.0 Service Provider.

The university sales wedge: orgs bring their own IdP (university SSO).
Uses python3-saml (MIT, verified from the LICENSE text before adoption).

IdP settings are encrypted at the application layer (Fernet) before they
reach the idn_sso_configs table — the DB never sees a plaintext cert.
"""

import json
import logging
import os
import warnings
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from pydantic import BaseModel, Field
from supabase import Client

from ..config import settings
from ..database import get_db
from . import service, tokens

log = logging.getLogger(__name__)

saml_router = APIRouter(prefix="/identity", tags=["identity"])
bearer = HTTPBearer(auto_error=False)

SAML_BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
SAML_BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"


# ---------------------------------------------------------------------------
# App-layer encryption for IdP settings
# ---------------------------------------------------------------------------

_cached_fernet: Fernet | None = None
_cached_fernet_key: str | None = None


def _fernet() -> Fernet:
    """Fernet keyed by TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY.

    Cache is keyed on the env value so tests (and key rotation) that change
    the env var get a fresh cipher instead of a stale one.
    """
    global _cached_fernet, _cached_fernet_key
    key = os.environ.get("TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY")
    if key != _cached_fernet_key:
        _cached_fernet_key = key
        _cached_fernet = None
    if _cached_fernet is None:
        if not key:
            warnings.warn(
                "TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY not set — generating "
                "EPHEMERAL key. Stored SSO configs will NOT survive "
                "restarts. Set the env var in production."
            )
            key = Fernet.generate_key().decode("ascii")
        _cached_fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
    return _cached_fernet


def encrypt_sso_settings(settings_dict: dict) -> str:
    return _fernet().encrypt(json.dumps(settings_dict).encode("utf-8")).decode("ascii")


def decrypt_sso_settings(blob: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"could not decrypt SSO settings: {exc}") from exc


def _normalize_cert(cert: str) -> str:
    """Strip PEM armor/whitespace — python3-saml wants bare base64."""
    return (
        cert.replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .strip()
    )


# ---------------------------------------------------------------------------
# Config endpoint (org admin only)
# ---------------------------------------------------------------------------


class SsoConfigIn(BaseModel):
    org_id: str
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    domains: list[str] = Field(default_factory=list)


def _require_org_admin(
    body: SsoConfigIn,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Client = Depends(get_db),
) -> dict:
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )
    try:
        claims = tokens.verify_access_token(service.get_keypair(), creds.credentials)
    except tokens.TokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    membership = (
        db.table("idn_org_memberships")
        .select("role")
        .eq("user_id", claims["sub"])
        .eq("org_id", body.org_id)
        .limit(1)
        .execute()
    ).data
    if not membership or membership[0]["role"] not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="org admin required"
        )
    return claims


@saml_router.post("/sso/config")
def sso_config(
    body: SsoConfigIn,
    claims: dict = Depends(_require_org_admin),
    db: Client = Depends(get_db),
):
    """Store (or replace) an org's SAML IdP config. IdP settings are
    encrypted at the app layer before storage."""
    blob = encrypt_sso_settings(
        {
            "idp_entity_id": body.idp_entity_id,
            "idp_sso_url": body.idp_sso_url,
            "idp_x509_cert": _normalize_cert(body.idp_x509_cert),
        }
    )
    row = {
        "protocol": "saml",
        "settings_json": blob,
        "domains": body.domains,
        "is_enabled": True,
    }
    existing = (
        db.table("idn_sso_configs")
        .select("id")
        .eq("org_id", body.org_id)
        .limit(1)
        .execute()
    ).data
    if existing:
        (db.table("idn_sso_configs").update(row).eq("id", existing[0]["id"]).execute())
    else:
        db.table("idn_sso_configs").insert({"org_id": body.org_id, **row}).execute()
    log.info("identity: SAML SSO configured for org %s", body.org_id)
    return {
        "org_id": body.org_id,
        "protocol": "saml",
        "enabled": True,
        "domains": body.domains,
    }


# ---------------------------------------------------------------------------
# SP machinery
# ---------------------------------------------------------------------------


def _public_api_url() -> str:
    return (
        os.environ.get("TWISTOR_IDENTITY_PUBLIC_API_URL")
        or settings.identity_public_api_url
    ).rstrip("/")


def _get_enabled_config(db: Client, org_id: str) -> dict:
    rows = (
        db.table("idn_sso_configs")
        .select("*")
        .eq("org_id", org_id)
        .eq("is_enabled", True)
        .limit(1)
        .execute()
    ).data
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no enabled SSO config for this org",
        )
    return rows[0]


def _sp_settings(org_id: str, config_row: dict) -> dict:
    """python3-saml settings dict from the stored (decrypted) IdP config."""
    idp = decrypt_sso_settings(config_row["settings_json"])
    base = _public_api_url()
    entity_id = (
        os.environ.get("TWISTOR_IDENTITY_SP_ENTITY_ID")
        or f"{base}/identity/saml/metadata"
    )
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": entity_id,
            "assertionConsumerService": {
                "url": f"{base}/api/v1/identity/sso/saml/{org_id}/acs",
                "binding": SAML_BINDING_POST,
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "idp": {
            "entityId": idp["idp_entity_id"],
            "singleSignOnService": {
                "url": idp["idp_sso_url"],
                "binding": SAML_BINDING_REDIRECT,
            },
            "x509cert": idp["idp_x509_cert"],
        },
        "security": {
            "authnRequestsSigned": False,
            "logoutRequestSigned": False,
            "logoutResponseSigned": False,
            "wantMessagesSigned": False,
            "wantAssertionsSigned": True,
            "wantNameId": True,
        },
    }


def _prepare_request(req: Request, post_data: dict | None = None) -> dict:
    """Translate a Starlette request into OneLogin's request dict."""
    return {
        "https": "on" if req.url.scheme == "https" else "off",
        "http_host": (
            req.headers.get("host") or (req.client.host if req.client else "localhost")
        ),
        "server_port": req.url.port or (443 if req.url.scheme == "https" else 80),
        "script_name": req.url.path,
        "get_data": dict(req.query_params),
        "post_data": post_data or {},
    }


def _client_ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    return (
        fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else None)
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


@saml_router.get("/sso/saml/{org_id}/login")
def saml_login(
    org_id: str,
    req: Request,
    db: Client = Depends(get_db),
    return_to: str | None = None,
):
    """SP-initiated login: redirect the browser to the org's IdP."""
    config_row = _get_enabled_config(db, org_id)
    auth = OneLogin_Saml2_Auth(_prepare_request(req), _sp_settings(org_id, config_row))
    url = auth.login(return_to=return_to)
    return RedirectResponse(url, status_code=307)


@saml_router.post("/sso/saml/{org_id}/acs")
async def saml_acs(org_id: str, req: Request, db: Client = Depends(get_db)):
    """Assertion Consumer Service: validate the IdP response, provision the
    user + org membership, and mint a Twistor session."""
    config_row = _get_enabled_config(db, org_id)
    form = await req.form()
    post_data = {k: v for k, v in form.items()}
    auth = OneLogin_Saml2_Auth(
        _prepare_request(req, post_data), _sp_settings(org_id, config_row)
    )
    auth.process_response()
    errors = auth.get_errors()
    if errors or not auth.is_authenticated():
        log.warning("identity: SAML ACS rejected for org %s: %s", org_id, errors)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SAML authentication failed",
        )

    attrs = auth.get_attributes() or {}
    email = _first(attrs.get("email"))
    nameid = auth.get_nameid() or ""
    if not email and "@" in nameid:
        email = nameid
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SAML response contained no email address",
        )
    email = email.strip().lower()

    existing = (
        db.table("idn_users").select("*").eq("email", email).limit(1).execute()
    ).data
    if existing:
        user = existing[0]
    else:
        user = (
            db.table("idn_users")
            .insert(
                {
                    "email": email,
                    # IdP-authenticated: the IdP already proved ownership of this
                    # email, so it counts as verified.
                    "email_verified_at": _now_iso(),
                }
            )
            .execute()
        ).data[0]

    membership = (
        db.table("idn_org_memberships")
        .select("id")
        .eq("user_id", user["id"])
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data
    if not membership:
        db.table("idn_org_memberships").insert(
            {
                "user_id": user["id"],
                "org_id": org_id,
                "role": "member",
            }
        ).execute()

    log.info("identity: SAML sign-in for %s in org %s", user["id"], org_id)
    return service._open_session(
        db, user, _client_ip(req), req.headers.get("user-agent")
    )

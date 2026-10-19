"""TW-619 Twistor Identity — OAuth sign-in (Google first).

Provider interface is open: subclass OAuthProvider and call
register_provider() to add GitHub (or any OAuth2/OIDC provider) next.
State is an HMAC-signed, timestamped blob that carries the PKCE verifier,
so no server-side session storage is needed between /start and /callback.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import warnings
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from supabase import Client

from ..config import settings
from ..database import get_db
from . import service

log = logging.getLogger(__name__)

oauth_router = APIRouter(prefix="/identity", tags=["identity"])

# OAuth state (the `state` query param) lives this long.
STATE_TTL_SECONDS = 600


class StateError(Exception):
    """Raised when the OAuth state blob is malformed, forged, or expired."""


# ---------------------------------------------------------------------------
# Signed state (HMAC-SHA256, stdlib only)
# ---------------------------------------------------------------------------

_ephemeral_state_secret: str | None = None


def _state_secret() -> bytes:
    global _ephemeral_state_secret
    secret = os.environ.get("TWISTOR_IDENTITY_STATE_SECRET")
    if not secret:
        if not service._dev_mode():
            # M1: fail hard — an ephemeral state secret diverges across
            # workers, silently breaking in-flight OAuth flows.
            raise RuntimeError(
                "TWISTOR_IDENTITY_STATE_SECRET is not set and "
                "TWISTOR_IDENTITY_ALLOW_EPHEMERAL is not enabled. Refusing "
                "to sign OAuth state with an ephemeral secret."
            )
        if _ephemeral_state_secret is None:
            warnings.warn(
                "TWISTOR_IDENTITY_ALLOW_EPHEMERAL=true — using EPHEMERAL "
                "OAuth state secret. In-flight OAuth flows will NOT survive "
                "restarts or verify across workers."
            )
            _ephemeral_state_secret = secrets.token_hex(32)
        secret = _ephemeral_state_secret
    return secret.encode("utf-8")


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign_state(payload: dict) -> str:
    body = {"iat": int(time.time()), **payload}
    raw = _b64u(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    sig = _b64u(hmac.new(_state_secret(), raw.encode("ascii"), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def _verify_state(token: str) -> dict:
    try:
        raw, sig = token.split(".")
    except ValueError:
        raise StateError("malformed state")
    expected = _b64u(
        hmac.new(_state_secret(), raw.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise StateError("bad state signature")
    try:
        body = json.loads(_b64u_decode(raw).decode("utf-8"))
    except Exception:
        raise StateError("malformed state")
    if int(time.time()) - int(body.get("iat", 0)) > STATE_TTL_SECONDS:
        raise StateError("state expired")
    return body


# ---------------------------------------------------------------------------
# Provider interface + registry (Google first, GitHub next)
# ---------------------------------------------------------------------------


class OAuthProvider:
    """One OAuth2/OIDC provider. Subclass and register to add a provider."""

    name = "base"

    def authorization_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        raise NotImplementedError

    def fetch_profile(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> dict:
        """Exchange code for tokens, fetch the user profile.

        Returns {"email", "provider_user_id", "email_verified", "name"}.
        """
        raise NotImplementedError


PROVIDERS: dict[str, OAuthProvider] = {}


def register_provider(provider: OAuthProvider) -> None:
    PROVIDERS[provider.name] = provider


def get_provider(name: str) -> OAuthProvider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown OAuth provider: {name}",
        )


def _env_or_503(name: str, label: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{label} is not configured",
        )
    return value


def _redirect_uri(provider: str) -> str:
    override = os.environ.get(
        f"TWISTOR_IDENTITY_{provider.upper()}_REDIRECT_URI"
    ) or os.environ.get("TWISTOR_IDENTITY_OAUTH_REDIRECT_URI")
    if override:
        return override
    base = (
        os.environ.get("TWISTOR_IDENTITY_PUBLIC_API_URL")
        or settings.identity_public_api_url
    ).rstrip("/")
    return f"{base}/api/v1/identity/oauth/{provider}/callback"


class GoogleProvider(OAuthProvider):
    name = "google"
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    SCOPES = "openid email profile"

    def _client_id(self) -> str:
        return _env_or_503("TWISTOR_IDENTITY_GOOGLE_CLIENT_ID", "Google OAuth")

    def _client_secret(self) -> str:
        return _env_or_503("TWISTOR_IDENTITY_GOOGLE_CLIENT_SECRET", "Google OAuth")

    def authorization_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        params = urlencode(
            {
                "client_id": self._client_id(),
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": self.SCOPES,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"{self.AUTHORIZE_URL}?{params}"

    def _oauth2_session(self, redirect_uri: str):
        from authlib.integrations.requests_client import OAuth2Session

        return OAuth2Session(
            self._client_id(),
            self._client_secret(),
            redirect_uri=redirect_uri,
            scope=self.SCOPES,
        )

    def fetch_profile(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> dict:
        session = self._oauth2_session(redirect_uri)
        try:
            session.fetch_token(self.TOKEN_URL, code=code, code_verifier=code_verifier)
            resp = session.get(self.USERINFO_URL)
            data = resp.json()
        except Exception as exc:
            log.warning("identity: Google token/profile exchange failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google OAuth exchange failed",
            )
        email = (data.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google did not return an email",
            )
        return {
            "email": email,
            "provider_user_id": str(data.get("sub") or data.get("id") or ""),
            "email_verified": bool(data.get("email_verified")),
            "name": data.get("name"),
        }


register_provider(GoogleProvider())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _client_ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    return (
        fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else None)
    )


@oauth_router.get("/oauth/{provider}/start")
def oauth_start(provider: str):
    """Return the provider authorization URL (with state + PKCE)."""
    prov = get_provider(provider)
    verifier = _b64u(secrets.token_bytes(32))
    challenge = _b64u(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _sign_state({"v": verifier, "p": provider})
    url = prov.authorization_url(
        state=state, code_challenge=challenge, redirect_uri=_redirect_uri(provider)
    )
    return {"authorization_url": url}


@oauth_router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str, code: str, state: str, req: Request, db: Client = Depends(get_db)
):
    """Exchange the code, find-or-create the user, link the OAuth account,
    and mint a Twistor session.

    B5: email-match auto-link to an EXISTING account requires the provider's
    email_verified claim. An unverified provider email matching an existing
    account is rejected (403) — never merged.
    """
    prov = get_provider(provider)
    try:
        payload = _verify_state(state)
    except StateError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired OAuth state",
        )
    if payload.get("p") != provider or "v" not in payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state does not match this provider",
        )
    profile = prov.fetch_profile(
        code=code, code_verifier=payload["v"], redirect_uri=_redirect_uri(provider)
    )
    email = str(profile["email"]).strip().lower()
    provider_user_id = str(profile["provider_user_id"])
    email_verified = bool(profile.get("email_verified"))

    # The OAuth account link is authoritative: it wins over email matching.
    acct = (
        db.table("idn_oauth_accounts")
        .select("user_id")
        .eq("provider", provider)
        .eq("provider_user_id", provider_user_id)
        .limit(1)
        .execute()
    ).data
    if acct:
        user_id = acct[0]["user_id"]
    else:
        existing = (
            db.table("idn_users").select("id").eq("email", email).limit(1).execute()
        ).data
        if existing:
            if not email_verified:
                # B5: never auto-link an existing account on an UNVERIFIED
                # provider email. With no-verification signup, an attacker
                # who pre-registered the victim's email would absorb the
                # victim's first SSO login (account takeover). idn_users
                # email is UNIQUE, so a separate unlinked account is
                # impossible; reject with an actionable message instead.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"This email is already registered, but your "
                        f"{provider} account did not verify it. Verify "
                        "your email with the provider first, or sign in "
                        "with your existing credentials and link the "
                        "account from settings."
                    ),
                )
            user_id = existing[0]["id"]
        else:
            # No existing account: creation matches the no-verification
            # signup policy, but the new account stays unverified.
            user_id = (db.table("idn_users").insert({"email": email}).execute()).data[
                0
            ]["id"]
        (
            db.table("idn_oauth_accounts")
            .insert(
                {
                    "user_id": user_id,
                    "provider": provider,
                    "provider_user_id": provider_user_id,
                }
            )
            .execute()
        )

    user = (
        db.table("idn_users").select("*").eq("id", user_id).limit(1).execute()
    ).data[0]
    if profile.get("email_verified") and not user.get("email_verified_at"):
        verified_at = service._now().isoformat()
        (
            db.table("idn_users")
            .update({"email_verified_at": verified_at})
            .eq("id", user_id)
            .execute()
        )
        user["email_verified_at"] = verified_at

    log.info("identity: OAuth sign-in via %s for user %s", provider, user_id)
    return service._open_session(
        db, user, _client_ip(req), req.headers.get("user-agent")
    )

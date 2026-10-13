"""
Clerk JWT verification.

Derives the JWKS URL from the publishable key so no secret key is needed
for token verification — uses Clerk's standard .well-known endpoint.
"""
import base64
import threading
import time
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import settings
from .database import get_db

bearer_scheme = HTTPBearer()

_jwks_cache: dict = {}
_jwks_fetched_at: float = 0
_jwks_lock = threading.Lock()   # fix #12: prevent race condition on cache refresh
JWKS_TTL = 3600


def _clerk_jwks_url() -> str:
    """Derive the JWKS URL from the Clerk publishable key.

    Publishable key format: pk_test_<base64(instance-host)>$
    JWKS lives at: https://<instance-host>/.well-known/jwks.json
    """
    pk = settings.clerk_publishable_key
    b64 = pk.split("_", 2)[2].rstrip("$")
    padding = 4 - len(b64) % 4
    if padding != 4:
        b64 += "=" * padding
    host = base64.b64decode(b64).decode("utf-8").rstrip("$")
    return f"https://{host}/.well-known/jwks.json"


def _get_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    with _jwks_lock:  # fix #12: thread-safe cache refresh
        if time.time() - _jwks_fetched_at > JWKS_TTL:
            url = _clerk_jwks_url()
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = time.time()
        return _jwks_cache


def _verify_clerk_jwt(token: str) -> dict:
    try:
        # fix #16: explicit verify_exp; fix #6: conditional audience verification
        decode_options: dict = {"verify_exp": True}
        kwargs: dict = {}
        if settings.should_verify_clerk_audience:
            kwargs["audience"] = settings.clerk_jwt_audience
        else:
            decode_options["verify_aud"] = False

        payload = jwt.decode(
            token,
            _get_jwks(),
            algorithms=["RS256"],
            options=decode_options,
            **kwargs,
        )
        return payload
    except JWTError:
        # fix #7: never leak JWT library internals to the caller
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
        )


class UserContext:
    """Minimal auth — verified user only. Used for onboarding (no org yet)."""
    def __init__(self, user_id: str):
        self.user_id = user_id


class AuthContext:
    """Full auth — verified user with a resolved org UUID."""
    def __init__(self, user_id: str, org_id: str, plan: str = "basic", is_owner: bool = False):
        self.user_id = user_id
        self.org_id  = org_id
        self.plan    = plan
        self.is_owner = is_owner  # TW-484: owner SSO bypasses all plan gates


def is_owner_email(email: str | None) -> bool:
    """TW-484: check an email against the OWNER_EMAILS allowlist (case-insensitive)."""
    if not email:
        return False
    allowed = {e.strip().lower() for e in settings.owner_emails.split(",") if e.strip()}
    return email.strip().lower() in allowed


async def get_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> UserContext:
    """Verify JWT, return user. No org required — safe for onboarding."""
    payload = _verify_clerk_jwt(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim.")
    return UserContext(user_id=user_id)


async def get_auth(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> AuthContext:
    """Verify JWT and resolve the user's organization."""
    payload = _verify_clerk_jwt(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim.")

    db = get_db()
    result = (
        db.table("organizations")
        .select("id, plan, subscription_status")
        .eq("clerk_user_id", user_id)
        .maybe_single()
        .execute()
    )

    if result is None or not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Complete onboarding first.",
        )

    return AuthContext(
        user_id=user_id,
        org_id=result.data["id"],
        plan=result.data.get("plan", "basic"),
        # TW-484: owner SSO — the owner's email unlocks every plan gate.
        is_owner=is_owner_email(get_clerk_user_email(user_id)),
    )


def get_clerk_user_email(user_id: str) -> str | None:
    """
    Fetch the primary email address for a Clerk user via the Clerk API.
    Returns None on any error — callers should handle gracefully.
    """
    try:
        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        primary_id = data.get("primary_email_address_id")
        for addr in data.get("email_addresses", []):
            if addr.get("id") == primary_id:
                return addr.get("email_address")
    except Exception:
        pass
    return None


def get_clerk_user_phone(user_id: str) -> str | None:
    """
    Fetch the primary phone number for a Clerk user via the Clerk API.
    Returns E.164 format (e.g. +13035550101) or None on any error.
    """
    try:
        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        primary_id = data.get("primary_phone_number_id")
        for num in data.get("phone_numbers", []):
            if num.get("id") == primary_id:
                return num.get("phone_number")
    except Exception:
        pass
    return None


def require_plan(minimum_plan: str):
    """Gate a route to a minimum subscription tier."""
    PLAN_ORDER = {"basic": 0, "pro": 1, "premium": 2, "enterprise": 3}

    def _check(auth: AuthContext = Depends(get_auth)) -> AuthContext:
        # TW-484: owner SSO bypasses all plan gates.
        if auth.is_owner:
            return auth
        if PLAN_ORDER.get(auth.plan, 0) < PLAN_ORDER.get(minimum_plan, 99):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires the {minimum_plan} plan or higher.",
            )
        return auth

    return _check

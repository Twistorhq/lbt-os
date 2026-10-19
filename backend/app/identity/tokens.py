"""TW-619 Twistor Identity — JWT + opaque token minting/verification."""

import hashlib
import secrets
import time
import uuid

from jose import JWTError, jwt

from .keys import Keypair

ISSUER = "https://identity.twistor.co"
ACCESS_TTL_SECONDS = 15 * 60


class TokenError(Exception):
    pass


def mint_access_token(
    keypair: Keypair,
    user_id: str,
    org_id: str | None = None,
    role: str | None = None,
    email_verified: bool = False,
    expires_in_seconds: int = ACCESS_TTL_SECONDS,
    issuer: str = ISSUER,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": user_id,
        "iat": now,
        "exp": now + expires_in_seconds,
        "jti": uuid.uuid4().hex,
        "email_verified": email_verified,
    }
    if org_id:
        claims["org"] = org_id
    if role:
        claims["role"] = role
    return jwt.encode(
        claims, keypair.private_pem, algorithm="RS256", headers={"kid": keypair.kid}
    )


def verify_access_token(keypair: Keypair, token: str, issuer: str = ISSUER) -> dict:
    try:
        return jwt.decode(
            token,
            keypair.public_pem,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require_exp": True, "require_iss": True, "require_sub": True},
        )
    except JWTError as e:
        raise TokenError(f"invalid token: {e}") from e


def get_kid(token: str) -> str | None:
    try:
        return jwt.get_unverified_header(token).get("kid")
    except JWTError:
        return None


def _opaque(nbytes: int = 32) -> tuple[str, str]:
    raw = secrets.token_urlsafe(nbytes)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def new_refresh_token() -> tuple[str, str]:
    return _opaque(32)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_magic_token() -> tuple[str, str]:
    return _opaque(32)


def hash_magic_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

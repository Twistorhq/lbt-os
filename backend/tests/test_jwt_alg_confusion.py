"""TW-704 — algorithm-confusion regression tests (GHSA-3qf3-8w2g-rqmx).

python-jose through 3.5.0 lets an HS256 token verify against an RSA public
key when the HMAC secret is the public key material (PEM or DER encoded):
the attacker only needs the *public* key to forge a token. Both production
verification paths must reject every non-RS256 token, no matter what key
material the attacker signs with.

Attack tokens are built by hand (hmac/hashlib/base64) — an attacker does
not use our JWT library, so these tests do not either.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException

from app import auth
from app.identity import keys, tokens


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hs256_token(claims: dict, secret: bytes, kid: str | None = None) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    if kid:
        header["kid"] = kid
    h = _b64u(json.dumps(header).encode())
    p = _b64u(json.dumps(claims).encode())
    sig = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64u(sig)}"


def _none_token(claims: dict, kid: str | None = None) -> str:
    header = {"alg": "none", "typ": "JWT"}
    if kid:
        header["kid"] = kid
    h = _b64u(json.dumps(header).encode())
    p = _b64u(json.dumps(claims).encode())
    return f"{h}.{p}."


def _public_der(public_pem: str) -> bytes:
    pub = serialization.load_pem_public_key(public_pem.encode())
    return pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _identity_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": tokens.ISSUER,
        "sub": "attacker-amara",
        "iat": now,
        "exp": now + 600,
        "jti": "forged",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


# --- app.identity.tokens path (PEM public key passed to the verifier) ---


def test_identity_hs256_signed_with_der_public_key_rejected():
    kp = keys.generate_keypair(kid="tw-der")
    forged = _hs256_token(_identity_claims(), _public_der(kp.public_pem), kid=kp.kid)
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, forged)


def test_identity_hs256_signed_with_pem_public_key_rejected():
    kp = keys.generate_keypair(kid="tw-pem")
    forged = _hs256_token(_identity_claims(), kp.public_pem.encode(), kid=kp.kid)
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, forged)


def test_identity_none_alg_rejected():
    kp = keys.generate_keypair(kid="tw-none")
    forged = _none_token(_identity_claims(), kid=kp.kid)
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, forged)


def test_identity_rs256_from_wrong_key_rejected():
    kp = keys.generate_keypair(kid="tw-right")
    other = keys.generate_keypair(kid="tw-wrong")
    tok = tokens.mint_access_token(other, user_id="attacker-amara")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, tok)


def test_identity_legit_rs256_still_verifies():
    kp = keys.generate_keypair(kid="tw-legit")
    tok = tokens.mint_access_token(kp, user_id="u-legit")
    claims = tokens.verify_access_token(kp, tok)
    assert claims["sub"] == "u-legit"


# --- app.auth Clerk path (JWKS dict passed to the verifier) ---


def _jwks_for(kp, kid: str) -> dict:
    pub = serialization.load_pem_public_key(kp.public_pem.encode())
    nums = pub.public_numbers()

    def int_b64(i: int) -> str:
        raw = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return _b64u(raw)

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": int_b64(nums.n),
                "e": int_b64(nums.e),
            }
        ]
    }


def _clerk_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {"sub": "user_attacker", "iat": now, "exp": now + 600}
    claims.update(overrides)
    return claims


def test_clerk_hs256_signed_with_der_public_key_rejected(monkeypatch):
    kp = keys.generate_keypair(kid="clerk-1")
    monkeypatch.setattr(auth, "_get_jwks", lambda: _jwks_for(kp, "clerk-1"))
    forged = _hs256_token(_clerk_claims(), _public_der(kp.public_pem), kid="clerk-1")
    with pytest.raises(HTTPException) as exc:
        auth._verify_clerk_jwt(forged)
    assert exc.value.status_code == 401


def test_clerk_none_alg_rejected(monkeypatch):
    kp = keys.generate_keypair(kid="clerk-1")
    monkeypatch.setattr(auth, "_get_jwks", lambda: _jwks_for(kp, "clerk-1"))
    forged = _none_token(_clerk_claims(), kid="clerk-1")
    with pytest.raises(HTTPException) as exc:
        auth._verify_clerk_jwt(forged)
    assert exc.value.status_code == 401


def test_clerk_legit_rs256_still_verifies(monkeypatch):
    kp = keys.generate_keypair(kid="clerk-1")
    monkeypatch.setattr(auth, "_get_jwks", lambda: _jwks_for(kp, "clerk-1"))
    tok = tokens.mint_access_token(
        kp, user_id="user_legit", expires_in_seconds=600
    )
    payload = auth._verify_clerk_jwt(tok)
    assert payload["sub"] == "user_legit"

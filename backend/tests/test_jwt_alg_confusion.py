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

import jwt as pyjwt
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


def _jwk_for(kp, kid: str) -> dict:
    pub = serialization.load_pem_public_key(kp.public_pem.encode())
    nums = pub.public_numbers()

    def int_b64(i: int) -> str:
        raw = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return _b64u(raw)

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": int_b64(nums.n),
        "e": int_b64(nums.e),
    }


def _jwks_for(kp, kid: str) -> dict:
    return {"keys": [_jwk_for(kp, kid)]}


def _jwks_multi(pairs: list) -> dict:
    return {"keys": [_jwk_for(kp, kid) for kp, kid in pairs]}


def _rs256_token(kp, claims: dict, kid: str | None = None) -> str:
    headers = {"kid": kid} if kid else {}
    return pyjwt.encode(claims, kp.private_pem, algorithm="RS256", headers=headers)


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


# --- TW-704 fix round (Rosa Major 1): multi-key JWKS parity ---
# python-jose accepted a no-kid token signed by ANY key in the JWKS (it
# tries the key set); the Clerk path must do the same. Kid-present stays
# strict: exactly the named key, nothing else.


def _two_key_setup(monkeypatch):
    kp1 = keys.generate_keypair(kid="clerk-a")
    kp2 = keys.generate_keypair(kid="clerk-b")
    monkeypatch.setattr(
        auth, "_get_jwks", lambda: _jwks_multi([(kp1, "clerk-a"), (kp2, "clerk-b")])
    )
    return kp1, kp2


def test_clerk_kid_selects_non_first_key(monkeypatch):
    _, kp2 = _two_key_setup(monkeypatch)
    tok = _rs256_token(kp2, _clerk_claims(sub="user_second"), kid="clerk-b")
    payload = auth._verify_clerk_jwt(tok)
    assert payload["sub"] == "user_second"


def test_clerk_no_kid_signed_by_second_key_verifies(monkeypatch):
    _, kp2 = _two_key_setup(monkeypatch)
    tok = _rs256_token(kp2, _clerk_claims(sub="user_second"))
    payload = auth._verify_clerk_jwt(tok)
    assert payload["sub"] == "user_second"


def test_clerk_no_kid_signed_by_first_key_verifies(monkeypatch):
    kp1, _ = _two_key_setup(monkeypatch)
    tok = _rs256_token(kp1, _clerk_claims(sub="user_first"))
    payload = auth._verify_clerk_jwt(tok)
    assert payload["sub"] == "user_first"


def test_clerk_no_kid_signed_by_unknown_key_401(monkeypatch):
    _two_key_setup(monkeypatch)
    stranger = keys.generate_keypair(kid="clerk-stranger")
    tok = _rs256_token(stranger, _clerk_claims(sub="user_stranger"))
    with pytest.raises(HTTPException) as exc:
        auth._verify_clerk_jwt(tok)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication failed."


def test_clerk_wrong_kid_still_401(monkeypatch):
    kp1, _ = _two_key_setup(monkeypatch)
    tok = _rs256_token(kp1, _clerk_claims(), kid="clerk-nope")
    with pytest.raises(HTTPException) as exc:
        auth._verify_clerk_jwt(tok)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication failed."


def test_clerk_legit_rs256_still_verifies(monkeypatch):
    kp = keys.generate_keypair(kid="clerk-1")
    monkeypatch.setattr(auth, "_get_jwks", lambda: _jwks_for(kp, "clerk-1"))
    tok = tokens.mint_access_token(kp, user_id="user_legit", expires_in_seconds=600)
    payload = auth._verify_clerk_jwt(tok)
    assert payload["sub"] == "user_legit"

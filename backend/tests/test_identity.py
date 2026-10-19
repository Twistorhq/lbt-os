"""TW-619 Twistor Identity — core unit tests (TDD, no DB)."""

import pytest

from app.identity import keys, passwords, tokens


def test_password_hash_and_verify_roundtrip():
    h = passwords.hash_password("correct horse 3x")
    assert h != "correct horse 3x"
    assert passwords.verify_password("correct horse 3x", h) is True
    assert passwords.verify_password("wrong", h) is False


def test_password_hash_is_salted():
    assert passwords.hash_password("same") != passwords.hash_password("same")


def test_jwt_mint_verify_roundtrip():
    kp = keys.generate_keypair()
    tok = tokens.mint_access_token(
        kp, user_id="u1", org_id="o1", role="owner", email_verified=True
    )
    claims = tokens.verify_access_token(kp, tok)
    assert claims["sub"] == "u1"
    assert claims["org"] == "o1"
    assert claims["role"] == "owner"
    assert claims["email_verified"] is True


def test_jwt_expired_rejected():
    kp = keys.generate_keypair()
    tok = tokens.mint_access_token(kp, user_id="u1", expires_in_seconds=-10)
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, tok)


def test_jwt_wrong_key_rejected():
    kp1, kp2 = keys.generate_keypair(), keys.generate_keypair()
    tok = tokens.mint_access_token(kp1, user_id="u1")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp2, tok)


def test_jwt_tampered_rejected():
    kp = keys.generate_keypair()
    tok = tokens.mint_access_token(kp, user_id="u1")
    head, body, sig = tok.split(".")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(kp, head + "." + body + "A" + sig[1:])


def test_jwt_kid_header_present():
    kp = keys.generate_keypair(kid="k-99")
    tok = tokens.mint_access_token(kp, user_id="u1")
    assert tokens.get_kid(tok) == "k-99"


def test_refresh_token_hash_is_opaque():
    raw, digest = tokens.new_refresh_token()
    assert raw != digest
    assert tokens.hash_refresh_token(raw) == digest


def test_magic_token_single_use_shape():
    raw, digest = tokens.new_magic_token()
    assert len(raw) >= 32
    assert tokens.hash_magic_token(raw) == digest


def test_jwks_publishes_public_key():
    kp = keys.generate_keypair(kid="k-1")
    jwks = keys.public_jwks([kp])
    assert jwks["keys"][0]["kid"] == "k-1"
    assert jwks["keys"][0]["kty"] == "RSA"
    assert "d" not in jwks["keys"][0]  # no private material leaks

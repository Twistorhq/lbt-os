"""TW-721 — strict kid resolution in identity token verification.

Before TW-721, verify_access_token fell back to trying every configured
keypair when the token's kid matched none of them (`or keypairs`), so a
token signed by a configured key verified even when its kid header named
a different (or no) key. Every legitimate identity token is minted by
tokens.mint_access_token, which always stamps the signing keypair's kid,
so strict resolution breaks no real flow — including the M3 rotation
grace window, where the previous keypair stays in the verification list
under its own kid.

These tests pin the strict contract:
(a) valid tokens for each real kid still verify;
(b) an unknown or missing kid is rejected with NO fallback, even when
    the signature is valid for a configured key;
(c) a token signed by the wrong key for a known kid is rejected.
"""

import jwt
import pytest

from app.identity import keys, tokens

CURRENT_KID = "tw-1"
PREVIOUS_KID = "tw-0"


def _claims_of(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def _resign(token: str, keypair, kid_header: str | None) -> str:
    """Re-sign a minted token's claims under an arbitrary key/kid header.

    kid_header=None produces a token with no kid header at all.
    """
    headers = {} if kid_header is None else {"kid": kid_header}
    return jwt.encode(
        _claims_of(token), keypair.private_pem, algorithm="RS256", headers=headers
    )


@pytest.fixture()
def current():
    return keys.generate_keypair(kid=CURRENT_KID)


@pytest.fixture()
def previous():
    return keys.generate_keypair(kid=PREVIOUS_KID)


# (a) valid tokens for each real kid still verify


def test_current_kid_verifies(current, previous):
    tok = tokens.mint_access_token(current, user_id="u-current")
    claims = tokens.verify_access_token([current, previous], tok)
    assert claims["sub"] == "u-current"


def test_previous_kid_verifies_during_rotation_grace(current, previous):
    tok = tokens.mint_access_token(previous, user_id="u-previous")
    claims = tokens.verify_access_token([current, previous], tok)
    assert claims["sub"] == "u-previous"


def test_single_keypair_form_still_verifies(current):
    tok = tokens.mint_access_token(current, user_id="u-single")
    claims = tokens.verify_access_token(current, tok)
    assert claims["sub"] == "u-single"


# (b) unknown / missing kid rejected with no fallback


def test_unknown_kid_rejected_despite_valid_signature(current, previous):
    # Signed by the CURRENT keypair's private key, but the header names a
    # kid nobody configured. The old fallback verified this; strict must not.
    tok = tokens.mint_access_token(current, user_id="u1")
    forged = _resign(tok, current, "tw-unknown")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token([current, previous], forged)


def test_missing_kid_rejected_despite_valid_signature(current, previous):
    # No kid header at all, signature valid for a configured key.
    tok = tokens.mint_access_token(current, user_id="u1")
    forged = _resign(tok, current, None)
    assert tokens.get_kid(forged) is None
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token([current, previous], forged)


def test_rotated_out_kid_rejected_once_grace_ends(current, previous):
    # After the previous keypair leaves the verification list, its kid is
    # unknown and its tokens must stop verifying.
    tok = tokens.mint_access_token(previous, user_id="u-old")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token([current], tok)


def test_unconfigured_issuer_key_rejected(current):
    other = keys.generate_keypair(kid="tw-9")
    tok = tokens.mint_access_token(other, user_id="u-outsider")
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token([current], tok)


# (c) wrong key for a known kid rejected


def test_wrong_key_for_known_kid_rejected(current, previous):
    # Header names the current kid; signature is from an unrelated key.
    attacker = keys.generate_keypair(kid="tw-9")
    tok = tokens.mint_access_token(current, user_id="u-victim")
    forged = _resign(tok, attacker, CURRENT_KID)
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token([current, previous], forged)


def test_garbage_token_rejected(current):
    with pytest.raises(tokens.TokenError):
        tokens.verify_access_token(current, "not-a-jwt")

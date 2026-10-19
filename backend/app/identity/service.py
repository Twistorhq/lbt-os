"""TW-619 Twistor Identity — core auth service (signup/login/refresh/sessions)."""

import logging
import os
import warnings
from datetime import datetime, timedelta, timezone

from . import keys, passwords, tokens
from .keys import Keypair

log = logging.getLogger(__name__)

REFRESH_TTL_DAYS = 30
MAGIC_LINK_TTL_MINUTES = 15
MIN_PASSWORD_LENGTH = 12

_cached_keypair: Keypair | None = None
_cached_previous: Keypair | None = None


def _dev_mode() -> bool:
    return os.environ.get("TWISTOR_IDENTITY_ALLOW_EPHEMERAL", "").lower() in (
        "1",
        "true",
        "yes",
    )


def get_keypair() -> Keypair:
    """Load the signing keypair. Env first; ephemeral ONLY with explicit
    dev flag (M1: multi-worker deployments must never silently diverge)."""
    global _cached_keypair
    if _cached_keypair:
        return _cached_keypair
    pem = os.environ.get("TWISTOR_IDENTITY_PRIVATE_KEY")
    kid = os.environ.get("TWISTOR_IDENTITY_KID", "tw-1")
    if pem:
        _cached_keypair = keys.load_keypair(pem, kid)
    elif _dev_mode():
        warnings.warn(
            "TWISTOR_IDENTITY_ALLOW_EPHEMERAL=true — using EPHEMERAL keypair. "
            "Tokens will NOT verify across workers or restarts."
        )
        log.warning("identity: ephemeral keypair in use (explicit dev flag)")
        _cached_keypair = keys.generate_keypair(kid="tw-dev-ephemeral")
    else:
        raise RuntimeError(
            "TWISTOR_IDENTITY_PRIVATE_KEY is not set and "
            "TWISTOR_IDENTITY_ALLOW_EPHEMERAL is not enabled. Refusing to "
            "start with an ephemeral signing key."
        )
    return _cached_keypair


def get_previous_keypair() -> Keypair | None:
    """Previous signing key for rotation grace (M3: kid grace window)."""
    global _cached_previous
    if _cached_previous:
        return _cached_previous
    pem = os.environ.get("TWISTOR_IDENTITY_PREVIOUS_PRIVATE_KEY")
    if not pem:
        return None
    kid = os.environ.get("TWISTOR_IDENTITY_PREVIOUS_KID", "tw-0")
    _cached_previous = keys.load_keypair(pem, kid)
    return _cached_previous


def get_verification_keypairs() -> list[Keypair]:
    kps = [get_keypair()]
    prev = get_previous_keypair()
    if prev:
        kps.append(prev)
    return kps


def _now():
    return datetime.now(timezone.utc)


def _mint_pair(kp: Keypair, user: dict, org_id: str | None, role: str | None) -> dict:
    access = tokens.mint_access_token(
        kp,
        user_id=user["id"],
        org_id=org_id,
        role=role,
        email_verified=bool(user.get("email_verified_at")),
    )
    raw_refresh, digest = tokens.new_refresh_token()
    return {
        "access_token": access,
        "refresh_token": raw_refresh,
        "refresh_digest": digest,
    }


def _create_session(
    db,
    user_id: str,
    digest: str,
    family_id: str | None,
    rotated_from: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict:
    row = {
        "user_id": user_id,
        "refresh_token_hash": digest,
        "expires_at": (_now() + timedelta(days=REFRESH_TTL_DAYS)).isoformat(),
        "rotated_from": rotated_from,
        "ip": ip,
        "user_agent": user_agent,
    }
    if family_id:
        row["family_id"] = family_id
    res = db.table("idn_sessions").insert(row).execute()
    return res.data[0]


def _get_session_by_digest(db, digest: str) -> dict | None:
    res = (
        db.table("idn_sessions")
        .select("*")
        .eq("refresh_token_hash", digest)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _revoke_family(db, family_id: str):
    (
        db.table("idn_sessions")
        .update({"revoked_at": _now().isoformat()})
        .eq("family_id", family_id)
        .is_("revoked_at", "null")
        .execute()
    )


def _user_orgs(db, user_id: str) -> list[dict]:
    res = (
        db.table("idn_org_memberships")
        .select("org_id,role")
        .eq("user_id", user_id)
        .execute()
    )
    return res.data or []


def signup(db, email: str, password: str, org_name: str | None = None) -> dict:
    """Create user + org + owner membership. Returns user (no session until
    first login — explicit, auditable)."""
    email = email.strip().lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise tokens.TokenError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    existing = (
        db.table("idn_users").select("id").eq("email", email).limit(1).execute()
    ).data
    if existing:
        raise tokens.TokenError("email already registered")
    user = (
        db.table("idn_users")
        .insert(
            {
                "email": email,
                "password_hash": passwords.hash_password(password),
            }
        )
        .execute()
    ).data[0]
    org = (
        db.table("organizations")
        .insert(
            {
                "name": org_name or f"{email.split('@')[0]}'s org",
            }
        )
        .execute()
    ).data[0]
    db.table("idn_org_memberships").insert(
        {
            "user_id": user["id"],
            "org_id": org["id"],
            "role": "owner",
        }
    ).execute()
    return {"user": _public_user(user), "org_id": org["id"]}


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "email_verified": bool(user.get("email_verified_at")),
        "created_at": user.get("created_at"),
    }


def login(db, email: str, password: str, ip=None, user_agent=None) -> dict:
    email = email.strip().lower()
    res = db.table("idn_users").select("*").eq("email", email).limit(1).execute()
    if not res.data:
        raise tokens.TokenError("invalid credentials")
    user = res.data[0]
    if user.get("is_disabled"):
        raise tokens.TokenError("account disabled")
    if not user.get("password_hash") or not passwords.verify_password(
        password, user["password_hash"]
    ):
        raise tokens.TokenError("invalid credentials")
    return _open_session(db, user, ip, user_agent)


def _open_session(db, user: dict, ip=None, user_agent=None) -> dict:
    # B3: the disabled check lives here so EVERY session-minting path
    # (login, magic link, OAuth, SAML, Clerk migration) enforces it.
    if user.get("is_disabled"):
        raise tokens.TokenError("account disabled")
    kp = get_keypair()
    orgs = _user_orgs(db, user["id"])
    org_id = orgs[0]["org_id"] if orgs else None
    role = orgs[0]["role"] if orgs else None
    pair = _mint_pair(kp, user, org_id, role)
    _create_session(db, user["id"], pair["refresh_digest"], None, None, ip, user_agent)
    db.table("idn_users").update({"last_sign_in_at": _now().isoformat()}).eq(
        "id", user["id"]
    ).execute()
    return {
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "user": _public_user(user),
        "orgs": orgs,
    }


def refresh(db, raw_token: str, ip=None, user_agent=None) -> dict:
    """Rotate refresh token. Reuse of a rotated token revokes the family."""
    digest = tokens.hash_refresh_token(raw_token)
    sess = _get_session_by_digest(db, digest)
    if not sess:
        raise tokens.TokenError("invalid refresh token")
    if sess.get("revoked_at"):
        # Reuse detection: a revoked token presented again means theft.
        _revoke_family(db, sess["family_id"])
        log.warning("identity: refresh reuse detected, family revoked")
        raise tokens.TokenError("token reuse detected; sessions revoked")
    if datetime.fromisoformat(sess["expires_at"]) < _now():
        raise tokens.TokenError("refresh token expired")
    user = (
        db.table("idn_users").select("*").eq("id", sess["user_id"]).limit(1).execute()
    ).data[0]
    if user.get("is_disabled"):
        raise tokens.TokenError("account disabled")
    kp = get_keypair()
    orgs = _user_orgs(db, user["id"])
    org_id = orgs[0]["org_id"] if orgs else None
    role = orgs[0]["role"] if orgs else None
    pair = _mint_pair(kp, user, org_id, role)
    new_sess = _create_session(
        db,
        user["id"],
        pair["refresh_digest"],
        sess["family_id"],
        sess["id"],
        ip,
        user_agent,
    )
    (
        db.table("idn_sessions")
        .update({"revoked_at": _now().isoformat()})
        .eq("id", sess["id"])
        .execute()
    )
    return {
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "user": _public_user(user),
        "orgs": orgs,
        "session_id": new_sess["id"],
    }


def logout(db, raw_token: str) -> None:
    digest = tokens.hash_refresh_token(raw_token)
    sess = _get_session_by_digest(db, digest)
    if sess:
        _revoke_family(db, sess["family_id"])


def issue_magic_link(db, email: str, purpose: str = "login") -> str | None:
    """Returns the raw token for emailing (caller sends, never stores)."""
    email = email.strip().lower()
    res = db.table("idn_users").select("id").eq("email", email).limit(1).execute()
    if not res.data:
        # Auto-provision passwordless user so magic link is the signup path.
        user = (db.table("idn_users").insert({"email": email}).execute()).data[0]
    else:
        user = res.data[0]
    raw, digest = tokens.new_magic_token()
    db.table("idn_magic_links").insert(
        {
            "user_id": user["id"],
            "token_hash": digest,
            "purpose": purpose,
            "expires_at": (
                _now() + timedelta(minutes=MAGIC_LINK_TTL_MINUTES)
            ).isoformat(),
        }
    ).execute()
    return raw


def consume_magic_link(db, raw_token: str, ip=None, user_agent=None) -> dict:
    digest = tokens.hash_magic_token(raw_token)
    res = (
        db.table("idn_magic_links")
        .select("*,idn_users!inner(*)")
        .eq("token_hash", digest)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise tokens.TokenError("invalid magic link")
    link = res.data[0]
    if datetime.fromisoformat(link["expires_at"]) < _now():
        raise tokens.TokenError("magic link expired")
    # B1: atomic single-use — the UPDATE only lands when used_at IS NULL,
    # so two concurrent consumes can't both succeed (no TOCTOU).
    claimed = (
        db.table("idn_magic_links")
        .update({"used_at": _now().isoformat()})
        .eq("id", link["id"])
        .is_("used_at", "null")
        .execute()
    )
    if not claimed.data:
        raise tokens.TokenError("magic link already used")
    user = link["idn_users"]
    if isinstance(user, list):
        user = user[0]
    if not user.get("email_verified_at"):
        db.table("idn_users").update({"email_verified_at": _now().isoformat()}).eq(
            "id", user["id"]
        ).execute()
        user["email_verified_at"] = _now().isoformat()
    return _open_session(db, user, ip, user_agent)


def switch_org(db, user_id: str, org_id: str) -> dict:
    """Re-mint access token scoped to a different org the user belongs to."""
    res = (
        db.table("idn_org_memberships")
        .select("role")
        .eq("user_id", user_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise tokens.TokenError("not a member of this organization")
    user = (
        db.table("idn_users").select("*").eq("id", user_id).limit(1).execute()
    ).data[0]
    kp = get_keypair()
    access = tokens.mint_access_token(
        kp,
        user_id=user_id,
        org_id=org_id,
        role=res.data[0]["role"],
        email_verified=bool(user.get("email_verified_at")),
    )
    return {"access_token": access, "org_id": org_id, "role": res.data[0]["role"]}

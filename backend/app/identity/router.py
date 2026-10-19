"""TW-619 Twistor Identity — HTTP API."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from supabase import Client

from ..auth import _verify_clerk_jwt  # migration bridge reuses Clerk verifier
from ..database import get_db
from ..limiter import limiter
from ..services import email as email_service
from . import keys, service, tokens

router = APIRouter(prefix="/identity", tags=["identity"])
bearer = HTTPBearer(auto_error=False)


class SignupIn(BaseModel):
    email: EmailStr
    password: str
    org_name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class MagicLinkIn(BaseModel):
    email: EmailStr
    purpose: str = "login"  # "login" | "verify"


class MagicConsumeIn(BaseModel):
    token: str


class SwitchOrgIn(BaseModel):
    org_id: str


class ClerkMigrateIn(BaseModel):
    clerk_jwt: str


def _client_ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    return (
        fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else None)
    )


def _token_err(e: tokens.TokenError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.post("/signup", status_code=201)
@limiter.limit("10/hour")  # B2: mass-account-creation throttle
def signup(request: Request, body: SignupIn, db: Client = Depends(get_db)):
    try:
        return service.signup(db, body.email, body.password, body.org_name)
    except tokens.TokenError as e:
        raise _token_err(e)


@router.post("/login")
@limiter.limit("20/minute")  # B2: brute-force throttle (IP bucket)
def login(body: LoginIn, request: Request, db: Client = Depends(get_db)):
    try:
        return service.login(
            db,
            body.email,
            body.password,
            _client_ip(request),
            request.headers.get("user-agent"),
        )
    except tokens.TokenError as e:
        raise _token_err(e)


@router.post("/refresh")
@limiter.limit("60/minute")  # B2
def refresh(body: RefreshIn, request: Request, db: Client = Depends(get_db)):
    try:
        return service.refresh(
            db,
            body.refresh_token,
            _client_ip(request),
            request.headers.get("user-agent"),
        )
    except tokens.TokenError as e:
        raise _token_err(e)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, db: Client = Depends(get_db)):
    service.logout(db, body.refresh_token)
    return None


@router.post("/magic-link", status_code=202)
@limiter.limit("5/hour")  # B2: email-bombing throttle
def magic_link(request: Request, body: MagicLinkIn, db: Client = Depends(get_db)):
    # Always 202 — never reveal whether the email exists.
    purpose = body.purpose if body.purpose in ("login", "verify") else "login"
    raw = service.issue_magic_link(db, body.email, purpose=purpose)
    # Fire-and-forget: Resend skips silently when unconfigured. The raw
    # token is emailed and never stored, logged, or returned.
    email_service.send_identity_magic_link(
        to=body.email.strip().lower(), token=raw, purpose=purpose
    )
    return {"sent": True}


@router.post("/magic-link/consume")
@limiter.limit("20/minute")  # B2
def magic_consume(body: MagicConsumeIn, request: Request, db: Client = Depends(get_db)):
    try:
        return service.consume_magic_link(
            db, body.token, _client_ip(request), request.headers.get("user-agent")
        )
    except tokens.TokenError as e:
        raise _token_err(e)


def _require_twistor_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return tokens.verify_access_token(
            service.get_verification_keypairs(), creds.credentials
        )
    except tokens.TokenError as e:
        raise _token_err(e)


@router.get("/me")
def me(claims: dict = Depends(_require_twistor_user), db: Client = Depends(get_db)):
    user = (
        db.table("idn_users")
        .select("id,email,email_verified_at,created_at")
        .eq("id", claims["sub"])
        .limit(1)
        .execute()
    ).data
    if not user:
        raise HTTPException(status_code=401, detail="unknown user")
    orgs = service._user_orgs(db, claims["sub"])
    return {"user": user[0], "orgs": orgs, "active_org": claims.get("org")}


@router.post("/orgs/switch")
def switch_org(
    body: SwitchOrgIn,
    claims: dict = Depends(_require_twistor_user),
    db: Client = Depends(get_db),
):
    try:
        return service.switch_org(db, claims["sub"], body.org_id)
    except tokens.TokenError as e:
        raise _token_err(e)


@router.get("/.well-known/jwks.json")
def jwks():
    return keys.public_jwks(service.get_verification_keypairs())


@router.post("/migrate/clerk")
@limiter.limit("10/hour")  # B2
def migrate_clerk(body: ClerkMigrateIn, request: Request, db: Client = Depends(get_db)):
    """One-time bridge: verify a Clerk JWT, link or create the Twistor
    identity user, and return a Twistor session. After migration the
    client uses Twistor tokens exclusively.

    M4: replay-protected (clerk_migrated_at set once; repeats rejected)
    and sunsettable via TWISTOR_IDENTITY_CLERK_MIGRATION_ENABLED=false.
    """
    import datetime
    import os

    if os.environ.get(
        "TWISTOR_IDENTITY_CLERK_MIGRATION_ENABLED", "true"
    ).lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=410, detail="Clerk migration window is closed")
    try:
        payload = _verify_clerk_jwt(body.clerk_jwt)
    except HTTPException:
        raise HTTPException(status_code=401, detail="invalid Clerk token")
    clerk_id = payload.get("sub")
    if not clerk_id:
        raise HTTPException(status_code=401, detail="invalid Clerk token")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = (
        db.table("idn_users")
        .select("id,clerk_migrated_at")
        .eq("clerk_user_id", clerk_id)
        .limit(1)
        .execute()
    ).data
    if existing:
        if existing[0].get("clerk_migrated_at"):
            raise HTTPException(
                status_code=409, detail="already migrated to Twistor Identity"
            )
        db.table("idn_users").update({"clerk_migrated_at": now}).eq(
            "id", existing[0]["id"]
        ).execute()
        user = (
            db.table("idn_users")
            .select("*")
            .eq("id", existing[0]["id"])
            .limit(1)
            .execute()
        ).data[0]
    else:
        email = payload.get("email") or f"{clerk_id}@clerk.migrated"
        user = (
            db.table("idn_users")
            .insert(
                {
                    "email": str(email).lower(),
                    "clerk_user_id": clerk_id,
                    "clerk_migrated_at": now,
                    "email_verified_at": now,
                }
            )
            .execute()
        ).data[0]
    return service._open_session(
        db, user, _client_ip(request), request.headers.get("user-agent")
    )

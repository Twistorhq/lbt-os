# TW-619 Twistor Identity — Design

Clean-room auth/identity service for Twistor Core. Mechanism studied from
Supabase GoTrue (MIT); all code written fresh. Replaces rented Clerk auth.

## Trust domains

Identity is a first-party auth trust domain, separate from the analytics
medallion. Emails are functionally required (magic links, verification,
SSO). They are stored in `idn_users`, never exposed via API except to the
owning user, never logged. Flagged for Rosa's PII review.

## Tables (`idn_` prefix; orgs reuse existing `organizations.id`)

- `idn_users(id uuid PK, email citext UNIQUE, password_hash text NULL,
  email_verified_at NULL, clerk_user_id text NULL UNIQUE,
  last_sign_in_at NULL, is_disabled bool, created_at, updated_at)`
- `idn_org_memberships(id uuid PK, user_id FK, org_id FK→organizations,
  role text CHECK(owner/admin/member), UNIQUE(user_id,org_id), created_at)`
- `idn_sessions(id uuid PK, user_id FK, refresh_token_hash text UNIQUE,
  expires_at, revoked_at NULL, rotated_from uuid FK NULL,
  family_id uuid, ip inet NULL, user_agent text NULL, created_at)`
- `idn_magic_links(id uuid PK, user_id FK, token_hash text UNIQUE,
  purpose text (login/verify), expires_at, used_at NULL, created_at)`
- `idn_oauth_accounts(id uuid PK, user_id FK, provider text,
  provider_user_id text, UNIQUE(provider,provider_user_id), created_at)`
- `idn_sso_configs(id uuid PK, org_id FK UNIQUE, protocol text (saml/oidc),
  domains text[], settings_json text (app-layer encrypted),
  is_enabled bool, created_at, updated_at)`

## Tokens

- Access JWT: RS256, our keypair. `iss` configurable (default
  `https://identity.twistor.co`), 15-min expiry, claims:
  `sub=user_id, org=org_id, role, email_verified, kid` header.
- Refresh: 32-byte opaque, sha256 stored. 30-day expiry, rotation on every
  use. Reuse of a rotated token → revoke entire family (theft detection).
- Key mgmt: `TWISTOR_IDENTITY_PRIVATE_KEY` (PEM). Dev fallback: ephemeral
  keypair with loud warning. `GET /identity/.well-known/jwks.json`
  publishes public keys by kid; verification accepts current + previous kid
  (grace window).
- Passwords: argon2id (`argon2-cffi`). Magic links: 32-byte token, sha256
  stored, 15-min expiry, single use.

## Endpoints (`/identity` router)

- POST /signup {email,password,org_name?} → user + org + owner membership
- POST /login {email,password} → {access_token, refresh_token, user, orgs}
- POST /refresh {refresh_token} → rotated pair (reuse → family revoked, 401)
- POST /logout {refresh_token} → revoke family
- POST /magic-link {email} → 202 always (no enumeration)
- POST /magic-link/consume {token} → session pair
- GET /me → user + memberships (bearer)
- POST /orgs/switch {org_id} → access token re-minted for org
- GET /oauth/{provider}/start → redirect URL (Zeke: google first)
- GET /oauth/{provider}/callback → session pair (Zeke)
- SSO: POST /sso/config (org admin), GET /sso/saml/{org}/login,
  POST /sso/saml/{org}/acs (Zeke, python3-saml MIT)
- POST /migrate/clerk {clerk_jwt} → verify via existing Clerk verifier,
  upsert user by clerk_user_id, mint session pair
- GET /.well-known/jwks.json

## Migration path (Clerk → Twistor Identity)

1. Deploy identity service alongside Clerk (both verify paths live).
2. Frontend calls /migrate/clerk once per user with their Clerk JWT →
   linked `idn_users` row, Twistor session from then on.
3. New signups go straight to Twistor Identity.
4. Clerk removed when migration counter hits 100%.

## Zeke's split (contract)

- Email delivery via `app/services/email.py` (Resend): magic-link,
  verify-email, SSO-invite templates. Token contract: Zeke receives the
  raw token string + recipient + purpose; he sends, never stores.
- OAuth: Google provider start/callback against the contract above.
- SAML SP: python3-saml (MIT) for login/acs endpoints.
- Frontend API contract doc for Sofia.

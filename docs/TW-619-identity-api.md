# TW-619 Twistor Identity — Frontend API Contract

**Prepared by Twistor Holdings LLC**

Base URL: `{API}/api/v1` (e.g. `https://api.twistor.co/api/v1`).
All JSON unless noted. Authenticated endpoints take
`Authorization: Bearer <access_token>`.

## The token model

- **Access token**: RS256 JWT, **15-minute** expiry. Claims:
  `sub` (user id), `org` (active org id), `role` (`owner` | `admin` |
  `member`), `email_verified` (bool), `iss` (default
  `https://identity.twistor.co`). Header carries `kid` for key rotation.
  Verify against `GET /identity/.well-known/jwks.json`.
- **Refresh token**: 32-byte opaque string, **30-day** expiry, **rotated on
  every use** — each `/refresh` returns a NEW pair; the old refresh token
  is dead after one use.
- **Reuse detection**: presenting an already-rotated refresh token revokes
  the ENTIRE token family (theft response). The user must sign in again.
- **Session pair shape** (returned by login, refresh, magic-link consume,
  OAuth callback, SAML ACS, Clerk migrate):

```json
{
  "access_token": "eyJ...",
  "refresh_token": "opaque-32-byte...",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "email_verified": true,
    "created_at": "2026-10-18T12:00:00+00:00"
  },
  "orgs": [{ "org_id": "uuid", "role": "owner" }]
}
```

**Storage guidance**: access token in memory only (never localStorage);
refresh token in an httpOnly, Secure, SameSite=Lax cookie. On any 401
with `detail` mentioning expiry or reuse, drop both tokens and send the
user to sign in.

## Endpoints

### POST /identity/signup → 201

```json
// request
{ "email": "founder@shop.com", "password": "correct horse 3x", "org_name": "Acme HVAC" }
// response 201
{ "user": { "id": "uuid", "email": "founder@shop.com", "email_verified": false,
            "created_at": "..." },
  "org_id": "uuid" }
```

Creates user + org + `owner` membership. Returns NO session — the user
signs in explicitly next. `org_name` optional (defaults to
`<local-part>'s org`). Duplicate email → **401** `email already registered`.

### POST /identity/login → 200

```json
// request
{ "email": "founder@shop.com", "password": "correct horse 3x" }
// response 200 — session pair (see above)
```

Wrong credentials or disabled account → **401** `invalid credentials` /
`account disabled`. Never reveals whether the email exists.

### POST /identity/refresh → 200

```json
// request
{ "refresh_token": "opaque-32-byte..." }
// response 200 — NEW session pair + "session_id": "uuid"
```

Rotation: old refresh token is revoked, new one issued. Failures →
**401**: `invalid refresh token`, `refresh token expired`, or
`token reuse detected; sessions revoked` (whole family killed — force
re-login).

### POST /identity/logout → 204

```json
{ "refresh_token": "opaque-32-byte..." }
```

Revokes the entire token family (all devices). Always 204, even for a
bogus token.

### POST /identity/magic-link → 202

```json
// request
{ "email": "founder@shop.com", "purpose": "login" }   // purpose: "login" | "verify"
// response 202
{ "sent": true }
```

**Always 202** — no enumeration. Unknown emails get auto-provisioned as
passwordless users, so magic link doubles as the signup path. The email
lands at:

```
{frontend_url}/auth/magic-link?token=<raw-token>&purpose=<purpose>
```

The portal page reads `token` and `purpose` from the query string and
POSTs to `/magic-link/consume`. Link expires in **15 minutes**, single
use. The raw token is emailed only — never returned by the API and never
stored.

### POST /identity/magic-link/consume → 200

```json
// request
{ "token": "<raw-token-from-email>" }
// response 200 — session pair
```

First successful consume marks the email verified. Failures → **401**:
`invalid magic link`, `magic link already used`, `magic link expired`.

### GET /identity/me → 200 (bearer)

```json
{ "user": { "id": "uuid", "email": "...", "email_verified_at": "...",
            "created_at": "..." },
  "orgs": [{ "org_id": "uuid", "role": "owner" }],
  "active_org": "uuid" }
```

### POST /identity/orgs/switch → 200 (bearer)

```json
// request
{ "org_id": "uuid" }
// response 200
{ "access_token": "eyJ...", "org_id": "uuid", "role": "member" }
```

Re-mints an access token scoped to another org the user belongs to.
Non-member → **401** `not a member of this organization`. (Refresh token
unchanged; keep using the existing one.)

### GET /identity/oauth/google/start → 200

```json
// response 200
{ "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?..." }
```

Redirect the browser to `authorization_url`. Unknown provider (only
`google` exists today; `github` is next) → **404**. Unconfigured Google
credentials → **503**.

### GET /identity/oauth/google/callback → 200

Google redirects here with `?code=...&state=...`. **The portal should NOT
call this directly** — the backend handles the exchange. Successful
callback returns a **session pair**; in the browser flow the backend
response goes to the redirect target. Frontend integration pattern:

1. User clicks "Sign in with Google" → portal fetches `/oauth/google/start`
   → `window.location = authorization_url`.
2. Google → backend `/oauth/google/callback?code&state`.
3. Backend exchanges the code (PKCE), fetches the Google profile,
   finds-or-creates the `idn_users` row, links `idn_oauth_accounts`,
   and returns the session pair.

The OAuth account link is authoritative: if the Google account was linked
before, that user wins even if the profile email matches a different row.
Google-verified emails mark `email_verified`. Bad/expired `state` →
**400**. Google exchange failure → **502**.

### POST /identity/sso/config → 200 (bearer, org owner/admin)

```json
// request
{ "org_id": "uuid",
  "idp_entity_id": "https://idp.university.edu/metadata",
  "idp_sso_url": "https://idp.university.edu/sso",
  "idp_x509_cert": "MIIB... (PEM armor optional)",
  "domains": ["university.edu"] }
// response 200
{ "org_id": "uuid", "protocol": "saml", "enabled": true,
  "domains": ["university.edu"] }
```

Upserts the org's SAML IdP config. IdP settings are Fernet-encrypted at
the app layer before storage. Non-admin → **403**. Missing fields →
**422**.

### GET /identity/sso/saml/{org_id}/login → 307

SP-initiated login. Redirect the browser here; the backend 307-redirects
to the org's IdP with a signed AuthnRequest. Unknown org or no enabled
config → **404**.

### POST /identity/sso/saml/{org_id}/acs → 200

The IdP POSTs the SAMLResponse here (form-encoded). The backend
validates, provisions the user + `member` membership (existing
memberships keep their role), and returns a **session pair**. The portal
does not call this directly. Invalid response or missing email →
**401**.

### POST /identity/migrate/clerk → 200

```json
// request
{ "clerk_jwt": "<the user's current Clerk session JWT>" }
// response 200 — session pair
```

One-time bridge per user. The backend verifies the Clerk JWT with the
existing Clerk verifier, upserts `idn_users` by `clerk_user_id` (email
marked verified), and returns a Twistor session pair. From then on the
client uses Twistor tokens exclusively. Invalid Clerk token → **401**.

### GET /identity/.well-known/jwks.json → 200

Public JWKS (`{ "keys": [...] }`) keyed by `kid`. Verification accepts
the current + previous kid (rotation grace window).

## Clerk migration flow (portal)

1. Deploy identity alongside Clerk; both verify paths live.
2. On app load: if the client holds a Clerk session, call
   `/migrate/clerk` **once** with the Clerk JWT → store the Twistor
   session pair, drop Clerk tokens.
3. New signups go straight to Twistor Identity (`/signup` or magic link).
4. Clerk is removed when the migration counter hits 100%.

## Error code summary

| Code | Meaning | Typical details |
|------|---------|-----------------|
| 400 | Bad request | `invalid or expired OAuth state`, `OAuth state does not match this provider` |
| 401 | Unauthorized | `invalid credentials`, `account disabled`, `invalid refresh token`, `refresh token expired`, `token reuse detected; sessions revoked`, `invalid magic link`, `magic link already used`, `magic link expired`, `SAML authentication failed`, `SAML response contained no email address`, `not a member of this organization`, `missing bearer token`, `unknown user` |
| 403 | Forbidden | `org admin required` (SSO config) |
| 404 | Not found | `unknown OAuth provider: <name>`, `no enabled SSO config for this org` |
| 422 | Validation | Pydantic body errors (missing/invalid fields) |
| 502 | Bad gateway | `Google OAuth exchange failed` |
| 503 | Unavailable | `Google OAuth is not configured` |

## Environment (backend)

| Variable | Purpose |
|----------|---------|
| `TWISTOR_IDENTITY_PRIVATE_KEY` | RS256 signing key (PEM). Dev fallback: ephemeral keypair with a loud warning. |
| `TWISTOR_IDENTITY_KID` | Key id for JWT `kid` header (default `tw-1`). |
| `TWISTOR_IDENTITY_GOOGLE_CLIENT_ID` / `TWISTOR_IDENTITY_GOOGLE_CLIENT_SECRET` | Google OAuth. |
| `TWISTOR_IDENTITY_GOOGLE_REDIRECT_URI` | Overrides the default `{PUBLIC_API}/api/v1/identity/oauth/google/callback`. Must match the Google console entry. |
| `TWISTOR_IDENTITY_OAUTH_REDIRECT_URI` | Fallback override for any provider. |
| `TWISTOR_IDENTITY_STATE_SECRET` | HMAC secret for OAuth state blobs (falls back to `API_SECRET`, then ephemeral). |
| `TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY` | Fernet key for IdP settings at rest (ephemeral fallback with warning). |
| `TWISTOR_IDENTITY_PUBLIC_API_URL` | Public base URL for ACS/entityId (default `http://localhost:8000`). |
| `TWISTOR_IDENTITY_SP_ENTITY_ID` | Overrides the default SP entityId. |
| `IDENTITY_FROM_EMAIL` | From address for identity emails (default `Twistor <noreply@twistor.co>`). |
| `RESEND_API_KEY` | Enables actual email delivery; sends are skipped silently without it. |

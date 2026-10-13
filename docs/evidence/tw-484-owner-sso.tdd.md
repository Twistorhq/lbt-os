# Prepared by Twistor Holdings LLC

# TW-484 evidence: owner SSO — TDD report

## What
Single sign-on owner role: Justynn signs in with one of three owner emails
(Twistorhq@gmail.com, Twistorhq@twistor.co, holla@twistor.co) → every plan
gate opens, frontend AND backend. Test anything before users see it.

## Backend
- `backend/app/config.py`: new `owner_emails` setting (comma-separated,
  default empty = nobody).
- `backend/app/auth.py`:
  - `AuthContext` gains `is_owner: bool = False`.
  - `is_owner_email()`: case-insensitive allowlist match; empty/None deny.
  - `get_auth` resolves the Clerk user's email via `get_clerk_user_email`
    and sets `is_owner`.
  - `require_plan` returns early for owners — no 403, ever.
- `backend/app/routers/organizations.py`: `/me` response includes
  `is_owner` so the frontend knows.

## Frontend
- `frontend/src/lib/pro.js`: `isProUnlocked(org)` checks `org?.is_owner`
  first, then the TW-480 dev flag, then the real plan. Also wires
  `applyDevUnlockFromUrl()` at boot (main.jsx).
- Strategy/Dashboard/Connections/AIInsights use the shared check.

## Tests
- `backend/tests/test_owner_sso.py`: allowlist matching (case, whitespace,
  rejects, empty). Logic verified 8/8 standalone (backend deps not
  installed in the worktree; full pytest run in CI).
- Frontend: TW-480's `pro.test.js` covers the flag path; owner path is a
  one-line `org?.is_owner` check on the same function.

## Security
- Allowlist is server-side env (`OWNER_EMAILS`) — never in the bundle.
- Frontend `is_owner` is display-only; real enforcement is `require_plan`
  in the backend.
- To configure: set `OWNER_EMAILS=Twistorhq@gmail.com,Twistorhq@twistor.co,holla@twistor.co`
  in the backend environment. Changing the list needs only an env change +
  restart — no code deploy.

## Deploy note
Backend must be redeployed with OWNER_EMAILS set for this to take effect.
Frontend deploys via the normal Pages pipeline.

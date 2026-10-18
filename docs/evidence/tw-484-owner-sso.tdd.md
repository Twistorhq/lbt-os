# Prepared by Twistor Holdings LLC

# TW-484 evidence: owner SSO — TDD report

## What
Single sign-on owner role: Justynn signs in with one of three owner emails
(owner@example.com, owner@twistor.co, hello@twistor.co) → every plan
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
  rejects, empty). Uses `owner@example.com`-style fixtures — no real emails
  in the repo. Logic verified 7/7 assertions standalone.
- `frontend/src/lib/__tests__/pro.test.js` (7 tests, from TW-480): flag
  override, plan fallbacks, URL handler set/clear/no-op. Included in this
  PR so nothing is lost if TW-480 closes as superseded.

## Security
- Allowlist is server-side env (`OWNER_EMAILS`) — never in the bundle.
- Frontend `is_owner` is display-only; real enforcement is `require_plan`
  in the backend.
- To configure: set `OWNER_EMAILS=owner@example.com,owner@twistor.co,hello@twistor.co`
  in the backend environment. Changing the list needs only an env change +
  restart — no code deploy.

## Deploy note
Backend must be redeployed with OWNER_EMAILS set for this to take effect.
Frontend deploys via the normal Pages pipeline.

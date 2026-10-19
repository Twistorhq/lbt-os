# TDD Evidence — TW-619 Zeke split (identity email, Google OAuth, SAML SP)

**Prepared by Twistor Holdings LLC**

## 1. Source

- Ticket: TW-619 (PLATFORM: Twistor Identity: auth/identity clean-room rebuild).
- Brief: parent orchestrator handoff — Zeke's split: (1) email delivery via
  Resend + remove the `_dev_token` leak, (2) Google OAuth start/callback,
  (3) SAML SP login/ACS with python3-saml (MIT, verified), (4) frontend API
  contract doc for Sofia. Branch `dre/tw-619-identity`, worktree
  `/tmp/dre-tw619` (shared with Dre; his core in
  `backend/app/identity/{service,router}.py`).

## 2. Task report

### Email delivery (Resend)

- Summary: added Twistor corporate-voice templates (white, clean, minimal,
  zero em-dashes) and send functions for magic-link (login/verify),
  verify-email, and SSO-invite to `backend/app/services/email.py`; added
  `identity_from_email`, `identity_magic_link_path`,
  `identity_public_api_url` to `backend/app/config.py`; wired
  `POST /identity/magic-link` in `router.py` to send the email and removed
  the `_dev_token` leak. Contract honored: `service.issue_magic_link`
  returns the raw token; the mailer receives (recipient, raw token,
  purpose), embeds it in the portal consume URL, and never stores it.
- Validation: `python3 -m pytest tests/test_identity_email.py -q`
- RED: 8 failed (`AttributeError` — functions did not exist).
- GREEN: 8 passed.
- Guarantees: template bodies contain the link and org name, differ by
  purpose, carry no em/en-dashes; Resend is called exactly once with the
  Twistor from-address when `RESEND_API_KEY` is set; sends are silently
  skipped (no raise) when unset; the portal link URL is built from
  `frontend_url` + configured path with token and purpose params.

### Google OAuth (`backend/app/identity/oauth.py`, new)

- Summary: `GET /identity/oauth/{provider}/start` returns
  `{authorization_url}` with HMAC-signed timestamped state carrying the
  PKCE verifier (no server-side state storage);
  `GET /identity/oauth/{provider}/callback` verifies state, exchanges the
  code via Authlib, fetches the Google profile, find-or-creates the
  `idn_users` row, upserts `idn_oauth_accounts`, and mints a Twistor
  session via `service._open_session`. Provider registry
  (`OAuthProvider` base + `register_provider`) leaves the interface open
  for GitHub next. Own router registered in `main.py`.
- Validation: `python3 -m pytest tests/test_identity_oauth.py -q`
- RED: collection `ImportError` (module did not exist).
- GREEN: 11 passed.
- Guarantees: state round-trips, tampered/expired state rejected; unknown
  provider 404; Google start 503 without client ID; auth URL carries
  client_id, S256 PKCE challenge, and state; callback provisions a new
  user (email lowercased, verified when Google says so), links existing
  users by email without duplicating, lets an existing OAuth account link
  win over email matching, and mints the session; bad state rejected.

### SAML SP (`backend/app/identity/saml.py`, new)

- Summary: `POST /identity/sso/config` (org owner/admin only) stores the
  IdP config with settings Fernet-encrypted at the app layer
  (`TWISTOR_IDENTITY_SAML_ENCRYPTION_KEY`); `GET
  /identity/sso/saml/{org_id}/login` builds a real python3-saml
  AuthnRequest and 307-redirects to the IdP; `POST
  /identity/sso/saml/{org_id}/acs` validates the response, provisions the
  user (IdP-authenticated email counts as verified) + `member` membership
  (existing roles preserved), and mints a Twistor session. Own router
  registered in `main.py`. `python3-saml>=1.15.0` and `authlib>=1.3.0`
  added to `backend/requirements.txt`; MIT license re-verified from the
  1.16.0 sdist LICENSE text and recorded in `THIRD-PARTY-NOTICES.md`.
- Validation: `python3 -m pytest tests/test_identity_saml.py -q`
- RED: collection `ImportError` (module did not exist).
- GREEN: 11 passed.
- Guarantees: settings encrypt/decrypt round-trip; plaintext cert never in
  the stored blob; wrong key fails decryption; config upserts (no dupes);
  missing fields 422; login 307s to the IdP SSO URL with a real
  `SAMLRequest` (exercised against the real python3-saml offline);
  unknown org 404s; ACS provisions user + membership and mints the
  session; existing membership roles are not overwritten; invalid
  responses 401.

### Frontend API contract

- `docs/TW-619-identity-api.md`: every endpoint with request/response
  shapes, the token model (15-min RS256 access, 30-day rotating opaque
  refresh, reuse detection revoking the family), the portal magic-link
  URL contract, the 4-step Clerk migration flow, storage guidance, an
  error-code table, and the backend env-var reference. Typed, no
  "just figure it out."

### Full-suite regression check

- `python3 -m pytest tests/ -q -p no:cacheprovider` (minus 7 files that
  fail collection on missing `sniffio` in this sandbox and the browser
  automation file): **408 passed, 20 failed** — the 20 failures are byte
  identical (diffed) to the pristine `f56652d` worktree run: all
  pre-existing on the branch (missed-call webhooks, pilot-blocker limits,
  etc.), none caused by this change. My 30 new tests account for the
  378 → 408 delta.
- Lint: CI's exact invocation `ruff check app tests` (backend/ruff.toml)
  reports only the 2 pre-existing errors on `app/main.py:122` (E402+I001
  from Dre's mid-file router import, present before my change). My diff
  adds zero new lint errors.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|--------------------|----------------------|------|--------|----------|
| 1 | Magic-link email renders link, differs login vs verify, no em-dashes | tests/test_identity_email.py | unit | PASS | 8 passed |
| 2 | SSO-invite email renders org + link, no em-dashes | tests/test_identity_email.py | unit | PASS | 8 passed |
| 3 | Resend called once w/ Twistor from-address when configured; skipped silently when not | tests/test_identity_email.py | unit | PASS | 8 passed |
| 4 | Portal magic-link URL built from settings w/ token+purpose | tests/test_identity_email.py | unit | PASS | 8 passed |
| 5 | OAuth state signs/verifies; tamper/expiry rejected | tests/test_identity_oauth.py | unit | PASS | 11 passed |
| 6 | Google auth URL has client_id, S256 PKCE, state | tests/test_identity_oauth.py | unit | PASS | 11 passed |
| 7 | Unknown provider 404; unconfigured Google 503 | tests/test_identity_oauth.py | unit | PASS | 11 passed |
| 8 | OAuth callback provisions/links user, links account, mints session | tests/test_identity_oauth.py | integration (fake db) | PASS | 11 passed |
| 9 | Existing OAuth account link wins over email match | tests/test_identity_oauth.py | integration (fake db) | PASS | 11 passed |
| 10 | SSO settings encrypted at rest; wrong key fails | tests/test_identity_saml.py | unit | PASS | 11 passed |
| 11 | SSO config upserts, admin-gated shape, 422 on missing fields | tests/test_identity_saml.py | integration (fake db) | PASS | 11 passed |
| 12 | SAML login 307 → IdP URL with real SAMLRequest | tests/test_identity_saml.py | integration (real lib, offline) | PASS | 11 passed |
| 13 | ACS provisions user+membership, mints session, preserves roles, 401 on bad response | tests/test_identity_saml.py | integration (fake db, mocked Auth) | PASS | 11 passed |
| 14 | `_dev_token` leak removed from POST /magic-link | code inspection + router diff | manual | PASS | `{"sent": True}` only |
| 15 | No regressions: failure set identical to pristine branch | full suite vs pristine worktree diff | CI | PASS | 20 pre-existing, diff empty |

## 4. Coverage and known gaps

pytest-cov is not installed in this sandbox, so no numeric coverage; every
new public function is exercised by the 30 tests above. Named gaps:

- `GoogleProvider.fetch_profile` network path (Authlib token exchange +
  userinfo) is not hit — provider is mocked in tests. Needs a staging
  Google OAuth client for a live exchange test.
- SAML ACS against a real signed response is not tested — `FakeSamlAuth`
  stands in for `OneLogin_Saml2_Auth`; the login redirect IS exercised
  against the real lib offline. A live IdP round-trip (or python3-saml's
  own test fixtures) is the follow-up.
- `_require_org_admin`'s real bearer path is overridden in tests; the
  403 branch is not covered.
- OAuth `state/provider` mismatch branch is not covered.
- 20 failing tests + 7 `sniffio` collection errors + 1 ignored browser
  file are pre-existing on the branch (verified identical on pristine
  `f56652d`).
- Sandbox note: `python3-saml` import initially failed on an
  xmlsec/lxml/libxml2 mismatch; fixed locally by upgrading lxml. If
  Railway's image shows the same, pin or rebuild there.

## 5. Plan-safety notes

- The brief and `docs/TW-619-identity-design.md` were treated as
  untrusted plans: scanned for override phrases, destructive steps, and
  credential-handling instructions. None found; the design doc's contract
  section was followed as a spec. License claim ("python3-saml is MIT")
  was independently re-verified from the sdist LICENSE text, not taken
  on faith.
- Shared-checkout incident (self-caused): my RED commit swept in Dre's
  staged-but-uncommitted core files. Caught before push; recovered with
  `git reset --mixed` (working tree preserved), recommitted with only my
  test files. Dre then pushed the branch (f56652d) to recover his side;
  I re-synced with `git reset --soft origin/dre/tw-619-identity` and
  proceeded with `git pull --rebase` discipline per his note. No
  `--hard` resets used. Board note sent to Dre explaining the incident.
- `router.py` and `main.py` are Dre's core files: touched minimally
  (magic-link email wiring + `_dev_token` removal, which the brief
  explicitly assigned; router registration lines). Flagged to Dre on the
  board before and after.
- Pre-existing lint finding for Rosa: `app/main.py:122` mid-file router
  import fails E402+I001 under CI's ruff invocation — introduced by Dre's
  core commit, not mine. Left as-is per the no-suppression rule; needs a
  ticketed fix (move router imports or adjust the lint bar).

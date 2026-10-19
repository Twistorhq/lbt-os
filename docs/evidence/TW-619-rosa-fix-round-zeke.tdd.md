# TW-619 Rosa fix round (Zeke share) — TDD evidence

Prepared by Twistor Holdings LLC

## 1. Source

- Ticket TW-619 (PLATFORM: Twistor Identity), Rosa review verdict 2026-10-19 (REQUEST_CHANGES).
- Zeke's share: **B5** (SSO account linking on unverified email), **M2** (email.py logs recipient addresses), **M1** (ephemeral crypto fallbacks in oauth.py/saml.py must fail hard), **N1** (ruff format on the 4 flagged files).
- Branch: `dre/tw-619-identity`, worktree `~/workspace/worktrees/dre-tw619-identity`.
- Base: Dre's 34ec14e (his B1/B2/B3/B4/M1/M3/M4 fixes); my GREEN commit d390547 underneath.

## 2. Task report

**B5 — unverified-email SSO linking (account-takeover vector).**
OAuth callback now requires the provider's `email_verified` claim before
email-match auto-link to an EXISTING account. Unverified provider email +
existing account = 403 with an actionable message (verify at the provider,
or sign in with existing credentials and link from settings). New-account
provisioning on unverified email still allowed (matches no-verification
signup policy) but stays unverified. Documented choice for the 403 (vs.
separate unlinked account): `idn_users.email` is CITEXT UNIQUE, so a
duplicate unlinked account is impossible without a schema change — reject
is the only sound option. SAML ACS: a valid signature-checked assertion
from the org's PINNED IdP (strict mode + wantAssertionsSigned +
org-admin-registered x509 cert) IS the email verification — the IdP
cryptographically vouches for the NameID — so email-match auto-link stays
(documented in the ACS docstring). ACS now also stamps `email_verified_at`
on existing users (the assertion just proved ownership).

**M2 — recipient addresses in email logs.** `backend/app/services/email.py`
`_send()` logged the raw `to` address at debug/info/warning. Now logs
`_recipient_tag(to)` = `sha256:<12 hex chars>` of the normalized address —
subject + truncated hash, never the address. Covers all three log lines
(skipped/sent/failed) and therefore every caller (magic-link, verify,
SSO-invite, audit/sync/weekly).

**M1 — ephemeral crypto fail-hard.** `oauth._state_secret()` and
`saml._fernet()` raised only warnings on missing config (divergent secrets
across workers). Both now follow Dre's `service.get_keypair()` pattern:
env var first, ephemeral ONLY when `TWISTOR_IDENTITY_ALLOW_EPHEMERAL=true`
(with warning), else `RuntimeError`. The `settings.api_secret` fallback in
`_state_secret()` was removed (config marks it "not currently used; remove
weak default"); `settings` import retained (still used for
`identity_public_api_url`).

**N1 — ruff format.** `oauth.py`, `saml.py`, `router.py`,
`app/services/email.py`, `app/main.py`, and the three identity test files
are format-clean. (Repo-wide `ruff format --check` still flags 98
pre-existing files — out of scope; CI runs `ruff check` only.)

**Drive-by fixes required to verify (Dre's lane, flagged on ticket):**
1. `backend/app/identity/router.py`: slowapi's `@limiter.limit` requires
   the request param to be literally named `request`; six decorated
   endpoints used `req`, which raised at import time and broke collection
   of 7 test modules importing `app.main`. Renamed `req` -> `request` on
   the six decorated endpoints only (`_client_ip` helper untouched).
2. `backend/app/main.py:126`: `app.include_router(identity_router, ...)`
   passed the MODULE instead of the APIRouter — `AttributeError` at import.
   Fixed to `identity_router.router` (matches the `.oauth_router` /
   `.saml_router` lines below it).

Validation commands run (this session, fresh):
- `python3 -m pytest tests/test_identity_oauth.py tests/test_identity_saml.py tests/test_identity_email.py -q` — RED run: 5 failed / 4 passed (the 5 prove-it tests failed as designed); GREEN run after fix: 40 passed.
- `python3 -m pytest tests/ -q -p no:cacheprovider` — **559 passed, 8 subtests passed, 0 failed** (full backend suite).
- `python3 -m ruff check app/identity/ app/services/email.py app/main.py tests/test_identity_*.py` — all checks passed.
- `python3 -m ruff format --check` on all 8 touched files — clean.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | OAuth callback REJECTS (403) email-match auto-link when provider email is unverified; no session, no link row, no duplicate user | tests/test_identity_oauth.py::test_callback_rejects_unverified_email_link_to_existing_user | RED | PASS (failed before fix, passes after) | RED commit dd6ee9e; GREEN run 40/40 |
| 2 | OAuth callback still provisions a NEW user on unverified email, session minted, account stays unverified | tests/test_identity_oauth.py::test_callback_unverified_email_provisions_new_user_unverified | unit | PASS | GREEN run |
| 3 | Verified provider email still auto-links existing user and stamps email_verified_at | tests/test_identity_oauth.py::test_callback_links_existing_user_by_email + test_callback_verified_email_stamps_verified_at_on_existing | unit | PASS | GREEN run |
| 4 | SAML ACS stamps email_verified_at on existing users (assertion = verification) | tests/test_identity_saml.py::test_acs_existing_user_gets_email_verified_at_stamped | unit | PASS | GREEN run |
| 5 | `_state_secret()` raises RuntimeError without env var and without explicit dev flag | tests/test_identity_oauth.py::test_state_secret_raises_without_config_and_flag_off | RED | PASS (failed before fix, passes after) | RED commit dd6ee9e |
| 6 | `_state_secret()` allows ephemeral with explicit flag (warns, caches) | tests/test_identity_oauth.py::test_state_secret_ephemeral_allowed_with_explicit_flag | unit | PASS | GREEN run |
| 7 | `saml._fernet()` raises RuntimeError without key and without explicit dev flag | tests/test_identity_saml.py::test_fernet_raises_without_key_and_flag_off | RED | PASS (failed before fix, passes after) | RED commit dd6ee9e |
| 8 | `saml._fernet()` allows ephemeral with explicit flag (warns, caches) | tests/test_identity_saml.py::test_fernet_ephemeral_allowed_with_explicit_flag | unit | PASS | GREEN run |
| 9 | Email send path never logs the recipient address; logs truncated sha256 tag instead | tests/test_identity_email.py::test_send_logs_hash_not_recipient_address | RED | PASS (failed before fix, passes after) | RED commit dd6ee9e |
| 10 | Email skipped path (no API key) never logs the recipient address | tests/test_identity_email.py::test_send_skipped_path_never_logs_recipient | RED | PASS (failed before fix, passes after) | RED commit dd6ee9e |
| 11 | Full backend suite: no regressions from any fix | `python3 -m pytest tests/ -q -p no:cacheprovider` | CI | 559 passed, 8 subtests passed | this session |
| 12 | ruff check clean on all touched files | `python3 -m ruff check ...` | CI | All checks passed | this session |
| 13 | ruff format clean on all touched files (N1) | `python3 -m ruff format --check ...` (8 files) | CI | clean | this session |

## 4. Coverage and known gaps

- Coverage: all three findings have direct prove-it tests (RED-validated).
  B5's OAuth-account-link-wins path and bad-state rejection were already
  covered and still pass.
- Gaps / follow-ups (not mine to close without direction):
  - Dre's B2 claim "rate limits on all identity endpoints" covers only
    `router.py` — `oauth.py` and `saml.py` endpoints have NO
    `@limiter.limit` decorators. Flagged for Rosa's re-review.
  - The two drive-by fixes above are in Dre's files (router.py param
    rename, main.py include_router) — minimal and obviously correct, but
    Dre should confirm on the ticket.
  - `settings.api_secret` is still referenced by
    `services/integrations.py` and `services/visitor_events.py` (pre-existing;
    config says "remove weak default" — separate ticket).
  - Repo-wide `ruff format --check` flags 98 pre-existing files; CI runs
    `ruff check` only, so untouched.

## 5. Plan-safety notes

- No untrusted plan content encountered. All changes are from Rosa's
  written verdict on TW-619 and Dre's handoff message.
- The stashed "zeke uncommitted rewrites pre-rosa-fix" (formatting-only,
  3 files) was verified logic-free via diff inspection and dropped —
  Dre's 34ec14e had already swept the equivalent formatting into the
  branch, and a fresh `ruff format` pass reproduces it.
- Environment note: this sandbox lacked `python3-saml` and `sniffio`
  (PEP 668 system python); installed via `pip --break-system-packages`
  plus an `lxml`/`xmlsec` upgrade to resolve a libxml2 mismatch, purely
  to run the test suite. No production code depends on the install path.
- Pre-review self-scan: no secrets, tokens, or PII in the diff; no network
  calls, subprocess shells, or file writes beyond the brief; no embedded
  instructions in comments/strings/test names. The 403 detail message
  contains no PII beyond echoing the provider name from the URL path.

# TW-721 — Identity tokens: strict kid resolution (remove `or keypairs` fallback)

Prepared by Twistor Holdings LLC

Engineer: Dre Coleman, Senior Backend Engineer & DBA — 2026-10-28
Branch: `dre/tw-721-kid-fallback` off origin/main @ 3142dc9
Commits: `9d5d87a` (RED tests) → `b07d54e` (GREEN fix) → evidence commit

## The finding (Rosa, TW-704 review)

`verify_access_token` in `backend/app/identity/tokens.py` resolved keys as:

```python
candidates = [kp for kp in keypairs if kp.kid == kid] or keypairs
```

When the token's kid matched no configured keypair, verification silently
fell back to trying **every** keypair. A signature was still required, so
this was never a vulnerability — but a token signed by a configured key
verified even when its kid header named a different key (or no key at
all), which defeats the point of kid-based key resolution and hides
issuer misconfiguration.

## Caller / key map (established before touching code)

**The function.** `backend/app/identity/tokens.py::verify_access_token(keypairs, token)`.
`get_kid()` is called only from this function.

**Every production caller passes the same key source** —
`service.get_verification_keypairs()`:

| Caller | Site |
|---|---|
| identity router (`_require_twistor_user`) | `backend/app/identity/router.py:139` |
| identity SAML admin gate | `backend/app/identity/saml.py:123` |
| flags router | `backend/app/flags/router.py:44` |
| flows router | `backend/app/flows/router.py:34` |
| notify router | `backend/app/notify/router.py:31` |
| social router | `backend/app/social/router.py:53` |
| telemetry router | `backend/app/telemetry/router.py:57` |
| verify router | `backend/app/verify/router.py:58` |

All eight verify Twistor-issued bearer access tokens. No external issuer
(Clerk, Supabase) feeds tokens into this path — the Clerk verifier is a
separate function in `backend/app/auth.py` (see "Adjacent path" below).

**Key sources** (`backend/app/identity/service.py`):

- Current signing keypair: env `TWISTOR_IDENTITY_PRIVATE_KEY`, kid from
  env `TWISTOR_IDENTITY_KID` (default `tw-1`; the production deploy key
  is kid `tw-1`). Dev-only ephemeral keypair (explicit
  `TWISTOR_IDENTITY_ALLOW_EPHEMERAL` flag): kid `tw-dev-ephemeral`.
- Previous keypair (M3 rotation grace): env
  `TWISTOR_IDENTITY_PREVIOUS_PRIVATE_KEY`, kid from env
  `TWISTOR_IDENTITY_PREVIOUS_KID` (default `tw-0`).
- `get_verification_keypairs()` returns `[current]` plus `[previous]`
  when configured — at most two keypairs, each under its own kid. The
  JWKS endpoint (`identity/router.py:175`) publishes exactly these kids.

**Minting.** `tokens.mint_access_token` is the only `jwt.encode` in
`backend/app`, and it always stamps `headers={"kid": keypair.kid}`.
Production mint sites: `service._mint_pair` (signup/login/refresh) and
the org-switch mint (`service.py:367`) — both sign with the **current**
keypair. Access-token TTL is 15 minutes, so no long-lived legacy tokens
exist.

**Dependency verdict: no live flow depends on the fallback.** Every
legitimately minted token carries the kid of the key that signed it, and
that keypair is in the verification list for the token's whole lifetime:
current-kid tokens verify against the current keypair; previous-kid
tokens verify during the grace window because the previous keypair stays
in the list under its own kid (pinned by the pre-existing
`test_kid_rotation_grace_window`). Once the previous keypair leaves the
list, its tokens must stop verifying — strict resolution is exactly that
behavior. Nothing had to be flagged as fallback-dependent; the fallback
was removed outright rather than gated.

**Existing tests checked for fallback dependence** (all keep passing):
`test_jwt_wrong_key_rejected`, `test_identity_rs256_from_wrong_key_rejected`,
and the rotation-grace test's unknown-kid assertion all expected rejection
already — under the old code they reached it via signature failure after
the fallback; under strict resolution they reach it at the kid gate. The
TW-704 alg-confusion forgeries carry `kid=kp.kid`, so they still exercise
the algorithm/signature rejection path unchanged.

**Adjacent path (reviewed, deliberately NOT changed).**
`backend/app/auth.py::_clerk_signing_keys` (Clerk JWKS) is strict when a
kid is present (unknown kid → no candidates → reject) and tries all keys
only for a kid-less token. That behavior was a deliberate TW-704
fix-round decision (python-jose parity; Clerk tokens are third-party
minted and we do not control their headers). Different issuer, different
contract — out of scope for this ticket, noted here so the distinction
is on the record.

## The change (tokens.py, +12/−2)

```python
kid = get_kid(token)
if kid is None:
    raise TokenError("invalid token: missing kid header")
candidates = [kp for kp in keypairs if kp.kid == kid]
if not candidates:
    raise TokenError(f"invalid token: unknown kid {kid!r}")
```

Multiple keypairs sharing one kid (not a real configuration) would still
all be tried — resolution is by kid, not by list position. Error type is
unchanged (`TokenError` → HTTP 401 at every caller). No other behavior in
the module changed.

## TDD evidence

New file `backend/tests/test_identity_kid_strict_tw721.py` (9 tests):

- (a) `test_current_kid_verifies`, `test_previous_kid_verifies_during_rotation_grace`,
  `test_single_keypair_form_still_verifies`
- (b) `test_unknown_kid_rejected_despite_valid_signature`,
  `test_missing_kid_rejected_despite_valid_signature`,
  `test_rotated_out_kid_rejected_once_grace_ends`,
  `test_unconfigured_issuer_key_rejected`
- (c) `test_wrong_key_for_known_kid_rejected`, `test_garbage_token_rejected`

The two (b) signature-valid cases are the sharp ones: the token is signed
by a configured keypair's private key, so only the kid gate can reject it.

**RED** (tests run against unmodified main code):

```
FAILED test_unknown_kid_rejected_despite_valid_signature
FAILED test_missing_kid_rejected_despite_valid_signature
2 failed, 7 passed
```

Both failures are "DID NOT RAISE TokenError" — the fallback verified the
token, proving the tests pin the fallback and nothing else.

**GREEN** (after the fix; new file + the three pre-existing identity
suites + the TW-704 alg-confusion suite):

```
45 passed  (tests/test_identity_kid_strict_tw721.py, test_identity.py,
            test_identity_service.py, test_jwt_alg_confusion.py)
```

**Paired full-suite node-set comparison** (`--continue-on-collection-errors`,
junit XML, failed/error node sets extracted and diffed):

```
branch  @ b07d54e: 1712 testcases — 20 failed, 1608 passed, 76 skipped, 8 errors
control @ 3142dc9: 1703 testcases — 20 failed, 1599 passed, 76 skipped, 8 errors
failed/error node sets: IDENTICAL (28 nodes; 0 branch-only, 0 control-only)
collection delta: exactly +9 (the new TW-721 tests, all passing)
```

Control worktree: `~/workspace/worktrees/control-tw721-main` (detached
origin/main @ 3142dc9). The 28 pre-existing failed/error nodes are the
known sandbox baseline, unchanged by this diff.

**Style gate (pre-report):**

```
ruff check app/identity/tokens.py tests/test_identity_kid_strict_tw721.py
→ All checks passed!
ruff format --check (same files) → 2 files already formatted
```

## Honest limits

- Verification is unit/suite level in the sandbox; no live deployment was
  touched. Production behavior change on deploy: an identity token with a
  missing or unconfigured kid now gets a 401 where it previously could
  verify if (and only if) it was signed by a configured key. Per the map
  above, no legitimate issuer produces such a token.
- The 401 detail now distinguishes "missing kid" / "unknown kid" from a
  signature failure. The kid value echoed is caller-supplied and is a
  public identifier (published in JWKS), not secret material.

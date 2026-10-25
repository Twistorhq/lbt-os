# TW-704 — python-jose algorithm confusion: migration to PyJWT

**Source:** TW-704 (critical, reporter Rex via TW-697 ztrust run-1): GHSA-3qf3-8w2g-rqmx / PYSEC-2026-4298 / CVE-2026-85394 in `python-jose[cryptography]==3.5.0`, pinned at `backend/requirements.txt:4`, imported by `backend/app/auth.py` and `backend/app/identity/tokens.py`. Justynn approved the scoped fix 2026-10-25: upgrade past the advisory OR migrate to PyJWT, with tests. No merge/deploy in scope.

## Approach chosen: migrate to PyJWT (upgrade impossible)

- PyPI (checked 2026-10-25): python-jose latest release is **3.5.0 itself** (2025-05-28). OSV range for GHSA-3qf3-8w2g-rqmx: introduced 0, **last_affected 3.5.0** — no patched release exists to upgrade to.
- Library-level bypass reproduced against jose 3.5.0: an HS256 token signed with the DER encoding of an RSA public key is **ACCEPTED** by `jose.jwt.decode` when the key is supplied in DER form without an algorithms pin (the advisory's exact condition: the PEM-armor guard from the CVE-2024-33663 fix does not fire for DER key material). Our two production call sites pin `algorithms=["RS256"]` and pass PEM/JWKS key forms, and were verified to reject the confusion tokens today — the exposure is the broken guard sitting in the production auth library, one call-site change away from a forgeable verifier.
- PyJWT is FOSS (MIT), actively maintained (2.15.1 current on PyPI, checked 2026-10-25), and OSV returns zero vulnerabilities for PyJWT 2.15.1 (query run 2026-10-25; same query for python-jose 3.5.0 returns GHSA-3qf3-8w2g-rqmx).

## What changed

- `backend/requirements.txt`: `python-jose[cryptography]==3.5.0` → `PyJWT[crypto]==2.15.1`.
- `backend/app/identity/tokens.py`: PyJWT encode/decode; `options={"require": ["exp", "iss", "sub"]}` replaces jose's `require_*` keys with identical semantics; `JWTError` → `PyJWTError`. Public signatures unchanged.
- `backend/app/auth.py`: PyJWT decode; new `_clerk_signing_key()` selects the JWKS entry by the token's `kid` into a `PyJWK` (PyJWT 2.15.1 does not consume a raw JWKS dict; a first cut passing a `PyJWKSet` to decode failed with "Expecting a PEM-formatted key" and was replaced). `JWTError` → `PyJWTError`; the 401/no-leak behavior is unchanged.
- `backend/tests/test_jwt_alg_confusion.py` (commit 6e84942, landed before the fix): 8 guard tests. Attack tokens are hand-built with hmac/hashlib/base64 — an attacker does not use our JWT library.

## Task report

- **Algorithm-confusion rejection, identity path** — HS256 tokens signed with (a) DER-encoded and (b) PEM-encoded RSA public key material, and an `alg=none` token, must raise `TokenError` from `verify_access_token`. Validation: `pytest tests/test_jwt_alg_confusion.py`. These pass on the pre-fix code as well *because both production call sites pin RS256* — they are the guarantee the migration had to preserve, and they pin it permanently. The library-level bypass above is the RED evidence for the dependency itself.
- **Algorithm-confusion rejection, Clerk path** — HS256/DER and `alg=none` tokens must surface as HTTP 401 from `_verify_clerk_jwt` (JWKS monkeypatched, no network). Same test file.
- **No behavior regression** — legit RS256 tokens still verify on both paths; wrong-key RS256 still rejected.

## Guarantees

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | HS256 token signed with DER public-key bytes does not verify (identity) | tests/test_jwt_alg_confusion.py::test_identity_hs256_signed_with_der_public_key_rejected | unit | PASS | pytest run 2026-10-25 |
| 2 | HS256 token signed with PEM public-key bytes does not verify (identity) | ::test_identity_hs256_signed_with_pem_public_key_rejected | unit | PASS | same run |
| 3 | alg=none token does not verify (identity + Clerk → 401) | ::test_identity_none_alg_rejected, ::test_clerk_none_alg_rejected | unit | PASS | same run |
| 4 | HS256/DER token → 401 on the Clerk JWKS path | ::test_clerk_hs256_signed_with_der_public_key_rejected | unit | PASS | same run |
| 5 | Legit RS256 tokens still verify on both paths; wrong-key RS256 rejected | ::test_identity_legit_rs256_still_verifies, ::test_clerk_legit_rs256_still_verifies, ::test_identity_rs256_from_wrong_key_rejected | unit | PASS | same run |
| 6 | Identity suites: 49 passed on clean origin/main control → 57 passed on branch (49 + 8 new), same 1 pre-existing SAML collection error | pytest tests/test_identity*.py --continue-on-collection-errors | unit | PASS | control vs branch runs 2026-10-25 |
| 7 | Zero new failures across all auth/identity-touching suites (18 files): failed/error node sets byte-identical control vs branch (20 failed + 7 errors, all pre-existing TW-471 baseline) | pytest (18 files) + diff of FAILED/ERROR node lists | unit | PASS | "NODE SETS IDENTICAL", 27 lines each |
| 8 | python-jose no longer pinned or imported anywhere in backend code | grep -rn "jose" backend (only doc/comment mentions remain) | manual | PASS | grep output 2026-10-25 |
| 9 | PyJWT 2.15.1 has no OSV vulnerabilities; jose 3.5.0 carries the GHSA | OSV query API for both pins | manual | PASS | `{}` vs GHSA-3qf3-8w2g-rqmx |

Tests were run with PyJWT 2.15.1 on PYTHONPATH from an isolated `pip install --target` (the sandbox's preinstalled PyJWT is 2.14.0), so the tested version is the pinned version.

## Coverage and known gaps

- No coverage percentage run; the change surface is two small modules and both verification paths are directly tested.
- `test_identity_saml.py` collection error (xmlsec/lxml pairing) is the pre-existing TW-471 environment defect, identical on control — not touched.
- The 20 failed + 7 error nodes in the wider auth-touching set are the TW-471 main-red baseline, byte-identical before and after.
- Rex's confirming ztrust re-scan is still required before TW-704 closes (per ticket); it is not part of this branch.
- Ruff: `ruff check` clean on all three changed code files; the one remaining repo-wide error (`app/main.py` I001) is pre-existing on origin/main. CI's invocation is `ruff check app tests` (no format gate, and one of the two touched production files was already not format-clean on main).

## Plan-safety notes

- None. No untrusted plan content was encountered; the brief came from Marcus on TW-704 and all advisory data was read from PyPI/OSV as evidence, not instructions.

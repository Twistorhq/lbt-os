# TW-873 Evidence — email-validator missing from backend/requirements.txt

## 1. Source
Ticket TW-873 (urgent bug, filed by Dre from TW-704 first-launch prep).
Brief: add `email-validator>=2.0.0` to `backend/requirements.txt`; prove the
fix in a fresh venv built the way the Dockerfile builds; confirm the CI
collection errors clear. Root cause (Dre, proven three ways on TW-704):
`app/identity/router.py` defines pydantic `EmailStr` fields and sits in
`app/main.py`'s import graph; pydantic raises ImportError at model
definition when email-validator is absent. requirements.txt had bare
`pydantic>=2.10.0` (no `[email]` extra) and no email-validator line.

## 2. Task report
Change: one line in `backend/requirements.txt`, after the pydantic pair:
`email-validator>=2.0.0`. No Python files touched.

Proof environment: fresh venv (`python3 -m venv`), installed exactly as
backend/Dockerfile + CI do: `pip install -r requirements.txt pytest`
(Dockerfile installs requirements.txt alone; CI adds pytest). Production
stub env: APP_ENV=production with stub values satisfying config.py's
production validators (sk_/whsec_/price_ prefixes, CLERK_JWT_AUDIENCE set,
TRUSTED_HOSTS=lbt-os-backend.fly.dev,127.0.0.1). All stub values fake;
no real secrets used or recorded.

RED (venv from the UNFIXED requirements; pip list confirms pydantic
2.14.2 present, email-validator absent):
- Boot: `python -c "from app.main import app"` ->
  `ImportError: email-validator is not installed, run 'pip install
  'pydantic[email]'`` (pydantic/networks.py import_email_validator).
- Collect: `pytest --collect-only -q` -> 2668 tests collected, 8 errors:
  7 files on the email-validator ImportError (test_analytics_events,
  test_security_fixes, test_tradeview_actions, test_tradeview_api,
  test_tradeview_entitlement, test_tradeview_leaks, test_visitor_events)
  + tests/test_speedstd.py on `ModuleNotFoundError: No module named
  'duckdb'` (TW-874, separate). Matches CI run 41205817272 exactly.

GREEN (same venv, reinstalled from the FIXED requirements.txt; the only
delta installed was dnspython + email-validator 2.3.0):
- Boot: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2`
  (the Dockerfile CMD) -> both workers "Application startup complete",
  scheduler started (3 jobs).
- `curl -H "Host: lbt-os-backend.fly.dev" /health` -> HTTP 200
  `{"status":"ok","env":"production","version":"1.0.0"}`.
- Wrong-host control: `curl -H "Host: evil.example.com" /health` ->
  HTTP 400 (TrustedHostMiddleware behaving as Dre recorded).
- Collect: `pytest --collect-only -q` -> 2783 tests collected, 1 error:
  only tests/test_speedstd.py (duckdb, TW-874). All 7 email-validator
  collection errors gone (+115 tests now collect).

## 3. Guarantees
| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Clean install from requirements.txt boots the app in production mode | fresh venv + uvicorn (Dockerfile CMD) + curl /health | RED | ImportError before fix; 200 {status: ok, env: production} after | Section 2 excerpts |
| 2 | The 7 email_validator CI collection errors are eliminated | pytest --collect-only -q in the fresh venv | RED | 8 errors before (7 email + 1 duckdb); 1 error after (duckdb only) | Section 2 counts: 2668 -> 2783 collected |
| 3 | Fix is exactly one dependency line; no code changed | git diff of the branch | manual | requirements.txt +1 line; this evidence doc is the only other file | Commit diff |

## 4. Coverage and known gaps
- Full test suite was NOT run in the venv (collection + boot were the
  brief's proof shape; CI runs the full suite on the merged tree).
- Remaining collection error is duckdb in tests/test_speedstd.py —
  TW-874 (Jalen), intentionally left untouched here.
- Sandbox note: /tmp is a 512 MB tmpfs and filled during the first venv
  install (`No space left on device`); the proof venv lives on workspace
  disk. Environment limit, not a product finding.
- No-token 401 mirror from Dre's prep was not re-run: the route paths
  probed returned 404 in this stub setup and /openapi.json is not served
  in production mode; the brief's bar (/health 200) plus the wrong-host
  400 control were both met. Auth behavior is unchanged by this diff
  (no code touched).

## 5. Plan-safety notes
None. The brief and ticket contained no untrusted or embedded
instructions; nothing outside the assigned one-line fix was acted on.

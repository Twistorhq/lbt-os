# Prepared by Twistor Holdings LLC

# TW-480 evidence: dev unlock flag — TDD report

## RED
Wrote `frontend/src/lib/__tests__/pro.test.js` first (8 tests):
- `isProUnlocked` true with flag set regardless of plan (basic/null/undefined)
- `isProUnlocked` false for basic plan with flag unset
- `isProUnlocked` true for pro/premium/enterprise without flag
- `isDevUnlocked` reflects flag only
- `applyDevUnlockFromUrl` sets flag on `?dev_unlock=1`, strips param
- clears flag on `?dev_unlock=0`
- no-op when param absent

Tests failed before the implementation (module did not exist).

## GREEN
Implemented `frontend/src/lib/pro.js`:
- `isProUnlocked(org)` — localStorage `twistor-dev-unlock === '1'` wins;
  otherwise `plan` in {pro, premium, enterprise}
- `applyDevUnlockFromUrl()` — persists `?dev_unlock=1|0`, strips param via
  `history.replaceState`
- `main.jsx` calls it at boot
- Strategy/Dashboard/Connections/AIInsights switched from inline
  `org?.plan === 'pro' || ...` to shared `isProUnlocked(org)`

All 8 tests pass. `node --check` clean on `pro.js`.

## Refactor
Killed 4 inline duplicate plan checks (single shared function). No behavior
change beyond the flag.

## Verification
- `grep` confirms zero remaining gate checks using raw `plan === 'pro'`
  (one cosmetic tier badge in AIInsights.jsx:106 intentionally left —
  display only, not a gate).
- Rosa review round 1: REQUEST_CHANGES (this doc + tests address the two
  blockers). Round 2 pending.

## Known limitation (Rosa major #4)
Backend enforces plan server-side (`require_plan("pro")` → 403). With the
flag on a non-Pro org, Pro UI renders but Pro API calls 403. Test with a
Pro org, or expect 403s on Pro-only endpoints.

## Removal
Follow-up ticket filed for flag removal before public launch (see ticket).

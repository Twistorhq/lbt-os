# TW-570 — M3 white-label BI: tasks

## Phase 1 — OpenSpec + Rosa proposal review
- [x] proposal.md, design.md, tasks.md written
- [x] Rosa round 1: REQUEST_CHANGES (3 major, 5 minor, 2 nits)
- [x] Design revision: output-shape validation contract, explicit tenant
      boundary, token_hash storage, no-logging rule, snapshots-only public
      path, best-effort sync toll, font_family validation, /view HTML shell,
      3 new security tests + migration policy test
- [ ] Rosa round 2 review of revised design

## Phase 2 — RED tests (commit: failing tests)
- [ ] `backend/tests/test_bi_whitelabel.py`: embed token tests
      (401/403/404/guessing), cross-org 404s, allowlist fail-closed,
      theme validation, toll emission, token uniqueness
- [ ] Validate RED (tests fail before code)

## Phase 3 — Implementation (commit: green)
- [ ] `supabase/migration_bi_whitelabel.sql` (+ RLS org isolation)
- [ ] `TOLL_RATES`: `bi.embed_view` = 5, `bi.connector_sync` = 25
- [ ] `backend/app/services/bi_connectors.py` (protocol + internal + stripe)
- [ ] `backend/app/services/bi_service.py` (dashboards, theme validation,
      embed rendering with WIDGET_METRICS allowlist, best-effort tolls)
- [ ] `backend/app/routers/bi.py` (all endpoints per design)
- [ ] Register router in app
- [ ] `frontend/src/pages/BI.jsx` (list/create/view/embed snippet)
- [ ] Validate GREEN: full new suite + existing suite

## Phase 4 — Evidence + review
- [ ] `ruff` clean (CI's exact invocation)
- [ ] `docs/evidence/TW-570-m3-bi.tdd.md`
- [ ] Pre-review self-scan (secrets, injections, unasked network calls)
- [ ] Rosa diff review (same turn ready) — do NOT merge

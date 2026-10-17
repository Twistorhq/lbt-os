# TW-571 — M4 roofing pro pack: tasks

- [ ] Migration `supabase/migration_roofing.sql`: suppliers, purchase_orders,
  storm_events, storm_campaigns + `quotes.attributes` JSONB ADD + RLS org
  policies on all four new tables.
- [ ] Config: `TOLL_RATES` += takeoff.computed (25), po.sent (50),
  storm.campaign_launched (100).
- [ ] Service `backend/app/services/roofing_service.py`: takeoff math
  (Decimal, exact cents), supplier/PO CRUD + send, NWS storm fetch
  (keyless, honest 502 on failure), campaign launch (existing-customer
  recipients, opt-out language, leads).
- [ ] Router `backend/app/routers/roofing.py` + registration in `main.py`
  under `/api/v1`.
- [ ] Frontend `frontend/src/pages/Suppliers.jsx` + nav route; takeoff form
  on the Sales quote surface.
- [ ] Tests `backend/tests/test_roofing.py` (TDD: RED first):
  takeoff math to exact cents; unknown material → 422; cross-org quote →
  404; PO send transitions status (mocked mail); storm event creates
  campaign + leads (mocked NWS + Twilio); supplier scoped to org.
- [ ] Evidence report `docs/evidence/TW-571-m4-roofing.tdd.md`.
- [ ] `ruff` clean; full suite green; Rosa review; no merge (Justynn's word).

<!-- Prepared by Twistor Holdings LLC -->

# TW-550: M1 flat billing engine — tasks

- [ ] 1. RED: write `backend/tests/test_billing_flat.py` (9 tests per design);
      run, confirm all fail for the right reasons.
- [ ] 2. `config.py`: new price settings, `PLAN_PRICE_MAP`, `PLAN_LIMITS`,
      `TOLL_RATES`, `TRIAL_PERIOD_DAYS`; production validation on new keys.
- [ ] 3. `stripe_service.py`: trial on checkout; `plan_allows_techs()` helper;
      self-healing try/except in `verify_checkout_session`.
- [ ] 4. `stripe_webhooks.py`: `_ALLOWED_CHECKOUT_PLANS` → new tiers.
- [ ] 5. Migration `supabase/migration_toll_ledger.sql` (+ RLS policy).
- [ ] 6. Service `backend/app/services/toll_service.py` (+ unit tests in the
      same test file).
- [ ] 7. GREEN: run new tests + full backend suite; fix until green.
- [ ] 8. `Billing.jsx`: two flat tiers, per-shop unlimited-tech copy; remove
      seat-count language. (No automated frontend test; verify by reading.)
- [ ] 9. `ruff check` on changed files; fix.
- [ ] 10. Evidence report `docs/evidence/TW-550-m1-flat-billing.tdd.md`.
- [ ] 11. Self-scan (secrets, embedded instructions, unasked network/file
      writes); commit RED then GREEN checkpoints; push branch; request Rosa
      review. Do NOT merge.

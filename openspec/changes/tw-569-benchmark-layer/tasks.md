<!-- Prepared by Twistor Holdings LLC -->

# TW-569 tasks

- [x] OpenSpec proposal (this directory)
- [ ] Migration: `supabase/migration_data_layer_framework.sql` (framework +
      serving tables, toll_events.applied_tier/plan_id, cohort hold-and-suppress cols)
- [ ] Schema registry: `backend/app/data_layer/schemas/benchmarks/v1.json`
- [ ] Redaction spec: `backend/app/data_layer/redaction/benchmarks_v1.yaml`
- [ ] Emitters: `backend/app/data_layer/benchmarks/emitters.py` (M1/M2/quote/job metrics, consent-gated)
- [ ] Promotion job: `backend/app/data_layer/benchmarks/promote.py` (idempotent, fault-isolated, data_events)
- [ ] Gold rollup: `backend/app/data_layer/benchmarks/rollup.py` (k-anonymity, hold-and-suppress)
- [ ] Toll worker: `backend/app/data_layer/toll_worker.py` (dynamic volume tiers, dedupe, retry/backoff)
- [ ] Serving: `backend/app/routers/data_v1.py` + main.py registration
- [ ] toll_service: applied_tier + plan_id params (backward compatible)
- [ ] Tests: `backend/tests/test_data_layer_benchmarks.py` (RED first where new logic)
- [ ] Evidence doc: `docs/evidence/TW-569-benchmark-layer.tdd.md`
- [ ] Ruff + pytest green; git status before every commit
- [ ] Rosa review (same turn ready)

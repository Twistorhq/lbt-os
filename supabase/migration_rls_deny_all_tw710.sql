-- Prepared by Twistor Holdings LLC
-- =============================================================
-- TW-710: deny-all RLS for the 19 tables appsecradar run 1 found
-- created without ENABLE ROW LEVEL SECURITY (TRIAGE.md class 1,
-- confirmed by Rex; evidence in
-- ~/workspace/security-audits/lbt-os/appsecradar-run-1/TRIAGE.md).
--
-- The tables: the sci_* data-layer corpus (the data-portal
-- product), stripe_events (billing event record), plan_audit_log
-- (audit trail), lead_stage_history (pipeline history),
-- workspace_exports / migration_runs (data portability), and
-- visitor_events. Supabase serves public-schema tables through
-- PostgREST; with RLS off, any holder of the project anon key
-- (public by design) could read and write them.
--
-- Posture, identical to the TW-650 social schema and TW-651
-- events layer: enable RLS and add one explicit deny-all policy
-- per table (FOR ALL USING (false)). The FastAPI backend is the
-- only client and connects with the service role, which bypasses
-- RLS by design, so service access is unaffected; every other
-- role (anon, authenticated, PostgREST) gets nothing.
--
-- Idempotent: safe to re-run (ALTER ... ENABLE is a no-op when
-- already enabled; policy creation is guarded by pg_policies).
-- Rollback is the DOWN section at the bottom (manual).
--
-- Run AFTER: migration_sci_core_v1.sql,
-- migration_sci_electrocatalysis_v1.sql,
-- migration_sci_high_tc_v1.sql, migration_data_portability.sql,
-- migration_revenue_intelligence.sql, migration_stripe_events.sql,
-- migration_visitor_events.sql.
-- =============================================================

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role is the only reader/writer.
-- ---------------------------------------------------------------

ALTER TABLE sci_labs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_calibrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_bronze_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_benchmarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_benchmark_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_electro_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_electro_gold ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_hightc_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_hightc_gold ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_exports ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_stage_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE stripe_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitor_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_labs_deny_all') THEN
        CREATE POLICY sci_labs_deny_all ON sci_labs FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_calibrations_deny_all') THEN
        CREATE POLICY sci_calibrations_deny_all ON sci_calibrations FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_bronze_artifacts_deny_all') THEN
        CREATE POLICY sci_bronze_artifacts_deny_all ON sci_bronze_artifacts FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_dataset_versions_deny_all') THEN
        CREATE POLICY sci_dataset_versions_deny_all ON sci_dataset_versions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_corrections_deny_all') THEN
        CREATE POLICY sci_corrections_deny_all ON sci_corrections FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_manifests_deny_all') THEN
        CREATE POLICY sci_manifests_deny_all ON sci_manifests FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_attestations_deny_all') THEN
        CREATE POLICY sci_attestations_deny_all ON sci_attestations FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_benchmarks_deny_all') THEN
        CREATE POLICY sci_benchmarks_deny_all ON sci_benchmarks FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_benchmark_runs_deny_all') THEN
        CREATE POLICY sci_benchmark_runs_deny_all ON sci_benchmark_runs FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_electro_measurements_deny_all') THEN
        CREATE POLICY sci_electro_measurements_deny_all ON sci_electro_measurements FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_electro_gold_deny_all') THEN
        CREATE POLICY sci_electro_gold_deny_all ON sci_electro_gold FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_hightc_measurements_deny_all') THEN
        CREATE POLICY sci_hightc_measurements_deny_all ON sci_hightc_measurements FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'sci_hightc_gold_deny_all') THEN
        CREATE POLICY sci_hightc_gold_deny_all ON sci_hightc_gold FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'workspace_exports_deny_all') THEN
        CREATE POLICY workspace_exports_deny_all ON workspace_exports FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'migration_runs_deny_all') THEN
        CREATE POLICY migration_runs_deny_all ON migration_runs FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'lead_stage_history_deny_all') THEN
        CREATE POLICY lead_stage_history_deny_all ON lead_stage_history FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'plan_audit_log_deny_all') THEN
        CREATE POLICY plan_audit_log_deny_all ON plan_audit_log FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'stripe_events_deny_all') THEN
        CREATE POLICY stripe_events_deny_all ON stripe_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'visitor_events_deny_all') THEN
        CREATE POLICY visitor_events_deny_all ON visitor_events FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order)
-- =============================================================
-- DROP POLICY IF EXISTS visitor_events_deny_all ON visitor_events;
-- DROP POLICY IF EXISTS stripe_events_deny_all ON stripe_events;
-- DROP POLICY IF EXISTS plan_audit_log_deny_all ON plan_audit_log;
-- DROP POLICY IF EXISTS lead_stage_history_deny_all ON lead_stage_history;
-- DROP POLICY IF EXISTS migration_runs_deny_all ON migration_runs;
-- DROP POLICY IF EXISTS workspace_exports_deny_all ON workspace_exports;
-- DROP POLICY IF EXISTS sci_hightc_gold_deny_all ON sci_hightc_gold;
-- DROP POLICY IF EXISTS sci_hightc_measurements_deny_all ON sci_hightc_measurements;
-- DROP POLICY IF EXISTS sci_electro_gold_deny_all ON sci_electro_gold;
-- DROP POLICY IF EXISTS sci_electro_measurements_deny_all ON sci_electro_measurements;
-- DROP POLICY IF EXISTS sci_benchmark_runs_deny_all ON sci_benchmark_runs;
-- DROP POLICY IF EXISTS sci_benchmarks_deny_all ON sci_benchmarks;
-- DROP POLICY IF EXISTS sci_attestations_deny_all ON sci_attestations;
-- DROP POLICY IF EXISTS sci_manifests_deny_all ON sci_manifests;
-- DROP POLICY IF EXISTS sci_corrections_deny_all ON sci_corrections;
-- DROP POLICY IF EXISTS sci_dataset_versions_deny_all ON sci_dataset_versions;
-- DROP POLICY IF EXISTS sci_bronze_artifacts_deny_all ON sci_bronze_artifacts;
-- DROP POLICY IF EXISTS sci_calibrations_deny_all ON sci_calibrations;
-- DROP POLICY IF EXISTS sci_labs_deny_all ON sci_labs;
-- ALTER TABLE visitor_events DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE stripe_events DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE plan_audit_log DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE lead_stage_history DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE migration_runs DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE workspace_exports DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_hightc_gold DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_hightc_measurements DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_electro_gold DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_electro_measurements DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_benchmark_runs DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_benchmarks DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_attestations DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_manifests DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_corrections DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_dataset_versions DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_bronze_artifacts DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_calibrations DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE sci_labs DISABLE ROW LEVEL SECURITY;

-- Prepared by Twistor Holdings LLC
-- =============================================================
-- TW-952 (TW-948-B4): additive columns for the persistent job
-- service on the B1 persistence tables.
--
-- B1 (migration_persistence_tw949.sql) landed agt_jobs with the
-- definition shape. B4's service needs two definition fields B1
-- did not carry: the catch-up window for missed fires, and the
-- one-shot instant (cron jobs leave it NULL; one-shot jobs carry
-- cron_expr NULL and their single fire instant here, mirrored
-- into next_fire_at by the service).
--
-- FILES ONLY: this migration is documented and reversible and
-- is NEVER applied by this ticket. Application is gated on
-- Justynn's separate word, per the TW-949 / TW-926 precedent.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- Run order: AFTER migration_persistence_tw949.sql.
-- =============================================================

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS catch_up_window_minutes INTEGER NOT NULL DEFAULT 60
        CHECK (catch_up_window_minutes >= 0);

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS one_shot_at TIMESTAMPTZ;

COMMENT ON COLUMN agt_jobs.catch_up_window_minutes IS
    'TW-952: a missed fire at most this many minutes old still fires once on ticker recovery; older slots are skipped and the schedule advances.';
COMMENT ON COLUMN agt_jobs.one_shot_at IS
    'TW-952: the single fire instant for one-shot jobs (cron_expr is NULL for those rows).';

-- =============================================================
-- DOWN section (manual rollback). Nothing here runs on
-- re-apply; it is the documented reversal.
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS one_shot_at;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS catch_up_window_minutes;
-- =============================================================

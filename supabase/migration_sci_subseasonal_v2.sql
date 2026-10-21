-- TW-599 Wave 1: S2S-TRUST v1 truth-lineage columns (subseasonal lane).
-- Prepared by Twistor Holdings LLC.
--
-- Diego's TW-592 S2S-TRUST v1 spec: every forecast record carries its truth
-- lineage (truth_source, lag, assimilation window, reforecast design) so a
-- scientist can audit what "truth" a score was computed against. Nullable:
-- records missing truth_source quarantine to provenance_incomplete at ingest.

ALTER TABLE sci_subseasonal_measurements
    ADD COLUMN IF NOT EXISTS center              TEXT,
    ADD COLUMN IF NOT EXISTS model_cycle         TEXT,
    ADD COLUMN IF NOT EXISTS run_type            TEXT
        CHECK (run_type IS NULL OR run_type IN ('realtime', 'reforecast')),
    ADD COLUMN IF NOT EXISTS truth_source        TEXT,
    ADD COLUMN IF NOT EXISTS truth_lag_days      INTEGER,
    ADD COLUMN IF NOT EXISTS assimilation_window TEXT,
    ADD COLUMN IF NOT EXISTS reforecast_design   TEXT;

ALTER TABLE sci_subseasonal_gold
    ADD COLUMN IF NOT EXISTS center              TEXT,
    ADD COLUMN IF NOT EXISTS model_cycle         TEXT,
    ADD COLUMN IF NOT EXISTS run_type            TEXT,
    ADD COLUMN IF NOT EXISTS truth_source        TEXT,
    ADD COLUMN IF NOT EXISTS truth_lag_days      INTEGER,
    ADD COLUMN IF NOT EXISTS assimilation_window TEXT,
    ADD COLUMN IF NOT EXISTS reforecast_design   TEXT;

CREATE INDEX IF NOT EXISTS sci_subseasonal_truth_src_idx
    ON sci_subseasonal_measurements (truth_source);
CREATE INDEX IF NOT EXISTS sci_subseasonal_center_idx
    ON sci_subseasonal_measurements (center, model_cycle);

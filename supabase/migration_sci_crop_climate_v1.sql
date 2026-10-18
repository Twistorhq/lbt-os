-- =============================================================
-- TW-593 Track 2: crop-climate lane V1 (TW-596)
-- Silver measurements + gold mart with train/test_future split tags.
--
-- DEPENDENCY: apply the shared science-core migration FIRST
-- (sci_labs, sci_calibrations, sci_bronze_artifacts, sci_dataset_versions,
--  sci_corrections, sci_manifests, sci_attestations, sci_benchmarks,
--  sci_benchmark_runs — owned by the electrocatalysis lane agent).
-- This file creates ONLY lane tables. Never touches shared files.
--
-- The climate-shift generalization benchmark (Diego 2026-10-18 lane:
-- "predict 2035 performance from 2025 data") withholds future-like
-- conditions. Gold rows carry `split`: 'train' | 'test_future'.
-- The hidden split is the point — test rows must never leak into
-- train queries.
--
-- V1 seeds SYNTHETIC data only (synthetic=true), labeled as such.
-- No real lab data until partners sign.
-- =============================================================

-- ---------------- Silver: per-trial measurements ----------------
CREATE TABLE IF NOT EXISTS sci_crop_measurements (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id     UUID        NOT NULL REFERENCES sci_bronze_artifacts (id),
    lab_id          UUID        NOT NULL REFERENCES sci_labs (id),
    calibration_id  UUID        REFERENCES sci_calibrations (id),
    -- NULL-able: field trials do not run through a calibrated instrument.
    -- (the SLAC round-robin lesson lives here: no calibration, no trust)
    conditions      JSONB       NOT NULL,
    -- lane-schema'd conditions: MUST carry trial_design
    --   {trial_design: 'rcbd'|'split_plot'|'strip', replicates: int,
    --    simulated_future_climate: bool, climate_scenario: 'ssp245'|'ssp585'|null}
    genotype        TEXT        NOT NULL,
    location        TEXT        NOT NULL,
    year            INT         NOT NULL,
    season          TEXT        NOT NULL DEFAULT 'summer',
    weather         JSONB       NOT NULL DEFAULT '{}',
    -- {season_precip_mm, season_gdd, heat_days_gt35c, mean_temp_c,
    --  temp_anomaly_c, drought_index}
    management      JSONB       NOT NULL DEFAULT '{}',
    -- {irrigation: 'rainfed'|'irrigated', nitrogen_kg_ha, tillage,
    --  planting_doy, previous_crop}
    yield_t_ha      NUMERIC     NOT NULL,
    synthetic       BOOLEAN     NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_crop_conditions_trial_design
        CHECK (conditions ? 'trial_design'),
    CONSTRAINT chk_crop_year_range
        CHECK (year BETWEEN 1900 AND 2100),
    CONSTRAINT chk_crop_season
        CHECK (season IN ('spring', 'summer', 'fall', 'winter')),
    CONSTRAINT chk_crop_yield_sane
        CHECK (yield_t_ha >= 0 AND yield_t_ha <= 60)
);

CREATE INDEX IF NOT EXISTS idx_sci_crop_measurements_gly
    ON sci_crop_measurements (genotype, location, year);
CREATE INDEX IF NOT EXISTS idx_sci_crop_measurements_lab
    ON sci_crop_measurements (lab_id);
CREATE INDEX IF NOT EXISTS idx_sci_crop_measurements_artifact
    ON sci_crop_measurements (artifact_id);

-- ---------------- Gold: benchmark-ready snapshot rows ----------------
-- One row per (dataset_version, silver measurement). Immutable: corrections
-- create a NEW dataset version; they never mutate these rows.
CREATE TABLE IF NOT EXISTS sci_crop_gold (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id  UUID        NOT NULL REFERENCES sci_dataset_versions (id),
    silver_id           UUID        NOT NULL REFERENCES sci_crop_measurements (id),
    split               TEXT        NOT NULL,
    -- 'train' | 'test_future': the climate-shift generalization benchmark's
    -- hidden split. Future-like conditions (projected 2035 climate) are
    -- withheld in 'test_future' and MUST NOT leak into train queries.
    genotype            TEXT        NOT NULL,
    location            TEXT        NOT NULL,
    year                INT         NOT NULL,
    yield_t_ha          NUMERIC     NOT NULL,
    synthetic           BOOLEAN     NOT NULL DEFAULT false,
    attestation_summary JSONB       NOT NULL DEFAULT '{}',
    -- {reproduced: int, partial: int, failed: int} — honest uncertainty,
    -- unattested cohorts ship with the label on them (TW-593 §3.4)

    CONSTRAINT chk_crop_gold_split
        CHECK (split IN ('train', 'test_future')),
    CONSTRAINT uq_crop_gold_version_silver
        UNIQUE (dataset_version_id, silver_id)
);

CREATE INDEX IF NOT EXISTS idx_sci_crop_gold_version_split
    ON sci_crop_gold (dataset_version_id, split);
CREATE INDEX IF NOT EXISTS idx_sci_crop_gold_gly
    ON sci_crop_gold (genotype, location, year);

-- Version immutability: gold rows are append-only snapshots.
-- Any UPDATE or DELETE attempt fails loud. Corrections supersede via
-- sci_corrections + a new sci_dataset_versions row, per TW-593 §3.3.
CREATE OR REPLACE FUNCTION sci_crop_gold_block_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'sci_crop_gold is immutable (dataset version %). Use sci_corrections + a new dataset version.',
        COALESCE(OLD.dataset_version_id, NEW.dataset_version_id);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sci_crop_gold_immutable ON sci_crop_gold;
CREATE TRIGGER trg_sci_crop_gold_immutable
    BEFORE UPDATE OR DELETE ON sci_crop_gold
    FOR EACH ROW EXECUTE FUNCTION sci_crop_gold_block_mutation();

-- ---------------- RLS: science datasets are public goods ----------------
-- No PII in silver/gold (TW-552: researcher PII is hashed in bronze only).
ALTER TABLE sci_crop_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_crop_gold ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sci_crop_measurements_read ON sci_crop_measurements;
CREATE POLICY sci_crop_measurements_read ON sci_crop_measurements
    FOR SELECT USING (true);

DROP POLICY IF EXISTS sci_crop_gold_read ON sci_crop_gold;
CREATE POLICY sci_crop_gold_read ON sci_crop_gold
    FOR SELECT USING (true);

-- Writes stay service-role only (no write policies issued).

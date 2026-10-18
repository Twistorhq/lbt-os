-- =============================================================
-- TW-593 Track 2 / SUBSEASONAL lane V1: lane tables only.
-- Shared core tables (sci_labs, sci_calibrations, sci_bronze_artifacts,
-- sci_dataset_versions, sci_corrections, sci_manifests, sci_attestations,
-- sci_benchmarks, sci_benchmark_runs) are owned by the electrocatalysis
-- lane's core migration and MUST run before this file.
--
-- Subseasonal prediction (weeks 2-6) lane: probabilistic forecast
-- measurements with bias-correction provenance. The MIT/Microsoft/ECMWF
-- Nature (Oct 2026) result — learned probabilistic bias correction on
-- top of dynamical models, doubled accuracy vs ECMWF baseline, beat
-- climatology for the first time — is this lane's breach to productize.
-- V1 seeds SYNTHETIC data only, labeled synthetic=true throughout.
-- Run once against your Supabase project.
-- =============================================================

-- -----------------------------------------------------------------
-- Silver: cleaned forecast measurements with provenance attached.
-- measured_at carries the forecast INIT timestamp (per §2 lane table:
-- every silver table carries measured_at). init_date is a stored
-- generated column so filters on init date stay index-friendly.
-- calibration_id is NULL-able: model runs have no instrument chain.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_subseasonal_measurements (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id         UUID        NOT NULL REFERENCES sci_bronze_artifacts(id),
    lab_id              UUID        NOT NULL REFERENCES sci_labs(id),
    calibration_id      UUID        REFERENCES sci_calibrations(id),  -- NULL for model runs
    conditions          JSONB       NOT NULL,  -- {bias_correction, base_model}
    measured_at         TIMESTAMPTZ NOT NULL,  -- forecast init timestamp
    init_date           DATE        GENERATED ALWAYS AS (measured_at::date) STORED,
    model_source        TEXT        NOT NULL,  -- 'ecmwf_ifs' | 'graphcast' | 'pangu' | ...
    lead_weeks          INT         NOT NULL CHECK (lead_weeks BETWEEN 2 AND 6),
    ensemble_size       INT         NOT NULL CHECK (ensemble_size > 0),
    variable            TEXT        NOT NULL,  -- 't2m' | 'pr' | 'mslp' | ...
    region              TEXT        NOT NULL,  -- 'CONUS' | 'EU' | 'TROP' | 'NA' | ...
    crps                NUMERIC     NOT NULL CHECK (crps >= 0),
    skill_vs_climatology NUMERIC,              -- CRPSS vs climatology; NULL if uncomputed
    synthetic           BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sci_s2s_var_region_lead
    ON sci_subseasonal_measurements (variable, region, lead_weeks);
CREATE INDEX IF NOT EXISTS idx_sci_s2s_init_date
    ON sci_subseasonal_measurements (init_date);
CREATE INDEX IF NOT EXISTS idx_sci_s2s_lab
    ON sci_subseasonal_measurements (lab_id);

-- Lane condition-schema contract: bias_correction + base_model are required keys.
ALTER TABLE sci_subseasonal_measurements
    ADD CONSTRAINT IF NOT EXISTS chk_sci_s2s_conditions_keys
    CHECK (
        conditions ? 'bias_correction'
        AND conditions ? 'base_model'
    );

-- -----------------------------------------------------------------
-- Gold: versioned, benchmark-ready snapshot rows. Immutable: no UPDATE,
-- no DELETE — corrections propagate as new dataset versions via
-- sci_corrections + new rows here. History stays intact.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_subseasonal_gold (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id  UUID        NOT NULL REFERENCES sci_dataset_versions(id),
    silver_id           UUID        NOT NULL REFERENCES sci_subseasonal_measurements(id),
    model_source        TEXT        NOT NULL,
    init_date           DATE        NOT NULL,
    lead_weeks          INT         NOT NULL,
    ensemble_size       INT         NOT NULL,
    variable            TEXT        NOT NULL,
    region              TEXT        NOT NULL,
    crps                NUMERIC     NOT NULL,
    skill_vs_climatology NUMERIC,
    synthetic           BOOLEAN     NOT NULL DEFAULT FALSE,
    attestation_summary JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {reproduced, partial, failed}
    row_hash            TEXT        NOT NULL,  -- sha256 over the snapshot row payload
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset_version_id, silver_id)
);

CREATE INDEX IF NOT EXISTS idx_sci_s2s_gold_version
    ON sci_subseasonal_gold (dataset_version_id);
CREATE INDEX IF NOT EXISTS idx_sci_s2s_gold_var_region_lead
    ON sci_subseasonal_gold (variable, region, lead_weeks);

-- Immutability: gold rows are append-only. Any UPDATE/DELETE attempt fails.
CREATE OR REPLACE FUNCTION sci_subseasonal_block_gold_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'sci_subseasonal_gold is immutable: corrections create new dataset versions, never mutate history (TW-593)';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sci_s2s_gold_immutable ON sci_subseasonal_gold;
CREATE TRIGGER trg_sci_s2s_gold_immutable
    BEFORE UPDATE OR DELETE ON sci_subseasonal_gold
    FOR EACH ROW EXECUTE FUNCTION sci_subseasonal_block_gold_mutation();

-- RLS: science data served by API key, not org login. Default deny;
-- service-role key bypasses RLS. Lane-specific policies ship with the
-- integration pass (Dre).
ALTER TABLE sci_subseasonal_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_subseasonal_gold ENABLE ROW LEVEL SECURITY;

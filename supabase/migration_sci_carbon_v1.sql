-- =============================================================
-- TW-599 Wave 1 / CARBON lane V1: lane tables only.
-- Shared core tables (sci_labs, sci_calibrations, sci_bronze_artifacts,
-- sci_dataset_versions, sci_corrections, sci_manifests, sci_attestations,
-- sci_benchmarks, sci_benchmark_runs) are owned by the core migration
-- (migration_sci_core_v1.sql) and MUST run before this file.
--
-- Carbon sequestration evidence layer (Diego TW-600 spec): the harmonized
-- record that cross-walks soil-carbon measurements, flux-tower records,
-- biomass maps, biochar stability data, and MRV-method performance with
-- method-code comparability gating. No open layer like this exists; the
-- commercial registries own their certificates, nobody owns the
-- validation layer. The Verra 94%-phantom collapse is the design driver:
-- method codes are first-class and never hidden, model-only MRV claims
-- with no measurement anchor are excluded from gold by construction.
--
-- Method-code comparability gating (the trust mechanism):
--   * analytical_method is REQUIRED on every silver insert; 'unknown' or
--     missing -> quarantine to provenance_incomplete at ingest, never
--     silently mixed in.
--   * Gold promotion requires a real method code (never 'unknown').
--   * Walkley-Black and dry-combustion records are never silently pooled:
--     they carry different comparable_group assignments, and queries
--     surface the method code on every row.
--
-- MRV-BLIND: the blind predict-then-measure challenge for soil-carbon MRV
-- methods. Held-out plots, sealed measurements, scoring gated on
-- dry-combustion measurements at both endpoints. First falsifiable
-- scoreboard soil-carbon MRV has ever had.
--
-- V1 seeds SYNTHETIC data only, labeled synthetic=true throughout.
-- Run once against your Supabase project.
-- =============================================================

-- -----------------------------------------------------------------
-- Silver: harmonized sequestration evidence records. One table with a
-- record_class discriminator; class-specific fields are first-class
-- columns where they are queried (method code, soil, flux, biomass,
-- biochar annex, MRV-method) and JSONB overflow otherwise.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_carbon_measurements (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id         UUID        NOT NULL REFERENCES sci_bronze_artifacts(id),
    lab_id              UUID        NOT NULL REFERENCES sci_labs(id),
    calibration_id      UUID        REFERENCES sci_calibrations(id),  -- NULL for model/literature records
    conditions          JSONB       NOT NULL,  -- {sampling_protocol, depth_scheme, ...}
    measured_at         TIMESTAMPTZ NOT NULL,
    record_class        TEXT        NOT NULL CHECK (record_class IN (
                            'soil-profile', 'flux-tower', 'biomass-pixel',
                            'mrv-method-run', 'literature-extracted',
                            'registry-crosswalk')),
    carbon_pool         TEXT        NOT NULL CHECK (carbon_pool IN (
                            'soil-organic', 'aboveground-biomass',
                            'belowground-biomass', 'biochar-stable',
                            'harvested-wood', 'peat')),
    -- Method code: first-class, never hidden. The comparability gate.
    analytical_method   TEXT        NOT NULL CHECK (analytical_method IN (
                            'dry-combustion', 'walkley-black', 'loi',
                            'spectroscopy', 'ec-tower', 'lidar-agb',
                            'model-derived', 'registry-reported', 'unknown')),
    method_comparable_group TEXT,  -- NULL = not assigned to any comparable set
    region              TEXT        NOT NULL,
    site_id             TEXT,
    -- Soil-profile fields
    depth_top_cm        NUMERIC CHECK (depth_top_cm IS NULL OR depth_top_cm >= 0),
    depth_bottom_cm     NUMERIC CHECK (depth_bottom_cm IS NULL OR depth_bottom_cm > 0),
    soc_g_kg            NUMERIC CHECK (soc_g_kg IS NULL OR soc_g_kg >= 0),
    bulk_density_g_cm3  NUMERIC CHECK (bulk_density_g_cm3 IS NULL OR bulk_density_g_cm3 > 0),
    texture             TEXT,
    ph                  NUMERIC CHECK (ph IS NULL OR (ph >= 0 AND ph <= 14)),
    land_use            TEXT,
    management_history  TEXT,
    -- Flux-tower fields
    tower_id            TEXT,
    tower_doi           TEXT,
    nee_g_c_m2_yr       NUMERIC,
    gpp_g_c_m2_yr       NUMERIC,
    respiration_g_c_m2_yr NUMERIC,
    gap_filling_method  TEXT,
    -- Biomass-pixel fields
    biomass_source      TEXT CHECK (biomass_source IS NULL OR biomass_source IN ('gedi','cci','nfi')),
    agb_mg_ha           NUMERIC CHECK (agb_mg_ha IS NULL OR agb_mg_ha >= 0),
    agb_uncertainty_mg_ha NUMERIC,
    epoch               TEXT,
    uncertainty_framework TEXT,   -- 'gedi-v2' | 'cci-v6': misalignment recorded, not hidden
    -- Biochar stability annex
    feedstock           TEXT,
    pyrolysis_temp_c    NUMERIC,
    residence_time_min  NUMERIC,
    h_c_org_ratio       NUMERIC CHECK (h_c_org_ratio IS NULL OR h_c_org_ratio >= 0),
    aromatic_c_pct      NUMERIC CHECK (aromatic_c_pct IS NULL OR (aromatic_c_pct >= 0 AND aromatic_c_pct <= 100)),
    o_c_ratio           NUMERIC,
    ebc_cert_id         TEXT,
    persistence_100yr_pct NUMERIC CHECK (persistence_100yr_pct IS NULL OR (persistence_100yr_pct >= 0 AND persistence_100yr_pct <= 100)),
    persistence_method  TEXT,
    -- MRV-method-run fields
    mrv_method_class    TEXT CHECK (mrv_method_class IS NULL OR mrv_method_class IN (
                            'direct-sampling', 'daycent-modeled',
                            'remote-sensing-hybrid', 'registry-certified')),
    baseline_definition TEXT,
    additionality_claim TEXT,
    additionality_evidence_level TEXT,
    permanence_assumption TEXT,
    buffer_terms        TEXT,
    uncertainty_quantification TEXT,
    -- Class-specific overflow
    class_payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- QC flags
    baseline_credibility TEXT CHECK (baseline_credibility IS NULL OR baseline_credibility IN (
                            'measured', 'regional-standard', 'standardized-fiction', 'weak', 'none')),
    permanence_evidence TEXT CHECK (permanence_evidence IS NULL OR permanence_evidence IN (
                            'measured', 'assumed', 'buffered')),
    anomaly_flag        BOOLEAN     NOT NULL DEFAULT FALSE,
    synthetic           BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sci_carbon_class_pool
    ON sci_carbon_measurements (record_class, carbon_pool);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_method
    ON sci_carbon_measurements (analytical_method);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_region
    ON sci_carbon_measurements (region);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_lab
    ON sci_carbon_measurements (lab_id);

-- Lane condition-schema contract: sampling_protocol is a required key.
ALTER TABLE sci_carbon_measurements
    ADD CONSTRAINT IF NOT EXISTS chk_sci_carbon_conditions_keys
    CHECK (conditions ? 'sampling_protocol');

-- Gold promotion rule: enforced on the gold table itself — its
-- analytical_method column carries CHECK (analytical_method <> 'unknown'),
-- so 'unknown' can never be promoted (belt and suspenders with the ingest
-- quarantine).

-- -----------------------------------------------------------------
-- Gold: versioned, benchmark-ready snapshot rows. Immutable: no UPDATE,
-- no DELETE — corrections propagate as new dataset versions via
-- sci_corrections + new rows here. History stays intact.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_carbon_gold (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id  UUID        NOT NULL REFERENCES sci_dataset_versions(id),
    silver_id           UUID        NOT NULL REFERENCES sci_carbon_measurements(id),
    record_class        TEXT        NOT NULL,
    carbon_pool         TEXT        NOT NULL,
    analytical_method   TEXT        NOT NULL CHECK (analytical_method <> 'unknown'),
    method_comparable_group TEXT,
    region              TEXT        NOT NULL,
    site_id             TEXT,
    depth_top_cm        NUMERIC,
    depth_bottom_cm     NUMERIC,
    soc_g_kg            NUMERIC,
    bulk_density_g_cm3  NUMERIC,
    agb_mg_ha           NUMERIC,
    agb_uncertainty_mg_ha NUMERIC,
    uncertainty_framework TEXT,
    h_c_org_ratio       NUMERIC,
    aromatic_c_pct      NUMERIC,
    persistence_100yr_pct NUMERIC,
    mrv_method_class    TEXT,
    baseline_credibility TEXT,
    permanence_evidence TEXT,
    anomaly_flag        BOOLEAN     NOT NULL DEFAULT FALSE,
    synthetic           BOOLEAN     NOT NULL DEFAULT FALSE,
    attestation_summary JSONB       NOT NULL DEFAULT '{}'::jsonb,
    row_hash            TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset_version_id, silver_id)
);

CREATE INDEX IF NOT EXISTS idx_sci_carbon_gold_version
    ON sci_carbon_gold (dataset_version_id);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_gold_class_method
    ON sci_carbon_gold (record_class, analytical_method);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_gold_group
    ON sci_carbon_gold (method_comparable_group);

-- Immutability: gold rows are append-only.
CREATE OR REPLACE FUNCTION sci_carbon_block_gold_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'sci_carbon_gold is immutable: corrections create new dataset versions, never mutate history (TW-599)';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sci_carbon_gold_immutable ON sci_carbon_gold;
CREATE TRIGGER trg_sci_carbon_gold_immutable
    BEFORE UPDATE OR DELETE ON sci_carbon_gold
    FOR EACH ROW EXECUTE FUNCTION sci_carbon_block_gold_mutation();

-- RLS: science data served by API key, not org login. Default deny;
-- service-role key bypasses RLS. Lane-specific policies ship with the
-- integration pass (Dre).
ALTER TABLE sci_carbon_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_carbon_gold ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------
-- MRV-BLIND: blind predict-then-measure challenge plots. Held-out plots
-- with sealed endpoint measurements; scoring is gated on
-- dry_combustion_endpoints = TRUE (Diego TW-600 spec). known_reversion
-- plots are planted additionality-failure cases.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_carbon_blind_plots (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plot_code           TEXT        NOT NULL UNIQUE,
    region              TEXT        NOT NULL,
    soil_texture        TEXT,
    climate_zone        TEXT,
    management_history  TEXT,
    measurement_window_start DATE NOT NULL,
    measurement_window_end   DATE NOT NULL CHECK (measurement_window_end > measurement_window_start),
    sealed_measurement_hash TEXT NOT NULL,  -- sha256 of sealed endpoint measurements
    dry_combustion_endpoints BOOLEAN NOT NULL DEFAULT FALSE,
    known_reversion     BOOLEAN     NOT NULL DEFAULT FALSE,
    status              TEXT        NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'scoring', 'closed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sci_carbon_blind_status
    ON sci_carbon_blind_plots (status);

-- MRV-BLIND submissions. Eligibility (Diego TW-600 spec):
-- published SHA-256 training-data lineage is REQUIRED; holdout overlap
-- is declared via plot-and-date hashes and disqualifies at scoring.
CREATE TABLE IF NOT EXISTS sci_carbon_blind_submissions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_id        UUID        NOT NULL REFERENCES sci_benchmarks(id) ON DELETE RESTRICT,
    submitter           TEXT        NOT NULL,
    training_lineage_hash TEXT NOT NULL CHECK (training_lineage_hash ~ '^[0-9a-fA-F]{64}$'),
    holdout_declaration_hash TEXT NOT NULL,
    predictions         JSONB       NOT NULL,  -- [{plot_code, soc_change_mg_ha, interval_lo, interval_hi, nominal_confidence, flags_additionality_failure}]
    status              TEXT        NOT NULL DEFAULT 'submitted'
                        CHECK (status IN ('submitted', 'scored', 'disqualified')),
    disqualification_reason TEXT,
    interval_score      NUMERIC,
    coverage            NUMERIC,
    reversion_bonus     NUMERIC,
    total_score         NUMERIC,
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sci_carbon_blind_sub_benchmark
    ON sci_carbon_blind_submissions (benchmark_id);
CREATE INDEX IF NOT EXISTS idx_sci_carbon_blind_sub_status
    ON sci_carbon_blind_submissions (status);

ALTER TABLE sci_carbon_blind_plots ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_carbon_blind_submissions ENABLE ROW LEVEL SECURITY;

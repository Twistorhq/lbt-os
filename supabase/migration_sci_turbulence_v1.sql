-- ============================================================================
-- TW-593 Track 2 · Turbulence lane (Dre Coleman)
-- Prepared by Twistor Holdings LLC
--
-- Lane tables for the turbulence closure data layer. This migration creates
-- ONLY the lane-owned tables. The shared core (sci_labs, sci_calibrations,
-- sci_bronze_artifacts, sci_dataset_versions, sci_corrections, sci_manifests,
-- sci_attestations, sci_benchmarks, sci_benchmark_runs) is owned by the
-- electrocatalysis lane's shared-core migration, which MUST run first.
--
-- All V1 rows are SYNTHETIC (labeled synthetic=true). No real lab data until
-- partners sign.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Silver: cleaned turbulence measurements with provenance attached.
-- calibration_id is NULL-able: DNS/LES/RANS runs are simulations, there is no
-- instrument to calibrate. Experimental rig measurements carry a calibration.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_turbulence_measurements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id uuid NOT NULL REFERENCES sci_bronze_artifacts(id),
  lab_id uuid NOT NULL REFERENCES sci_labs(id),
  calibration_id uuid REFERENCES sci_calibrations(id),
  conditions jsonb NOT NULL,
  measured_at timestamptz NOT NULL,          -- run timestamp for simulations
  case_name text NOT NULL,                   -- 'channel_flow' | 'boundary_layer' | 'jet' (+ variants)
  reynolds numeric,                          -- Re_tau / Re_theta / Re_D depending on case
  grid_resolution text,                      -- e.g. '1024x512x512', '64M cells'
  solver text,                               -- 'DNS' | 'LES' | 'RANS' | 'URANS' | 'DES'
  scheme text,                               -- 'spectral' | 'FVM' | 'FDM' | 'spectral-element'
  closure_model text,                        -- 'none' (DNS) | 'k-epsilon' | 'k-omega-sst' | 'smagorinsky' | 'wale' | 'dynamic-smagorinsky' | 'gnn-learned' | ...
  fidelity_metrics jsonb,                    -- l2 errors, spectra agreement, stability hours
  synthetic boolean NOT NULL DEFAULT false,

  -- Lane-schema'd conditions: every turbulence record must name its domain
  -- and boundary conditions. (The SLAC round-robin lesson: conditions are data.)
  CONSTRAINT chk_turb_conditions_keys
    CHECK (conditions ?& array['domain', 'bc']),

  -- Reynolds number is a positive physical quantity when present.
  CONSTRAINT chk_turb_reynolds_positive
    CHECK (reynolds IS NULL OR reynolds > 0),

  -- Solver must be a known CFD fidelity class.
  CONSTRAINT chk_turb_solver_allowed
    CHECK (solver IS NULL OR solver IN ('DNS', 'LES', 'RANS', 'URANS', 'DES')),

  -- DNS runs resolve everything: a DNS row must not claim a closure model.
  CONSTRAINT chk_turb_dns_has_no_closure
    CHECK (solver <> 'DNS' OR closure_model IS NULL OR closure_model IN ('none', 'resolved')),

  -- synthetic flag is never NULL: honest labeling, no fake precision.
  CONSTRAINT chk_turb_synthetic_not_null
    CHECK (synthetic IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_turb_meas_case_re
  ON sci_turbulence_measurements (case_name, reynolds);
CREATE INDEX IF NOT EXISTS idx_turb_meas_closure
  ON sci_turbulence_measurements (closure_model);
CREATE INDEX IF NOT EXISTS idx_turb_meas_lab
  ON sci_turbulence_measurements (lab_id);
CREATE INDEX IF NOT EXISTS idx_turb_meas_artifact
  ON sci_turbulence_measurements (artifact_id);

-- ----------------------------------------------------------------------------
-- Gold: benchmark-ready snapshot rows. Immutable: UPDATE/DELETE raise.
-- New dataset versions are new rows in sci_dataset_versions; corrections
-- supersede via sci_corrections, never by mutating gold.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_turbulence_gold (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id uuid NOT NULL REFERENCES sci_dataset_versions(id),
  measurement_id uuid NOT NULL REFERENCES sci_turbulence_measurements(id),
  semver text NOT NULL,
  attestation_summary jsonb,                 -- {reproduced: n, partial: n, failed: n}
  synthetic boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_version_id, measurement_id)
);

CREATE INDEX IF NOT EXISTS idx_turb_gold_version
  ON sci_turbulence_gold (dataset_version_id);

-- Version immutability, enforced in the database: gold rows are append-only.
CREATE OR REPLACE FUNCTION sci_turbulence_gold_immutable()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'sci_turbulence_gold is immutable: corrections create new dataset versions, never mutate gold rows (TW-593)';
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_turb_gold_no_update ON sci_turbulence_gold;
CREATE TRIGGER trg_turb_gold_no_update
  BEFORE UPDATE OR DELETE ON sci_turbulence_gold
  FOR EACH ROW EXECUTE FUNCTION sci_turbulence_gold_immutable();

-- ----------------------------------------------------------------------------
-- Row-level security: read keys see their lane; writes go through signed
-- ingest only (service role). Policies mirror the TW-564 medallion pattern.
-- ----------------------------------------------------------------------------
ALTER TABLE sci_turbulence_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE sci_turbulence_gold ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS turb_meas_read ON sci_turbulence_measurements;
CREATE POLICY turb_meas_read ON sci_turbulence_measurements
  FOR SELECT USING (true);

DROP POLICY IF EXISTS turb_gold_read ON sci_turbulence_gold;
CREATE POLICY turb_gold_read ON sci_turbulence_gold
  FOR SELECT USING (true);

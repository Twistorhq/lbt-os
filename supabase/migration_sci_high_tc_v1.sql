-- Prepared by Twistor Holdings LLC
-- TW-593 Track 2 · high-Tc lane · silver + gold tables (V1)
--
-- ORDERING: apply AFTER the shared science core migration
-- (migration_sci_core_v1.sql, owned by the electrocatalysis lane), which
-- creates sci_labs, sci_calibrations, sci_bronze_artifacts,
-- sci_dataset_versions, sci_corrections, sci_benchmarks. This file creates
-- ONLY high_tc lane tables and references the shared tables by FK.
--
-- SYNTHETIC V1: lab_id / calibration_id are NULLABLE so the synthetic seed
-- (synthetic=true rows from seed.py) can land before a "twistor-synthetic"
-- lab identity is registered in sci_labs by Dre's integration pass.
-- Verified-lab ingestion MUST populate both; the router enforces that.

-- ---------------------------------------------------------------------------
-- Silver: cleaned high-Tc measurements with provenance attached.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_hightc_measurements (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id     uuid NOT NULL REFERENCES sci_bronze_artifacts(id),
  lab_id          uuid REFERENCES sci_labs(id),
  calibration_id  uuid REFERENCES sci_calibrations(id),
  conditions      jsonb NOT NULL DEFAULT '{}'::jsonb,
  measured_at     timestamptz NOT NULL DEFAULT now(),
  composition     text NOT NULL CHECK (char_length(composition) > 0),
  structure       text NOT NULL CHECK (structure IN (
                    'cuprate', 'iron_pnictide', 'iron_chalcogenide',
                    'hydride', 'nickelate', 'perovskite', 'mgb2', 'conventional'
                  )),
  synthesis_route text,
  tc_k            numeric NOT NULL CHECK (tc_k > 0 AND tc_k < 400),
  measurement_method text NOT NULL CHECK (measurement_method IN (
                    'resistivity', 'susceptibility', 'specific_heat', 'other'
                  )),
  pressure_gpa    numeric NOT NULL DEFAULT 0 CHECK (pressure_gpa >= 0),
  synthetic       boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hightc_meas_structure
  ON sci_hightc_measurements (structure);
CREATE INDEX IF NOT EXISTS idx_hightc_meas_tc
  ON sci_hightc_measurements (tc_k);
CREATE INDEX IF NOT EXISTS idx_hightc_meas_pressure
  ON sci_hightc_measurements (pressure_gpa);
CREATE INDEX IF NOT EXISTS idx_hightc_meas_artifact
  ON sci_hightc_measurements (artifact_id);
CREATE INDEX IF NOT EXISTS idx_hightc_meas_synthetic
  ON sci_hightc_measurements (synthetic);

-- ---------------------------------------------------------------------------
-- Gold: benchmark-ready snapshot rows. IMMUTABLE — version bumps only.
-- Denormalized so the gold query endpoint never joins on the hot path.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_hightc_gold (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id uuid NOT NULL REFERENCES sci_dataset_versions(id),
  silver_id         uuid NOT NULL REFERENCES sci_hightc_measurements(id),
  composition       text NOT NULL,
  structure         text NOT NULL,
  tc_k              numeric NOT NULL,
  pressure_gpa      numeric NOT NULL DEFAULT 0,
  measurement_method text NOT NULL,
  synthetic         boolean NOT NULL DEFAULT false,
  provenance_hash   text NOT NULL,   -- sha256 over silver row + artifact hash
  attestation_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (dataset_version_id, silver_id)
);

CREATE INDEX IF NOT EXISTS idx_hightc_gold_version
  ON sci_hightc_gold (dataset_version_id);
CREATE INDEX IF NOT EXISTS idx_hightc_gold_structure
  ON sci_hightc_gold (structure);
CREATE INDEX IF NOT EXISTS idx_hightc_gold_tc
  ON sci_hightc_gold (tc_k);
CREATE INDEX IF NOT EXISTS idx_hightc_gold_pressure
  ON sci_hightc_gold (pressure_gpa);

-- Immutability guard: gold rows are append-only. Corrections land as new
-- dataset versions (see sci_corrections + supersedes_id in the shared core).
CREATE OR REPLACE FUNCTION sci_hightc_gold_no_mutate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'sci_hightc_gold is immutable: issue a correction and mint a new dataset version';
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_hightc_gold_no_mutate ON sci_hightc_gold;
CREATE TRIGGER trg_hightc_gold_no_mutate
  BEFORE UPDATE OR DELETE ON sci_hightc_gold
  FOR EACH ROW EXECUTE FUNCTION sci_hightc_gold_no_mutate();

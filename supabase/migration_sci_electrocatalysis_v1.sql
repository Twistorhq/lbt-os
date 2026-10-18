-- =====================================================================
-- TW-593 Track 2 · Scientific Data Layer V1 · Electrocatalysis lane
-- Prepared by Twistor Holdings LLC
--
-- Lane 1 (breakthrough-lanes, 83/100): electrocatalysts that close the
-- carbon cycle. The reference implementation — the other five lanes copy
-- this shape.
--
-- Design driver: the SLAC four-lab round-robin. Measurement conditions
-- (temp, pressure, electrolyte, pH, solvent, cell type — "how hard the
-- mixture was shaken") are first-class data, not metadata.
--
-- V1 seeds SYNTHETIC data only, synthetic=true stamped. No real lab data
-- until partners sign.
-- =====================================================================

-- Silver: cleaned measurements with provenance attached. Every row carries
-- the full chain: artifact (raw, hashed, signed) → lab → calibration.
CREATE TABLE IF NOT EXISTS sci_electro_measurements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Provenance (§2): every silver row names its bronze artifact, lab, and
  -- the calibration event behind the instrument that made it.
  artifact_id uuid NOT NULL REFERENCES sci_bronze_artifacts(id) ON DELETE RESTRICT,
  lab_id uuid NOT NULL REFERENCES sci_labs(id) ON DELETE RESTRICT,
  calibration_id uuid REFERENCES sci_calibrations(id) ON DELETE RESTRICT,

  -- Conditions (§1: conditions are data, not metadata). Lane-schema'd:
  --   temp_c, pressure_bar, electrolyte, ph, solvent, cell_type
  -- plus lane-specific extras (stirring_rpm — the round-robin ghost).
  conditions JSONB NOT NULL,
  measured_at timestamptz,

  -- Lane value columns.
  composition text,              -- e.g. 'Fe70Bi15Ni10Sn3Zn2'
  surface_facet text,            -- e.g. '(111)', '(100)', 'polycrystalline'
  adsorbate text,                -- '*CO2', '*CO', '*N2', '*H', ...
  dft_adsorption_ev numeric,     -- computed adsorption energy (eV)
  experimental_tof numeric,      -- turnover frequency, s^-1 (experimental)
  faradaic_efficiency numeric,   -- % (0–100)
  synthetic boolean NOT NULL DEFAULT true, -- V1: synthetic until partners sign

  created_at timestamptz NOT NULL DEFAULT now(),

  CHECK (faradaic_efficiency IS NULL OR (faradaic_efficiency >= 0 AND faradaic_efficiency <= 100)),
  CHECK (conditions ? 'temp_c'),
  CHECK (conditions ? 'pressure_bar'),
  CHECK (conditions ? 'electrolyte'),
  CHECK (conditions ? 'ph'),
  CHECK (conditions ? 'solvent'),
  CHECK (conditions ? 'cell_type')
);
CREATE INDEX IF NOT EXISTS idx_sci_electro_artifact
  ON sci_electro_measurements (artifact_id);
CREATE INDEX IF NOT EXISTS idx_sci_electro_lab
  ON sci_electro_measurements (lab_id);
CREATE INDEX IF NOT EXISTS idx_sci_electro_composition
  ON sci_electro_measurements (composition);
CREATE INDEX IF NOT EXISTS idx_sci_electro_adsorbate
  ON sci_electro_measurements (adsorbate);

-- Gold: benchmark-ready mart rows. Each gold row snapshots a silver
-- measurement into a pinned dataset version with an attestation summary.
CREATE TABLE IF NOT EXISTS sci_electro_gold (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  silver_id uuid NOT NULL REFERENCES sci_electro_measurements(id) ON DELETE RESTRICT,
  dataset_version_id uuid NOT NULL REFERENCES sci_dataset_versions(id) ON DELETE RESTRICT,

  -- Snapshot of the measurement values that matter for the benchmark.
  composition text,
  surface_facet text,
  adsorbate text,
  faradaic_efficiency numeric,
  experimental_tof numeric,

  -- Attestation weight (§3.4): how many independent labs reproduced this
  -- cohort. Served honestly — unattested rows are labeled, not hidden.
  attestation_summary JSONB NOT NULL DEFAULT '{"reproduced":0,"partial":0,"failed":0}'::jsonb,

  row_hash text NOT NULL,        -- sha256 over the snapshotted values
  synthetic boolean NOT NULL DEFAULT true,

  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sci_electro_gold_version
  ON sci_electro_gold (dataset_version_id);
CREATE INDEX IF NOT EXISTS idx_sci_electro_gold_composition
  ON sci_electro_gold (composition);

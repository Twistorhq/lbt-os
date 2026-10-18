-- =====================================================================
-- TW-593 Track 2 · Scientific Data Layer V1 · Shared core migration
-- Prepared by Twistor Holdings LLC
--
-- Shared tables (§2 of the architecture doc): one core, all six lanes.
-- Medallion: sci_bronze_artifacts → per-lane silver measurements → gold
-- dataset snapshots (sci_dataset_versions). Immutable snapshots, corrections
-- as supersession, independent attestations, reproducibility manifests,
-- blind benchmarks.
--
-- Design driver: the SLAC four-lab round-robin failure — "how hard the
-- mixture was shaken" broke reproducibility. So calibration chains, lab
-- identity with signing keys, and conditions-as-data are first-class.
--
-- PII doctrine (TW-552): researcher names / lab contact info are hashed at
-- ingestion in bronze and masked in silver/gold. No PII-shaped columns here.
-- =====================================================================

-- Who produced the data. Labs register once, get a signing key.
CREATE TABLE IF NOT EXISTS sci_labs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('academic','national_lab','industry','independent')),
  verification_status text NOT NULL DEFAULT 'pending'
    CHECK (verification_status IN ('pending','verified','suspended')),
  public_key text,               -- ed25519, for signed ingestion receipts
  contact_hashed text,           -- TW-552: hashed at ingestion, never raw
  created_at timestamptz NOT NULL DEFAULT now()
);

-- The instrument chain behind a measurement. A measurement without its
-- calibration is noise (the SLAC round-robin lesson).
CREATE TABLE IF NOT EXISTS sci_calibrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lab_id uuid NOT NULL REFERENCES sci_labs(id) ON DELETE RESTRICT,
  instrument_id text NOT NULL,   -- lab's own instrument identifier
  instrument_kind text NOT NULL, -- 'potentiostat' | 'gc_ms' | 'dns_solver' | ...
  calibrated_at timestamptz NOT NULL,
  valid_until timestamptz,
  standard_ref text NOT NULL,    -- what it was calibrated against
  calibration_payload_hash text, -- hash of the calibration certificate
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sci_calibrations_lab
  ON sci_calibrations (lab_id);

-- Bronze: raw artifacts, immutable, content-hashed, signed. Never updated.
CREATE TABLE IF NOT EXISTS sci_bronze_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,            -- 'electrocatalysis' | 'subseasonal' | ...
  lab_id uuid NOT NULL REFERENCES sci_labs(id) ON DELETE RESTRICT,
  storage_path text NOT NULL,
  content_hash text NOT NULL,    -- sha256 of raw payload
  collector_signature text,      -- lab's ed25519 signature over content_hash
  collected_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  schema_version int NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sci_bronze_lane
  ON sci_bronze_artifacts (lane);
CREATE INDEX IF NOT EXISTS idx_sci_bronze_lab
  ON sci_bronze_artifacts (lab_id);

-- Gold: versioned, benchmark-ready dataset snapshots. APPEND-ONLY.
-- New version = new row, never an UPDATE. Enforced by trigger below.
CREATE TABLE IF NOT EXISTS sci_dataset_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,
  dataset_name text NOT NULL,
  semver text NOT NULL,          -- '1.0.0'; new version = new row, never update
  row_count int NOT NULL CHECK (row_count >= 0),
  content_root_hash text NOT NULL, -- merkle root over the snapshot's row hashes
  benchmark_pin text,            -- which benchmark this version feeds, if any
  doi text,                     -- minted on Justynn's call (V1: placeholders)
  supersedes_id uuid REFERENCES sci_dataset_versions(id) ON DELETE RESTRICT,
  released_at timestamptz NOT NULL DEFAULT now(),
  release_notes text NOT NULL,
  UNIQUE (lane, dataset_name, semver)
);

-- Immutability enforcement: a published dataset snapshot is a scholarly
-- object. If it's wrong, you supersede it — you never rewrite it.
CREATE OR REPLACE FUNCTION sci_block_dataset_version_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'sci_dataset_versions is append-only: release a new version instead of updating %', OLD.id;
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_sci_dataset_versions_immutable ON sci_dataset_versions;
CREATE TRIGGER trg_sci_dataset_versions_immutable
  BEFORE UPDATE OR DELETE ON sci_dataset_versions
  FOR EACH ROW EXECUTE FUNCTION sci_block_dataset_version_mutation();

-- Corrections: errata that supersede records. Never mutate history — a
-- correction points at the silver row and at the NEW dataset version that
-- carries the corrected snapshot.
CREATE TABLE IF NOT EXISTS sci_corrections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,
  corrects_silver_table text NOT NULL,
  corrects_silver_id uuid NOT NULL,
  reason text NOT NULL,
  corrected_values JSONB NOT NULL,
  corrected_by uuid REFERENCES sci_labs(id) ON DELETE RESTRICT,
  corrected_at timestamptz NOT NULL DEFAULT now(),
  new_dataset_version_id uuid REFERENCES sci_dataset_versions(id) ON DELETE RESTRICT
                               -- the version that carries the correction
);
CREATE INDEX IF NOT EXISTS idx_sci_corrections_silver
  ON sci_corrections (lane, corrects_silver_table, corrects_silver_id);
CREATE INDEX IF NOT EXISTS idx_sci_corrections_version
  ON sci_corrections (new_dataset_version_id);

-- Reproducibility: pin data + code + env per published result.
CREATE TABLE IF NOT EXISTS sci_manifests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,
  result_name text NOT NULL,
  dataset_version_id uuid NOT NULL REFERENCES sci_dataset_versions(id) ON DELETE RESTRICT,
  code_ref text NOT NULL,        -- git sha / container digest
  env_hash text NOT NULL,        -- hashed environment spec
  run_params JSONB NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Independent quality attestations: re-measurement by a SECOND lab.
-- The trust signal of the whole data layer.
--
-- APP-LEVEL CONSTRAINT (enforced by trigger below + application code):
--   attestor_lab_id != originator lab_id. The architecture doc's SQL sketch
--   ("SELECT ... FROM pg_temp") is not valid DDL, so the rule is implemented
--   here as a trigger that resolves the originator lab through
--   <silver_table>.artifact_id → sci_bronze_artifacts.lab_id. A self-
--   attestation is a lab vouching for itself; the trigger rejects it.
CREATE TABLE IF NOT EXISTS sci_attestations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,
  attests_silver_table text NOT NULL,
  attests_silver_id uuid NOT NULL,
  attestor_lab_id uuid NOT NULL REFERENCES sci_labs(id) ON DELETE RESTRICT,
  verdict text NOT NULL CHECK (verdict IN ('reproduced','partial','failed')),
  evidence_hash text,            -- hash of the re-measurement artifact
  attested_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sci_attestations_silver
  ON sci_attestations (lane, attests_silver_table, attests_silver_id);

CREATE OR REPLACE FUNCTION sci_check_attestor_is_independent()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  origin_lab uuid;
BEGIN
  -- Resolve the originator lab of the attested silver row.
  -- Silver tables all carry artifact_id → sci_bronze_artifacts(lab_id).
  EXECUTE format(
    'SELECT b.lab_id FROM %I s JOIN sci_bronze_artifacts b ON b.id = s.artifact_id WHERE s.id = $1',
    NEW.attests_silver_table
  ) INTO origin_lab USING NEW.attests_silver_id;

  IF origin_lab IS NULL THEN
    RAISE EXCEPTION 'sci_attestations: attested silver row %.% not found',
      NEW.attests_silver_table, NEW.attests_silver_id;
  END IF;

  IF origin_lab = NEW.attestor_lab_id THEN
    RAISE EXCEPTION 'sci_attestations: attestor lab % must differ from originator lab % (self-attestation rejected)',
      NEW.attestor_lab_id, origin_lab;
  END IF;

  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_sci_attestor_independent ON sci_attestations;
CREATE TRIGGER trg_sci_attestor_independent
  BEFORE INSERT OR UPDATE ON sci_attestations
  FOR EACH ROW EXECUTE FUNCTION sci_check_attestor_is_independent();

-- Blind benchmarks: the CASP analog per lane. The withheld answers are
-- hashed at release (hidden_split_hash); no manifest, no leaderboard entry.
CREATE TABLE IF NOT EXISTS sci_benchmarks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lane text NOT NULL,
  name text NOT NULL,            -- 'catbench_v1' | 's2s_quest_v1' | ...
  dataset_version_id uuid NOT NULL REFERENCES sci_dataset_versions(id) ON DELETE RESTRICT,
  metric text NOT NULL,          -- 'faradaic_efficiency_mae' | 'crps_skill' | ...
  hidden_split_hash text NOT NULL, -- the withheld answers, hashed at release
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lane, name)
);

CREATE TABLE IF NOT EXISTS sci_benchmark_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  benchmark_id uuid NOT NULL REFERENCES sci_benchmarks(id) ON DELETE RESTRICT,
  submitter text NOT NULL,       -- team / model name
  score numeric NOT NULL,
  manifest_id uuid REFERENCES sci_manifests(id) ON DELETE RESTRICT,
                             -- reproducibility required for leaderboard
  submitted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sci_benchmark_runs_benchmark
  ON sci_benchmark_runs (benchmark_id);

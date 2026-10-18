-- Prepared by Twistor Holdings LLC
-- TW-593 Track 2 (scientific data layer), PFAS lane V1 - Dre's crew, TW-595.
--
-- LANE TABLES ONLY. The shared core migration (sci_labs, sci_calibrations,
-- sci_bronze_artifacts, sci_dataset_versions, sci_corrections, sci_manifests,
-- sci_attestations, sci_benchmarks, sci_benchmark_runs) is owned by the
-- electrocatalysis lane and MUST be applied first. Every FK below references it.
--
-- First benchmark concept (method-comparison across destruction methods):
--   name   : pfas_method_bench_v1
--   metric : destruction_pct_mae (mean absolute error on held-out destruction_pct)
--   status : open (concept - pins to the first released dataset version)
-- The row itself is seeded by backend/app/data_layer/science/pfas/seed.py
-- (PFAS_METHOD_BENCHMARK) once the shared sci_benchmarks table exists.
-- V1 seeds SYNTHETIC data only, labeled synthetic=true. No real lab data
-- until partners sign.

-- ---------------------------------------------------------------------------
-- Silver: cleaned PFAS destruction measurements with full provenance.
-- Every row traces: measurement -> bronze artifact (content hash + lab
-- signature) -> lab identity -> calibration event -> calibration standard.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sci_pfas_measurements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id uuid NOT NULL REFERENCES public.sci_bronze_artifacts(id),
  lab_id uuid NOT NULL REFERENCES public.sci_labs(id),
  calibration_id uuid REFERENCES public.sci_calibrations(id),
  conditions jsonb NOT NULL,
  measured_at timestamptz NOT NULL,
  compound text NOT NULL,
  destruction_method text NOT NULL,
  destruction_pct numeric NOT NULL,
  analytical_method text NOT NULL,
  detection_limit_ppt numeric NOT NULL,
  synthetic boolean NOT NULL DEFAULT false,
  superseded_by uuid REFERENCES public.sci_pfas_measurements(id),
  created_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT sci_pfas_compound_ck CHECK (compound IN (
    'PFOA', 'PFOS', 'GenX', 'PFHxS', 'PFNA', 'PFDA', 'PFBS',
    'PFHpA', 'PFOSA', '6:2 FTS', 'PFBA', 'PFPeA'
  )),
  CONSTRAINT sci_pfas_method_ck CHECK (destruction_method IN (
    'thermal', 'electrochemical', 'plasma', 'sonochemical',
    'photocatalytic', 'supercritical_water_oxidation',
    'hydrothermal', 'mechanochemical'
  )),
  CONSTRAINT sci_pfas_pct_ck CHECK (destruction_pct >= 0 AND destruction_pct <= 100),
  CONSTRAINT sci_pfas_analytical_ck CHECK (analytical_method IN (
    'LC-MS/MS', 'LC-HRMS', 'QTOF-MS', 'IC', 'GC-MS', 'TOF-SIMS', '19F-NMR'
  )),
  CONSTRAINT sci_pfas_dl_ck CHECK (detection_limit_ppt > 0),
  -- Conditions are data, not metadata (the "how hard the mixture was shaken"
  -- lesson). Lane schema requires temp_c, residence_s, matrix on every row.
  CONSTRAINT sci_pfas_conditions_ck CHECK (
    (conditions ? 'temp_c')
    AND (conditions ? 'residence_s')
    AND (conditions ? 'matrix')
  ),
  CONSTRAINT sci_pfas_no_self_supersede CHECK (superseded_by IS DISTINCT FROM id)
);

CREATE INDEX IF NOT EXISTS sci_pfas_measurements_compound_idx
  ON public.sci_pfas_measurements (compound);
CREATE INDEX IF NOT EXISTS sci_pfas_measurements_method_idx
  ON public.sci_pfas_measurements (destruction_method);
CREATE INDEX IF NOT EXISTS sci_pfas_measurements_artifact_idx
  ON public.sci_pfas_measurements (artifact_id);
CREATE INDEX IF NOT EXISTS sci_pfas_measurements_lab_idx
  ON public.sci_pfas_measurements (lab_id);
CREATE INDEX IF NOT EXISTS sci_pfas_measurements_synthetic_idx
  ON public.sci_pfas_measurements (synthetic) WHERE synthetic = true;
CREATE INDEX IF NOT EXISTS sci_pfas_measurements_active_idx
  ON public.sci_pfas_measurements (compound, destruction_method)
  WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- Gold: benchmark-ready snapshot rows. Immutable by trigger below.
-- One row per (dataset_version, measurement). Corrections never mutate:
-- they supersede the silver row and land in a NEW dataset version.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sci_pfas_gold (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  measurement_id uuid NOT NULL REFERENCES public.sci_pfas_measurements(id),
  dataset_version_id uuid NOT NULL REFERENCES public.sci_dataset_versions(id),
  compound text NOT NULL,
  destruction_method text NOT NULL,
  destruction_pct numeric NOT NULL,
  attestation_summary jsonb NOT NULL
    DEFAULT '{"reproduced": 0, "partial": 0, "failed": 0}'::jsonb,
  synthetic boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_version_id, measurement_id)
);

CREATE INDEX IF NOT EXISTS sci_pfas_gold_version_idx
  ON public.sci_pfas_gold (dataset_version_id);
CREATE INDEX IF NOT EXISTS sci_pfas_gold_compound_method_idx
  ON public.sci_pfas_gold (compound, destruction_method);

-- Version immutability: gold rows are append-only. A correction creates a new
-- dataset version with new gold rows; history is never rewritten.
CREATE OR REPLACE FUNCTION public.sci_pfas_gold_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'sci_pfas_gold is immutable: issue a correction and cut a new dataset version (TW-593)';
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS sci_pfas_gold_no_mutation ON public.sci_pfas_gold;
CREATE TRIGGER sci_pfas_gold_no_mutation
  BEFORE UPDATE OR DELETE ON public.sci_pfas_gold
  FOR EACH ROW EXECUTE FUNCTION public.sci_pfas_gold_immutable();

-- Row-level security: read open to the science API role, writes server-side only.
ALTER TABLE public.sci_pfas_measurements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sci_pfas_gold ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sci_pfas_measurements_read ON public.sci_pfas_measurements;
CREATE POLICY sci_pfas_measurements_read ON public.sci_pfas_measurements
  FOR SELECT USING (true);

DROP POLICY IF EXISTS sci_pfas_gold_read ON public.sci_pfas_gold
...[truncated 672 chars]
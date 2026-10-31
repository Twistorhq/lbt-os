-- Prepared by Twistor Holdings LLC
-- TW-838 · quantum lane · silver tables (V1)
--
-- ORDERING: apply AFTER the shared science core migration
-- (migration_sci_core_v1.sql), which creates sci_labs, sci_calibrations,
-- sci_bronze_artifacts, sci_dataset_versions. This file creates ONLY
-- quantum lane tables and references the shared tables by FK.
--
-- Lane shape (TW-838 brief, Section 5): bronze holds verbatim vendor
-- payloads (file store partitioned vendor/date/device, indexed in
-- sci_bronze_artifacts with lane='quantum'); silver is ONE observation
-- schema across vendors; vendor-published marketing specifications land
-- in a parallel table with a provenance flag and are never mixed with
-- measured telemetry.
--
-- Legal basis (Angela terms read, TW-838, 2026-10-30): DERIVED-ONLY.
-- Raw calibration values are internal only at every price point; gold
-- serves genuinely computed aggregates, drift features, and indexes.
-- Production collection runs under an IBM Cloud Pay-As-You-Go account
-- (CSA basis); the Open Plan (EUA 2(d)) is code-testing scale only.
--
-- PII: none exists in any pipe in this lane (machine telemetry and
-- published device specifications only). Nothing to hash or mask; the
-- medallion applies for lineage.

-- ---------------------------------------------------------------------------
-- Silver: normalized calibration observations, one row per
-- (device, qubit_or_edge, metric, measured_at) observation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_quantum_observations (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id       uuid NOT NULL REFERENCES sci_bronze_artifacts(id),
  calibration_id    uuid REFERENCES sci_calibrations(id),
  vendor            text NOT NULL CHECK (char_length(vendor) > 0),
  device            text NOT NULL CHECK (char_length(device) > 0),
  device_generation text,
  qubit_or_edge     text NOT NULL,
  metric            text NOT NULL CHECK (char_length(metric) > 0),
  value             double precision NOT NULL,
  unit              text,
  measured_at       timestamptz NOT NULL,
  source_metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quantum_obs_device_metric
  ON sci_quantum_observations (vendor, device, metric);
CREATE INDEX IF NOT EXISTS idx_quantum_obs_measured
  ON sci_quantum_observations (measured_at);
CREATE INDEX IF NOT EXISTS idx_quantum_obs_artifact
  ON sci_quantum_observations (artifact_id);

-- Deny-all RLS posture, consistent with TW-710: no policies, service
-- role only. The paid tier serves DERIVED gold, never this table raw.
ALTER TABLE sci_quantum_observations ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Silver (parallel): vendor-published specification snapshots.
-- Marketing-grade values with provenance, kept apart from measurements.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_quantum_spec_snapshots (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id  uuid NOT NULL REFERENCES sci_bronze_artifacts(id),
  vendor       text NOT NULL,
  device       text NOT NULL,
  metric       text NOT NULL CHECK (char_length(metric) > 0),
  value        double precision NOT NULL,
  unit         text,
  provenance   text NOT NULL CHECK (provenance IN ('marketing', 'vendor_spec')),
  source_url   text NOT NULL,
  retrieved_at timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quantum_spec_device
  ON sci_quantum_spec_snapshots (vendor, device);

ALTER TABLE sci_quantum_spec_snapshots ENABLE ROW LEVEL SECURITY;

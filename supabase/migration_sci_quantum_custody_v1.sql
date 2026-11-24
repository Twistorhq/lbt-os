-- Prepared by Twistor Holdings LLC
-- TW-1329 · quantum custody layer · jobs, backend snapshots, alerts (V1)
--
-- ORDERING: apply AFTER migration_sci_core_v1.sql and
-- migration_sci_quantum_v1.sql. This file creates ONLY the custody
-- tables and references the shared bronze table by FK.
--
-- Why this exists (TW-1328 exit plan, TW-1329 build): the IBM Cloud
-- deactivation of 2026-11-04 put provider-side QPU history at risk of
-- deletion with the account. The custody rule from TW-1328: the quantum
-- provider is a calculator, not a filing cabinet. Nothing of record
-- lives provider-side. These tables are the system of record for the
-- job lifecycle that enforces the rule:
--
--   1. INTENT FIRST: a job row is written BEFORE submission, and the
--      provider job ID is recorded the moment submission returns. A job
--      we cannot name is a job we cannot recover.
--   2. PULL ON COMPLETION: result payload, job metadata, and a fresh
--      backend snapshot land in bronze (sci_bronze_artifacts) and the
--      job is marked custodied in the same run. Raw payloads stay
--      verbatim in bronze; this table carries the lifecycle and the
--      provenance, never a rewritten copy of the payload.
--   3. RECONCILIATION: the 15-minute sweep and the daily provider-side
--      diff read and write these statuses; a gap is a row, not a rumor.
--   4. ALERTS ARE ROWS: provider auth failures are recorded here AND
--      paged the same day (see the runbook: family board, owner
--      Marcus). A silent pause like TW-838's is a defect, not a state.
--
-- Store of record: Supabase Postgres (founder decision, TW-1329).
-- Custody of the store itself: nightly pg_dump export to
-- Twistor-controlled storage outside Supabase with a weekly hash
-- verification, so an exit from Supabase is a restore, not a
-- negotiation. The export mechanics live in the collector repo
-- (backend/scripts/quantum_custody_export.sh), not in this migration.
--
-- PII: none. Machine telemetry, job metadata, published device data.

-- ---------------------------------------------------------------------------
-- Job lifecycle: one row per quantum job, written at intent time.
-- status flow: intent -> submitted -> running -> completed -> custodied
--              submitted/running -> failed (provider-terminal)
--              any pre-custodied state -> orphaned (provider access lost
--              before custody; the circuit survives in circuit_qasm, so
--              the job can be re-run on the fallback provider)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_quantum_jobs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider            text NOT NULL CHECK (char_length(provider) > 0),
  backend             text NOT NULL CHECK (char_length(backend) > 0),
  provider_job_id     text,
  circuit_qasm        text NOT NULL,
  circuit_sha256      text NOT NULL,
  -- shots is the submitted shot count. 0 marks a row recovered by the
  -- daily diff (mechanic 4): a provider-side job whose submission we
  -- never witnessed, so its shot count is unknown, never invented.
  shots               int NOT NULL CHECK (shots >= 0),
  status              text NOT NULL DEFAULT 'intent'
    CHECK (status IN ('intent', 'submitted', 'running', 'completed',
                      'custodied', 'failed', 'orphaned')),
  intent_recorded_at  timestamptz NOT NULL DEFAULT now(),
  submitted_at        timestamptz,
  completed_at        timestamptz,
  custodied_at        timestamptz,
  result_artifact_id  uuid REFERENCES sci_bronze_artifacts(id),
  snapshot_artifact_id uuid REFERENCES sci_bronze_artifacts(id),
  job_metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

-- A provider job ID names exactly one job in our store. Partial index:
-- intent rows have no provider ID yet and must not collide on NULL.
CREATE UNIQUE INDEX IF NOT EXISTS uq_quantum_jobs_provider_job
  ON sci_quantum_jobs (provider, provider_job_id)
  WHERE provider_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quantum_jobs_status
  ON sci_quantum_jobs (status);
CREATE INDEX IF NOT EXISTS idx_quantum_jobs_provider_backend
  ON sci_quantum_jobs (provider, backend);

ALTER TABLE sci_quantum_jobs ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Backend snapshot ledger: one row per daily (or change-triggered)
-- backend properties/configuration capture, pointing at the verbatim
-- bronze artifact. Telemetry is the un-backfillable time series, so
-- snapshots are taken on a cadence whether or not jobs ran (TW-1328
-- mechanic 5). The bronze index already records the capture; this
-- ledger is the queryable per-day series with provenance up front.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_quantum_backend_snapshots (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider      text NOT NULL CHECK (char_length(provider) > 0),
  backend       text NOT NULL CHECK (char_length(backend) > 0),
  snapshot_date date NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('properties', 'configuration')),
  artifact_id   uuid NOT NULL REFERENCES sci_bronze_artifacts(id),
  content_hash  text NOT NULL,
  captured_at   timestamptz NOT NULL,
  source        text NOT NULL,
  provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quantum_snap_series
  ON sci_quantum_backend_snapshots (provider, backend, snapshot_date);

ALTER TABLE sci_quantum_backend_snapshots ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Custody alerts: provider auth failures and reconciliation gaps.
-- An alert row is necessary but not sufficient: the collector also
-- pages the named channel the same day (runbook, TW-1329). Rows exist
-- so the alert history is auditable and Rex can probe it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sci_quantum_alerts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider        text NOT NULL CHECK (char_length(provider) > 0),
  kind            text NOT NULL
    CHECK (kind IN ('auth_failure', 'reconcile_gap', 'diff_gap', 'export_failure')),
  detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
  raised_at       timestamptz NOT NULL DEFAULT now(),
  acknowledged_at timestamptz,
  resolved_at     timestamptz
);

CREATE INDEX IF NOT EXISTS idx_quantum_alerts_open
  ON sci_quantum_alerts (provider, kind) WHERE resolved_at IS NULL;

ALTER TABLE sci_quantum_alerts ENABLE ROW LEVEL SECURITY;

-- =====================================================================
-- TW-612 · Real-data ingestors · license tagging on bronze
-- Prepared by Twistor Holdings LLC
--
-- The license tag rides every record (Diego TW-610 medallion mapping).
-- The migration constrains the verdict enum; application code enforces the
-- gold promotion gate (see licenses.py). Full license lineage into dataset
-- versions is a follow-up (TW-614).
-- =====================================================================

ALTER TABLE sci_bronze_artifacts
  ADD COLUMN IF NOT EXISTS license_tag text,
  ADD COLUMN IF NOT EXISTS license_verdict text
    CHECK (license_verdict IN ('green', 'yellow', 'red')),
  ADD COLUMN IF NOT EXISTS license_source_url text,
  ADD COLUMN IF NOT EXISTS source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Backfill: pre-existing synthetic-seed artifacts were produced in-house.
COMMENT ON COLUMN sci_bronze_artifacts.license_tag IS
  'SPDX-ish license identity, e.g. CC0-1.0, CC-BY-4.0, US-PD. NULL = pre-license-tagging era (treated as red).';
COMMENT ON COLUMN sci_bronze_artifacts.license_verdict IS
  'green = paywall-eligible; yellow = check required; red = quarantined out of gold.';

CREATE INDEX IF NOT EXISTS idx_sci_bronze_license
  ON sci_bronze_artifacts (lane, license_verdict);

-- Idempotency race guard (Rosa TW-612): check-then-insert on
-- (lane, content_hash) needs a uniqueness guarantee or concurrent
-- workers double-insert.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sci_bronze_lane_hash
  ON sci_bronze_artifacts (lane, content_hash);

-- Gold promotion gate: a per-lane helper view exposing only GREEN artifacts.
-- Ingestors join silver rows to this view when minting gold snapshots.
CREATE OR REPLACE VIEW sci_bronze_gold_eligible AS
SELECT id, lane, lab_id, storage_path, content_hash, collected_at,
       license_tag, license_source_url
FROM sci_bronze_artifacts
WHERE license_verdict = 'green';

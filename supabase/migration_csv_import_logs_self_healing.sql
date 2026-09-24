-- Migration: TW-208 self-healing — dead-letter columns on csv_import_logs
-- Run in Supabase SQL editor
--
-- Lets the import pipeline persist exactly what it skipped (row numbers +
-- reasons) where the morning standup can read it. The application code
-- degrades gracefully when these columns don't exist yet, so this migration
-- can be applied any time after the TW-208 deploy.

ALTER TABLE csv_import_logs
    ADD COLUMN IF NOT EXISTS skipped_rows INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS details JSONB;

COMMENT ON COLUMN csv_import_logs.skipped_rows IS
    'TW-208: rows skipped loudly during import (poison rows), never silently dropped';
COMMENT ON COLUMN csv_import_logs.details IS
    'TW-208: dead-letter summary JSON {pipeline, skipped, sample:[{index, preview, error, at}]}';

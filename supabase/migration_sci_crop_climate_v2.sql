-- TW-599 Wave 1: hemp-first trait panel (crop-climate lane).
-- Prepared by Twistor Holdings LLC.
--
-- CLIMATESHIFT-1 v1 launches hemp-first: every trial record can carry a
-- crop-specific trait panel. For hemp: the full cannabinoid panel (THC/CBD/CBG
-- percentages), fiber quality metrics, and the testing lab's accreditation ID.
-- Nullable JSONB: crops without a trait panel (maize/soy/wheat reference
-- corpus) store NULL/empty.

ALTER TABLE sci_crop_measurements
    ADD COLUMN IF NOT EXISTS traits JSONB;

ALTER TABLE sci_crop_gold
    ADD COLUMN IF NOT EXISTS traits JSONB;

CREATE INDEX IF NOT EXISTS sci_crop_traits_gin
    ON sci_crop_measurements USING GIN (traits);

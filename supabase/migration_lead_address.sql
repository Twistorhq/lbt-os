-- Run this in Supabase SQL Editor
-- TW-303: leads need addresses so the Leak Map can pin every stalled quote
-- and missed follow-up. Additive, nullable — old rows are unaffected.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS address TEXT;

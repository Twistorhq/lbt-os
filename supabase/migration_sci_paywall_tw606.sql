-- Prepared by Twistor Holdings LLC
-- TW-606: dynamic volume pricing curve -- per-dataset price floors and
-- customer tiers.
--
-- 1. sci_dataset_versions.price_floor_cents: the dataset's asset-value
--    floor for the download volume curve
--      price(d) = floor + (5999 - floor) * (d / 100000) ** 1.5, cap $59.99
--    Set at launch from the asset-value tier (standard $0.01 / premium $9.99
--    / flagship $29.99). Default 1 ($0.01).
--
-- 2. organizations.customer_tier: 'student' | 'researcher' | 'other'.
--    Set by the signup/purchase question ("researcher, student, or other").
--    The tier sets the download entry fee (student $5.99 / researcher $10.99
--    / other $10.99); all tiers then pay per-request API tolls at the
--    dataset's live curve price. Subscription still required.
--    NOTE: organizations is a shared core table -- Rosa review required
--    before this ALTER runs anywhere near real data.
--
-- 3. organizations.rate_limit_tier: 'standard' | 'pro' | 'scale'.
--    Monthly fees priced off the same volume curve. Enforcement wiring
--    is a follow-up; this only stores the tier.

ALTER TABLE sci_dataset_versions
  ADD COLUMN IF NOT EXISTS price_floor_cents int NOT NULL DEFAULT 1;

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS customer_tier text NOT NULL DEFAULT 'other';

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS rate_limit_tier text NOT NULL DEFAULT 'standard';

-- Backfill example (run per dataset at launch, reviewed):
-- UPDATE sci_dataset_versions
--    SET price_floor_cents = 999
--  WHERE lane = 'carbon' AND semver = '1.0.0';  -- premium asset value

-- =============================================================
-- TW-651 Twistor Social: local events data layer (evt_).
-- Run AFTER migration_social_v1.sql (evt_claims references
-- rsn_groups and idn_users; evt_listings.claimed_group_id links a
-- claimed listing to its native group).
--
-- Source contract: TW-646 architecture spec Section 10.3 and the
-- merged OpenSpec package (TW-648), tasks.md item 28.
--
-- Doctrines carried in DDL:
-- * Index, not a copy (spec 10.1): silver and gold hold facts,
--   attribution, and the source link only. There is no description,
--   image, photo, logo, price, or organizer-contact column in this
--   schema and there never will be (spec 10.6 never-store list);
--   connector code strips those fields before bronze landing too.
-- * Medallion: bronze payloads (evt_raw_items) are retained only
--   where the source's terms posture allows it, which is why
--   payload is nullable while payload_hash is not; silver carries
--   harmonized facts plus provenance; gold (evt_gold_listings) is
--   a read model rebuilt from silver by the publish step, never
--   written by a connector.
-- * Provenance is a storage rule: a listing's fingerprint is UNIQUE
--   and stays on the row after cancellation, so the row itself is
--   the 90-day fingerprint tombstone (spec 10.3); merged listings
--   keep one evt_listing_sources row per carrying source.
-- * Age ratings are nullable here on purpose: indexed events arrive
--   unrated and unrated fails closed for members (spec 5.9),
--   unlike native rsn_ content where ratings are mandatory.
-- * Delete behavior is deliberate: provenance and ops rows use
--   NO ACTION from the source side (a source is retired by ops,
--   never cascade-vaporized); owned rows (listing sources, gold,
--   publish log) CASCADE with their listing.
--
-- RLS: deny-all on every table, service role only, with ONE
-- exception by design (spec 10.3): evt_gold_listings carries a
-- SELECT-only policy for the dedicated read-only role
-- evt_public_read, the only evt_ surface a public request can
-- touch. No INSERT, UPDATE, or DELETE policy exists anywhere in
-- this file. Idempotent: safe to re-run.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Public read role for the gold surface (login is granted by ops,
-- never by this migration).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evt_public_read') THEN
        CREATE ROLE evt_public_read NOLOGIN;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION evt_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------
-- Sources: one configured pipe each
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('ics', 'api', 'organizer_import')),
    connector TEXT CHECK (connector IN ('ics_generic', 'localist', 'tribe_events', 'eventbrite_api', 'organizer_oauth')),
    terms_posture TEXT NOT NULL DEFAULT 'index_facts' CHECK (terms_posture IN ('index_facts', 'organizer_only', 'prohibited')),
    attribution_label TEXT NOT NULL,
    feed_url TEXT,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    poll_interval_seconds INT NOT NULL DEFAULT 21600 CHECK (poll_interval_seconds > 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_polled_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    consecutive_failures INT NOT NULL DEFAULT 0,
    circuit_open_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------
-- Bronze: raw items exactly as landed (after connector redaction)
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_raw_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES evt_sources(id) ON DELETE NO ACTION,
    source_item_key TEXT NOT NULL,
    payload JSONB,
    payload_hash TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, source_item_key, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_evt_raw_items_latest
    ON evt_raw_items (source_id, source_item_key, fetched_at DESC);

-- ---------------------------------------------------------------
-- Silver: venues, listings, provenance
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_venues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    street_address TEXT,
    city TEXT,
    neighborhood TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    geocode_provider TEXT,
    geocoded_at TIMESTAMPTZ,
    address_fingerprint TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS evt_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ,
    venue_id UUID REFERENCES evt_venues(id) ON DELETE NO ACTION,
    category TEXT,
    min_age_band TEXT CHECK (min_age_band IN ('age_16_plus', 'age_18_plus', 'age_21_plus')),
    rating_source TEXT CHECK (rating_source IN ('source_field', 'heuristic', 'organizer_confirmed', 'staff')),
    rated_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled', 'stale', 'claimed')),
    claimed_group_id UUID REFERENCES rsn_groups(id) ON DELETE NO ACTION,
    display_source_id UUID REFERENCES evt_sources(id) ON DELETE NO ACTION,
    fingerprint TEXT NOT NULL UNIQUE,
    cancelled_at TIMESTAMPTZ,
    tombstone_until TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

CREATE INDEX IF NOT EXISTS idx_evt_listings_status_starts
    ON evt_listings (status, starts_at);

CREATE TABLE IF NOT EXISTS evt_listing_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES evt_listings(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES evt_sources(id) ON DELETE NO ACTION,
    source_item_key TEXT NOT NULL,
    source_url TEXT,
    source_organizer_ref TEXT,
    authority INT NOT NULL CHECK (authority IN (1, 2, 3)),
    fetched_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    missed_syncs INT NOT NULL DEFAULT 0,
    UNIQUE (listing_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_evt_listing_sources_by_source
    ON evt_listing_sources (source_id, source_item_key);

-- ---------------------------------------------------------------
-- Claims (flow lands in tasks.md item 31; table per item 28)
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES evt_listings(id) ON DELETE NO ACTION,
    claimant_user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE NO ACTION,
    method TEXT NOT NULL CHECK (method IN ('oauth', 'domain', 'staff_review')),
    evidence_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied', 'displaced')),
    decided_by UUID REFERENCES idn_users(id) ON DELETE NO ACTION,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------
-- Ops: ingest runs, dead letters, publish log
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_ingest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES evt_sources(id) ON DELETE NO ACTION,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'failed', 'circuit_open', 'skipped_prohibited')),
    items_fetched INT NOT NULL DEFAULT 0,
    items_new INT NOT NULL DEFAULT 0,
    items_updated INT NOT NULL DEFAULT 0,
    items_unchanged INT NOT NULL DEFAULT 0,
    items_cancelled INT NOT NULL DEFAULT 0,
    items_dead_lettered INT NOT NULL DEFAULT 0,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS evt_dead_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES evt_sources(id) ON DELETE NO ACTION,
    ingest_run_id UUID REFERENCES evt_ingest_runs(id) ON DELETE NO ACTION,
    source_item_key TEXT,
    payload JSONB,
    payload_hash TEXT,
    error_class TEXT NOT NULL,
    error_detail TEXT,
    attempts INT NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'replayed', 'discarded')),
    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evt_publish_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES evt_listings(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('published', 'withheld', 'removed')),
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------
-- Gold: the public read model (facts, attribution, source link)
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evt_gold_listings (
    listing_id UUID PRIMARY KEY REFERENCES evt_listings(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ,
    venue_id UUID,
    venue_name TEXT,
    street_address TEXT,
    city TEXT,
    neighborhood TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    category TEXT,
    min_age_band TEXT CHECK (min_age_band IN ('age_16_plus', 'age_18_plus', 'age_21_plus')),
    attribution_text TEXT NOT NULL,
    primary_source_url TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evt_gold_starts ON evt_gold_listings (starts_at);

-- ---------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY['evt_sources', 'evt_venues', 'evt_listings', 'evt_claims'])
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_' || t || '_touch'
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION evt_touch_updated_at()',
                'trg_' || t || '_touch', t
            );
        END IF;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------
-- RLS: deny-all everywhere; gold is SELECT-only for evt_public_read
-- ---------------------------------------------------------------

ALTER TABLE evt_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_raw_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_venues ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_listing_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_ingest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_dead_letters ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_publish_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE evt_gold_listings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_sources_deny_all') THEN
        CREATE POLICY evt_sources_deny_all ON evt_sources FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_raw_items_deny_all') THEN
        CREATE POLICY evt_raw_items_deny_all ON evt_raw_items FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_venues_deny_all') THEN
        CREATE POLICY evt_venues_deny_all ON evt_venues FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_listings_deny_all') THEN
        CREATE POLICY evt_listings_deny_all ON evt_listings FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_listing_sources_deny_all') THEN
        CREATE POLICY evt_listing_sources_deny_all ON evt_listing_sources FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_claims_deny_all') THEN
        CREATE POLICY evt_claims_deny_all ON evt_claims FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_ingest_runs_deny_all') THEN
        CREATE POLICY evt_ingest_runs_deny_all ON evt_ingest_runs FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_dead_letters_deny_all') THEN
        CREATE POLICY evt_dead_letters_deny_all ON evt_dead_letters FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_publish_log_deny_all') THEN
        CREATE POLICY evt_publish_log_deny_all ON evt_publish_log FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'evt_gold_public_read_select') THEN
        CREATE POLICY evt_gold_public_read_select ON evt_gold_listings FOR SELECT TO evt_public_read USING (true);
    END IF;
END
$$;

GRANT SELECT ON evt_gold_listings TO evt_public_read;

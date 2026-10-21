-- =============================================================
-- TW-650 Twistor Social V1: social product tables (rsn_).
-- Run AFTER migration_verify_v1.sql (safety tables hold FKs into
-- vfy_identity_anchors).
--
-- Source contract: TW-646 architecture spec Section 4, merged
-- OpenSpec design (TW-648), tasks.md item 13, including the fix-round
-- deltas Rosa verified: rsn_rsvps `requested` sub-state with host
-- decision fields, rsn_close_friends, rsn_ambassadors, and the
-- audience-scope columns on posts and events.
--
-- Doctrines carried in DDL:
-- * PII stays bronze: exact locations live only in
--   rsn_event_locations, a separate table on a separate access path,
--   never joined into a general query path. Trusted-contact channels
--   are keyed HMAC hashes. Check-ins hold verdicts only: there is no
--   coordinate column in rsn_checkins and there never will be
--   (spec Section 7.1).
-- * Age ratings are mandatory: min_age_band is NOT NULL on groups,
--   events, and series. There is no unrated native content
--   (spec Section 5.9).
-- * Tier gates sit on the verified floor: meetup_min_tier /
--   min_tier_view / min_tier_rsvp can raise the bar above verified,
--   never admit an unverified account (spec Section 5.1).
-- * Delete behavior is deliberate: owned, ephemeral rows CASCADE
--   with the account; evidentiary rows (RSVPs, check-ins, confirms,
--   welfare checks, reviews, reveals, reports, sanctions, appeals,
--   trust ledger, collectibles) use NO ACTION, so account closure
--   must pass through the retention job (tasks.md item 10a), never
--   vaporize the evidence trail (spec Section 6.3).
--
-- RLS deny-all (service role only). Idempotent: safe to re-run
-- (IF NOT EXISTS throughout; policies guarded by pg_policies).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- updated_at touch helper (pattern from migration_identity_v1).
CREATE OR REPLACE FUNCTION rsn_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------
-- Social graph and groups
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rsn_profiles (
    user_id UUID PRIMARY KEY REFERENCES idn_users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    bio TEXT,
    avatar_ref TEXT,
    home_metro TEXT,
    locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'es')),
    show_attendance_default BOOLEAN NOT NULL DEFAULT TRUE,
    show_standing_default BOOLEAN NOT NULL DEFAULT TRUE,
    trusted_contact_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rsn_couple_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_low_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    user_high_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'dissolved')),
    initiated_by UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    confirmed_at TIMESTAMPTZ,
    dissolved_at TIMESTAMPTZ,
    dissolved_by UUID REFERENCES idn_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (user_low_id < user_high_id),
    UNIQUE (user_low_id, user_high_id)
);
-- One active link per user (spec Section 4).
CREATE UNIQUE INDEX IF NOT EXISTS idx_rsn_couple_links_active_low
    ON rsn_couple_links(user_low_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_rsn_couple_links_active_high
    ON rsn_couple_links(user_high_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS rsn_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    category TEXT NOT NULL,
    metro TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public'
        CHECK (visibility IN ('public', 'private', 'hidden')),
    meetup_min_tier TEXT NOT NULL DEFAULT 'verified'
        CHECK (meetup_min_tier IN ('verified', 'host_approved')),
    min_age_band TEXT NOT NULL
        CHECK (min_age_band IN ('age_16_plus', 'age_18_plus', 'age_21_plus')),
    created_by UUID NOT NULL REFERENCES idn_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_groups_metro ON rsn_groups(metro);

CREATE TABLE IF NOT EXISTS rsn_group_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES rsn_groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'moderator', 'member')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('invited', 'active', 'removed', 'left')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_rsn_memberships_user
    ON rsn_group_memberships(user_id);

-- Blocking is mutual invisibility (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blocker_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    blocked_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (blocker_id <> blocked_id),
    UNIQUE (blocker_id, blocked_id)
);

-- Close friends (TW-648 design delta): owner-curated, one-way, the
-- friend is never notified of listing or removal.
CREATE TABLE IF NOT EXISTS rsn_close_friends (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    friend_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (owner_id <> friend_id),
    UNIQUE (owner_id, friend_id)
);
CREATE INDEX IF NOT EXISTS idx_rsn_close_friends_friend
    ON rsn_close_friends(friend_id);

-- ---------------------------------------------------------------
-- Events, RSVPs, check-ins
-- ---------------------------------------------------------------

-- Series: the standing meetup. Instances are ordinary rsn_events
-- rows materialized by the scheduler tick on a rolling 4-week
-- horizon (spec Section 4). venue_ref points at the series' usual
-- rsn_event_locations record and is the ONLY link between a series
-- and its location (Rosa M2): the locations table carries no
-- back-pointer, so the two can never disagree about which address
-- is authoritative. Its FK is added after that table exists
-- (circular pair, see below).
CREATE TABLE IF NOT EXISTS rsn_event_series (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES rsn_groups(id) ON DELETE CASCADE,
    host_user_id UUID NOT NULL REFERENCES idn_users(id),
    title TEXT NOT NULL,
    description_template TEXT,
    recurrence_rule TEXT NOT NULL,
    default_capacity INTEGER CHECK (default_capacity IS NULL OR default_capacity > 0),
    default_guests_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    default_min_tier_view TEXT NOT NULL DEFAULT 'verified'
        CHECK (default_min_tier_view IN ('verified', 'host_approved')),
    default_min_tier_rsvp TEXT NOT NULL DEFAULT 'verified'
        CHECK (default_min_tier_rsvp IN ('verified', 'host_approved')),
    default_min_age_band TEXT NOT NULL
        CHECK (default_min_age_band IN ('age_16_plus', 'age_18_plus', 'age_21_plus')),
    venue_ref UUID,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'ended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rsn_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES rsn_groups(id) ON DELETE CASCADE,
    host_user_id UUID NOT NULL REFERENCES idn_users(id),
    title TEXT NOT NULL,
    description TEXT,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    capacity INTEGER CHECK (capacity IS NULL OR capacity > 0),
    guests_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    min_tier_view TEXT NOT NULL DEFAULT 'verified'
        CHECK (min_tier_view IN ('verified', 'host_approved')),
    min_tier_rsvp TEXT NOT NULL DEFAULT 'verified'
        CHECK (min_tier_rsvp IN ('verified', 'host_approved')),
    min_age_band TEXT NOT NULL
        CHECK (min_age_band IN ('age_16_plus', 'age_18_plus', 'age_21_plus')),
    venue_type TEXT NOT NULL DEFAULT 'venue'
        CHECK (venue_type IN ('venue', 'home')),
    audience_scope TEXT NOT NULL DEFAULT 'group'
        CHECK (audience_scope IN ('group', 'connections', 'close_friends')),
    series_id UUID REFERENCES rsn_event_series(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'cancelled', 'completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);
CREATE INDEX IF NOT EXISTS idx_rsn_events_group_starts
    ON rsn_events(group_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_rsn_events_series
    ON rsn_events(series_id) WHERE series_id IS NOT NULL;

-- Exact locations (bronze). Separate table, separate access path:
-- the exact location is served only by the gated reveal endpoint
-- (spec Section 8). A row is owned in exactly one of two ways: by
-- an event (event_id, UNIQUE), or as a series' usual-location
-- template, in which case event_id is NULL and ownership is
-- expressed ONLY by rsn_event_series.venue_ref pointing here
-- (Rosa M2: one source of truth, no back-pointer column that could
-- diverge from it). The surrogate id exists so venue_ref has a
-- stable target; a template row no series points at is inert and
-- is never reachable through the reveal path.
CREATE TABLE IF NOT EXISTS rsn_event_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID UNIQUE REFERENCES rsn_events(id) ON DELETE CASCADE,
    venue_name TEXT,
    street_address TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    arrival_instructions TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Close the circular pair: series.venue_ref -> locations(id).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_rsn_event_series_venue_ref'
    ) THEN
        ALTER TABLE rsn_event_series
            ADD CONSTRAINT fk_rsn_event_series_venue_ref
            FOREIGN KEY (venue_ref) REFERENCES rsn_event_locations(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- One usual-location row serves one series (Rosa M2): two series
-- never share, and silently co-edit, a single address record.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rsn_event_series_venue_ref_unique
    ON rsn_event_series(venue_ref) WHERE venue_ref IS NOT NULL;

-- RSVPs. `requested` is the TW-648 fix-round sub-state: a guest RSVP
-- enters requested; the host approves (going, a host-approved
-- confirmed guest) or declines. The host's close friends never enter
-- requested. plus_one links two verified members' rows only; it is
-- not a guest mechanism (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_rsvps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    status TEXT NOT NULL DEFAULT 'going'
        CHECK (status IN ('going', 'waitlist', 'declined', 'no_show', 'requested')),
    couple_link_id UUID REFERENCES rsn_couple_links(id),
    plus_one_of_rsvp_id UUID REFERENCES rsn_rsvps(id),
    rsvp_window TEXT NOT NULL DEFAULT 'general'
        CHECK (rsvp_window IN ('keyholder', 'pillar', 'fixture', 'regular',
                               'general', 'waitlist_promotion', 'host_held')),
    host_decided_by UUID REFERENCES idn_users(id),
    host_decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, user_id),
    CHECK (host_decided_at IS NULL OR host_decided_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_rsn_rsvps_event_status
    ON rsn_rsvps(event_id, status);
CREATE INDEX IF NOT EXISTS idx_rsn_rsvps_requested
    ON rsn_rsvps(event_id) WHERE status = 'requested';

-- Guest passes. The guest holds no account, no RSVP row, and no
-- check-in row: arrival is recorded here, corroborated by host
-- confirm, and conduct resolves to the anchor's ledger (Section 7.8).
CREATE TABLE IF NOT EXISTS rsn_guest_passes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    anchor_rsvp_id UUID NOT NULL REFERENCES rsn_rsvps(id),
    anchor_user_id UUID NOT NULL REFERENCES idn_users(id),
    guest_label TEXT NOT NULL,
    age_band_attested_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'attached'
        CHECK (status IN ('attached', 'checked_in', 'completed',
                          'no_show', 'removed')),
    attached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checked_in_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_guest_passes_anchor
    ON rsn_guest_passes(anchor_user_id);

-- Every exact-location fetch is a row: deterrent and evidence trail.
CREATE TABLE IF NOT EXISTS rsn_location_reveals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    revealed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_reveals_event ON rsn_location_reveals(event_id);

-- ---------------------------------------------------------------
-- Safety check-in loop (spec Section 7)
-- ---------------------------------------------------------------

-- Verdicts only. No coordinate column exists in this table and none
-- ever will: the device evaluates the geofence and sends the verdict.
CREATE TABLE IF NOT EXISTS rsn_checkins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    proof_type TEXT NOT NULL
        CHECK (proof_type IN ('geo_soft', 'qr_strong', 'host_confirm')),
    arrived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    departed_at TIMESTAMPTZ,
    UNIQUE (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS rsn_encounter_confirms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    confirmer_id UUID NOT NULL REFERENCES idn_users(id),
    subject_id UUID NOT NULL REFERENCES idn_users(id),
    met_them BOOLEAN NOT NULL,
    identity_matched BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (confirmer_id <> subject_id),
    UNIQUE (event_id, confirmer_id, subject_id)
);

-- Response enums and timestamps only; no location, no free text.
CREATE TABLE IF NOT EXISTS rsn_welfare_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    prompted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    response TEXT
        CHECK (response IN ('fine', 'need_a_minute', 'need_help', 'no_response')),
    help_action TEXT NOT NULL DEFAULT 'none'
        CHECK (help_action IN ('none', 'trusted_contact_notified',
                               'location_share_started', 'exit_help_shown',
                               'silent_alert')),
    responded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rsn_welfare_event ON rsn_welfare_checks(event_id);

CREATE TABLE IF NOT EXISTS rsn_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    reviewer_id UUID NOT NULL REFERENCES idn_users(id),
    subject_id UUID REFERENCES idn_users(id),
    subject_type TEXT NOT NULL
        CHECK (subject_type IN ('person', 'event', 'guest_pass')),
    guest_pass_id UUID REFERENCES rsn_guest_passes(id),
    interaction_rating SMALLINT
        CHECK (interaction_rating IS NULL OR interaction_rating BETWEEN 1 AND 5),
    good_person BOOLEAN,
    would_meet_again BOOLEAN,
    would_join_again BOOLEAN,
    free_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_reviews_subject
    ON rsn_reviews(subject_id) WHERE subject_id IS NOT NULL;

-- Used only by the welfare flow and emergency notices, via Notify.
CREATE TABLE IF NOT EXISTS rsn_trusted_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    contact_name TEXT NOT NULL,
    contact_channel_hash TEXT NOT NULL,
    relationship TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, contact_channel_hash)
);

-- ---------------------------------------------------------------
-- Trust (spec Section 7.5)
-- ---------------------------------------------------------------

-- Append-only ledger: no updates, no deletes outside the retention
-- job. The score is a derived view, recomputable at any time.
CREATE TABLE IF NOT EXISTS rsn_trust_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    event_type TEXT NOT NULL
        CHECK (event_type IN ('verification_tier_set', 'identity_confirm_positive',
                              'identity_strike', 'review_received', 'no_show',
                              'report_substantiated', 'report_unfounded',
                              'vouch_received', 'checkin_completed',
                              'series_streak')),
    weight NUMERIC NOT NULL,
    ref_type TEXT,
    ref_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_trust_events_user
    ON rsn_trust_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS rsn_trust_scores (
    user_id UUID PRIMARY KEY REFERENCES idn_users(id) ON DELETE CASCADE,
    score NUMERIC NOT NULL DEFAULT 0,
    band TEXT NOT NULL DEFAULT 'good_standing'
        CHECK (band IN ('trusted', 'good_standing', 'watch', 'restricted')),
    standing_tier TEXT NOT NULL DEFAULT 'verified'
        CHECK (standing_tier IN ('verified', 'regular', 'fixture',
                                 'pillar', 'keyholder')),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The single rule table the reward catalog maps onto (Section 7.9).
-- Changing a pin means editing a row here, never code, never a
-- hand grant.
CREATE TABLE IF NOT EXISTS rsn_collectible_rules (
    rule_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL
        CHECK (kind IN ('threshold', 'per_ref', 'composite', 'streak', 'tenure')),
    params JSONB NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT 'adult'
        CHECK (scope IN ('adult', 'teen_school', 'both')),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Written only by the derivation job and the revocation pass.
-- There is no admin write path (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_collectibles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    rule_key TEXT NOT NULL REFERENCES rsn_collectible_rules(rule_key),
    ref_type TEXT NOT NULL
        CHECK (ref_type IN ('venue', 'district', 'series', 'group', 'milestone')),
    ref_id UUID,
    value INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    minted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    UNIQUE (user_id, rule_key, ref_type, ref_id, value)
);
-- Ref-less rules (First Night, night counts, Year One) carry
-- ref_id NULL, and Postgres treats NULLs as distinct under the
-- table constraint above, so it cannot bind for them (Rosa m5).
-- This partial index is what makes a ref-less instance one per
-- member per rule and value (spec Section 7.9).
CREATE UNIQUE INDEX IF NOT EXISTS idx_rsn_collectibles_refless_unique
    ON rsn_collectibles(user_id, rule_key, ref_type, value)
    WHERE ref_id IS NULL;

-- A vouch is a trust signal only: it never grants the vouchee any
-- capability, scope, or tier (spec Sections 5.1, 7.5).
CREATE TABLE IF NOT EXISTS rsn_vouches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voucher_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    vouchee_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES rsn_events(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (voucher_id <> vouchee_id),
    UNIQUE (voucher_id, vouchee_id)
);

-- ---------------------------------------------------------------
-- Feed
-- ---------------------------------------------------------------

-- A post carries no age rating of its own: visibility inherits the
-- rating of its container (spec Section 4). audience_scope is the
-- TW-648 design delta, enforced at the same query layer as the
-- Section 5.9 band filter; neither substitutes for the other.
CREATE TABLE IF NOT EXISTS rsn_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id UUID NOT NULL REFERENCES idn_users(id),
    group_id UUID REFERENCES rsn_groups(id) ON DELETE CASCADE,
    post_type TEXT NOT NULL DEFAULT 'update'
        CHECK (post_type IN ('update', 'celebration', 'event_recap')),
    body TEXT NOT NULL,
    media_refs JSONB NOT NULL DEFAULT '[]',
    visibility TEXT NOT NULL DEFAULT 'group'
        CHECK (visibility IN ('group', 'metro', 'connections')),
    audience_scope TEXT NOT NULL DEFAULT 'group'
        CHECK (audience_scope IN ('group', 'connections', 'close_friends')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_posts_group ON rsn_posts(group_id, created_at);

CREATE TABLE IF NOT EXISTS rsn_reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES rsn_posts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('like', 'congrats', 'celebrate')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (post_id, user_id, kind)
);

CREATE TABLE IF NOT EXISTS rsn_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES rsn_posts(id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES idn_users(id),
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('published', 'held', 'removed')),
    screening_verdict TEXT NOT NULL DEFAULT 'clean'
        CHECK (screening_verdict IN ('clean', 'flagged')),
    screening_categories JSONB NOT NULL DEFAULT '[]',
    human_review_state TEXT
        CHECK (human_review_state IN ('queued', 'reviewed_clean',
                                      'removed_on_review')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_comments_post ON rsn_comments(post_id, created_at);

-- Teen voice quota counter (spec Section 5.10): incremented
-- atomically at comment acceptance, never decremented by removals.
CREATE TABLE IF NOT EXISTS rsn_voice_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    week_start DATE NOT NULL,
    comments_used INTEGER NOT NULL DEFAULT 0 CHECK (comments_used >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, week_start)
);

-- ---------------------------------------------------------------
-- Safety and moderation (spec Section 6)
-- ---------------------------------------------------------------

-- Category set is spec Section 4 plus the TW-648 design's serious
-- categories (threats, sexual_misconduct, identity_fraud), which
-- bypass ambassadors at intake. Report contents are staff-only.
CREATE TABLE IF NOT EXISTS rsn_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_id UUID NOT NULL REFERENCES idn_users(id),
    subject_user_id UUID REFERENCES idn_users(id),
    target_type TEXT NOT NULL
        CHECK (target_type IN ('user', 'event', 'post', 'comment', 'group')),
    target_id UUID NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('harassment', 'identity_mismatch', 'unsafe_behavior',
                            'fraud', 'age_misrating', 'threats',
                            'sexual_misconduct', 'identity_fraud', 'other_serious')),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'triaged', 'actioned', 'unfounded')),
    assigned_to UUID REFERENCES idn_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rsn_reports_status ON rsn_reports(status, created_at);

-- Bans attach to the identity anchor, not the account (Section 6.2).
CREATE TABLE IF NOT EXISTS rsn_bans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_anchor_hash TEXT NOT NULL
        REFERENCES vfy_identity_anchors(anchor_hash),
    scope TEXT NOT NULL DEFAULT 'platform' CHECK (scope IN ('platform', 'group')),
    group_id UUID REFERENCES rsn_groups(id) ON DELETE CASCADE,
    reason_category TEXT NOT NULL,
    evidence_packet_ref TEXT,
    issued_by UUID NOT NULL REFERENCES idn_users(id),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    appeal_status TEXT NOT NULL DEFAULT 'none'
        CHECK (appeal_status IN ('none', 'pending', 'upheld', 'overturned')),
    lifted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rsn_bans_anchor ON rsn_bans(identity_anchor_hash);

-- The rungs of the consequence ladder below a ban (Section 6.6);
-- purge is the integrity lifecycle's terminal sanction (Section
-- 6.7), recorded here so appeals and audit treat it identically.
CREATE TABLE IF NOT EXISTS rsn_sanctions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_anchor_hash TEXT NOT NULL
        REFERENCES vfy_identity_anchors(anchor_hash),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    level TEXT NOT NULL CHECK (level IN ('warning', 'restriction', 'purge')),
    reason_category TEXT NOT NULL,
    source_ref TEXT,
    scope TEXT NOT NULL DEFAULT 'platform' CHECK (scope IN ('platform', 'group')),
    group_id UUID REFERENCES rsn_groups(id) ON DELETE CASCADE,
    issued_by UUID NOT NULL REFERENCES idn_users(id),
    expires_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'overturned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_sanctions_anchor
    ON rsn_sanctions(identity_anchor_hash) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS rsn_appeals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_anchor_hash TEXT NOT NULL
        REFERENCES vfy_identity_anchors(anchor_hash),
    appellant_user_id UUID NOT NULL REFERENCES idn_users(id),
    target_type TEXT NOT NULL CHECK (target_type IN ('sanction', 'ban')),
    target_id UUID NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'under_review', 'upheld',
                          'overturned', 'modified')),
    assigned_to UUID REFERENCES idn_users(id),
    sla_due_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    outcome_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_appeals_status
    ON rsn_appeals(status, sla_due_at);

-- Immutable snapshots: hash-chained manifests so a packet produced
-- later provably matches what was frozen (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_evidence_packets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_type TEXT NOT NULL
        CHECK (case_type IN ('report', 'ban', 'law_enforcement_request')),
    case_ref TEXT NOT NULL,
    snapshot_manifest JSONB NOT NULL,
    created_by UUID NOT NULL REFERENCES idn_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    legal_hold BOOLEAN NOT NULL DEFAULT FALSE
);

-- Every disclosure to anyone outside Twistor staff is a row.
-- No exceptions (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_disclosures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id UUID NOT NULL REFERENCES rsn_evidence_packets(id),
    request_type TEXT NOT NULL
        CHECK (request_type IN ('subpoena', 'court_order', 'warrant', 'emergency')),
    requesting_agency TEXT NOT NULL,
    legal_review_by UUID REFERENCES idn_users(id),
    disclosed_at TIMESTAMPTZ,
    scope_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only audit log. If it matters, it is here.
CREATE TABLE IF NOT EXISTS rsn_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_audit_target ON rsn_audit_log(target, created_at);

-- ---------------------------------------------------------------
-- Billing (organizers pay; attendees are never billing subjects)
-- ---------------------------------------------------------------

-- Configuration, not code. price_cents stays NULL until Justynn
-- sets launch prices (spec Section 4).
CREATE TABLE IF NOT EXISTS rsn_plan_catalog (
    plan_key TEXT PRIMARY KEY
        CHECK (plan_key IN ('organizer_free', 'organizer_plus')),
    price_cents INTEGER CHECK (price_cents IS NULL OR price_cents >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    entitlements JSONB NOT NULL DEFAULT '{}'
);

INSERT INTO rsn_plan_catalog (plan_key, price_cents, currency, entitlements)
VALUES
    ('organizer_free', 0, 'USD',
     '{"group_count": 1, "promotion_quota": 0, "claim_privileges": false, "series_support": false}'),
    ('organizer_plus', NULL, 'USD',
     '{"group_count": null, "promotion_quota": null, "claim_privileges": true, "series_support": true}')
ON CONFLICT (plan_key) DO NOTHING;

-- A lapsed plan degrades promotion and paid entitlements only; it
-- never touches member access, attendance, or any safety function.
CREATE TABLE IF NOT EXISTS rsn_organizer_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_type TEXT NOT NULL CHECK (owner_type IN ('user', 'group')),
    owner_id UUID NOT NULL,
    plan_key TEXT NOT NULL REFERENCES rsn_plan_catalog(plan_key),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('trialing', 'active', 'past_due', 'cancelled')),
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_organizer_plans_owner
    ON rsn_organizer_plans(owner_type, owner_id);

-- ---------------------------------------------------------------
-- Ambassador program (TW-648 design, decided 2026-10-20)
-- ---------------------------------------------------------------

-- First-line review staffed by high-standing verified locals.
-- Queue-scoped entitlement grants are checked per request at the
-- entitlement layer; this row is the role record. Bronze document
-- and biometric stores are never reachable from an ambassador
-- session.
CREATE TABLE IF NOT EXISTS rsn_ambassadors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES idn_users(id) ON DELETE CASCADE,
    metro TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'invited'
        CHECK (status IN ('invited', 'vetting', 'active', 'suspended', 'revoked')),
    vetted_by UUID REFERENCES idn_users(id),
    activated_at TIMESTAMPTZ,
    status_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rsn_ambassadors_metro_status
    ON rsn_ambassadors(metro, status);

-- ---------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_rsn_profiles_touch ON rsn_profiles;
CREATE TRIGGER trg_rsn_profiles_touch
    BEFORE UPDATE ON rsn_profiles
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_groups_touch ON rsn_groups;
CREATE TRIGGER trg_rsn_groups_touch
    BEFORE UPDATE ON rsn_groups
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_events_touch ON rsn_events;
CREATE TRIGGER trg_rsn_events_touch
    BEFORE UPDATE ON rsn_events
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_event_series_touch ON rsn_event_series;
CREATE TRIGGER trg_rsn_event_series_touch
    BEFORE UPDATE ON rsn_event_series
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_organizer_plans_touch ON rsn_organizer_plans;
CREATE TRIGGER trg_rsn_organizer_plans_touch
    BEFORE UPDATE ON rsn_organizer_plans
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_ambassadors_touch ON rsn_ambassadors;
CREATE TRIGGER trg_rsn_ambassadors_touch
    BEFORE UPDATE ON rsn_ambassadors
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_rsn_voice_usage_touch ON rsn_voice_usage;
CREATE TRIGGER trg_rsn_voice_usage_touch
    BEFORE UPDATE ON rsn_voice_usage
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role is the only reader/writer.
-- ---------------------------------------------------------------

ALTER TABLE rsn_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_couple_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_group_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_close_friends ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_event_series ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_event_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_rsvps ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_guest_passes ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_location_reveals ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_checkins ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_encounter_confirms ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_welfare_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_trusted_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_trust_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_trust_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_collectible_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_collectibles ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_vouches ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_voice_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_bans ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_sanctions ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_appeals ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_evidence_packets ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_disclosures ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_plan_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_organizer_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE rsn_ambassadors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_profiles_deny_all') THEN
        CREATE POLICY rsn_profiles_deny_all ON rsn_profiles FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_couple_links_deny_all') THEN
        CREATE POLICY rsn_couple_links_deny_all ON rsn_couple_links FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_groups_deny_all') THEN
        CREATE POLICY rsn_groups_deny_all ON rsn_groups FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_group_memberships_deny_all') THEN
        CREATE POLICY rsn_group_memberships_deny_all ON rsn_group_memberships FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_blocks_deny_all') THEN
        CREATE POLICY rsn_blocks_deny_all ON rsn_blocks FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_close_friends_deny_all') THEN
        CREATE POLICY rsn_close_friends_deny_all ON rsn_close_friends FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_event_series_deny_all') THEN
        CREATE POLICY rsn_event_series_deny_all ON rsn_event_series FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_events_deny_all') THEN
        CREATE POLICY rsn_events_deny_all ON rsn_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_event_locations_deny_all') THEN
        CREATE POLICY rsn_event_locations_deny_all ON rsn_event_locations FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_rsvps_deny_all') THEN
        CREATE POLICY rsn_rsvps_deny_all ON rsn_rsvps FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_guest_passes_deny_all') THEN
        CREATE POLICY rsn_guest_passes_deny_all ON rsn_guest_passes FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_location_reveals_deny_all') THEN
        CREATE POLICY rsn_location_reveals_deny_all ON rsn_location_reveals FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_checkins_deny_all') THEN
        CREATE POLICY rsn_checkins_deny_all ON rsn_checkins FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_encounter_confirms_deny_all') THEN
        CREATE POLICY rsn_encounter_confirms_deny_all ON rsn_encounter_confirms FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_welfare_checks_deny_all') THEN
        CREATE POLICY rsn_welfare_checks_deny_all ON rsn_welfare_checks FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_reviews_deny_all') THEN
        CREATE POLICY rsn_reviews_deny_all ON rsn_reviews FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_trusted_contacts_deny_all') THEN
        CREATE POLICY rsn_trusted_contacts_deny_all ON rsn_trusted_contacts FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_trust_events_deny_all') THEN
        CREATE POLICY rsn_trust_events_deny_all ON rsn_trust_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_trust_scores_deny_all') THEN
        CREATE POLICY rsn_trust_scores_deny_all ON rsn_trust_scores FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_collectible_rules_deny_all') THEN
        CREATE POLICY rsn_collectible_rules_deny_all ON rsn_collectible_rules FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_collectibles_deny_all') THEN
        CREATE POLICY rsn_collectibles_deny_all ON rsn_collectibles FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_vouches_deny_all') THEN
        CREATE POLICY rsn_vouches_deny_all ON rsn_vouches FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_posts_deny_all') THEN
        CREATE POLICY rsn_posts_deny_all ON rsn_posts FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_reactions_deny_all') THEN
        CREATE POLICY rsn_reactions_deny_all ON rsn_reactions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_comments_deny_all') THEN
        CREATE POLICY rsn_comments_deny_all ON rsn_comments FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_voice_usage_deny_all') THEN
        CREATE POLICY rsn_voice_usage_deny_all ON rsn_voice_usage FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_reports_deny_all') THEN
        CREATE POLICY rsn_reports_deny_all ON rsn_reports FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_bans_deny_all') THEN
        CREATE POLICY rsn_bans_deny_all ON rsn_bans FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_sanctions_deny_all') THEN
        CREATE POLICY rsn_sanctions_deny_all ON rsn_sanctions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_appeals_deny_all') THEN
        CREATE POLICY rsn_appeals_deny_all ON rsn_appeals FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_evidence_packets_deny_all') THEN
        CREATE POLICY rsn_evidence_packets_deny_all ON rsn_evidence_packets FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_disclosures_deny_all') THEN
        CREATE POLICY rsn_disclosures_deny_all ON rsn_disclosures FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_audit_log_deny_all') THEN
        CREATE POLICY rsn_audit_log_deny_all ON rsn_audit_log FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_plan_catalog_deny_all') THEN
        CREATE POLICY rsn_plan_catalog_deny_all ON rsn_plan_catalog FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_organizer_plans_deny_all') THEN
        CREATE POLICY rsn_organizer_plans_deny_all ON rsn_organizer_plans FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_ambassadors_deny_all') THEN
        CREATE POLICY rsn_ambassadors_deny_all ON rsn_ambassadors FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order)
-- =============================================================
-- DROP TABLE IF EXISTS rsn_ambassadors;
-- DROP TABLE IF EXISTS rsn_organizer_plans;
-- DROP TABLE IF EXISTS rsn_plan_catalog;
-- DROP TABLE IF EXISTS rsn_audit_log;
-- DROP TABLE IF EXISTS rsn_disclosures;
-- DROP TABLE IF EXISTS rsn_evidence_packets;
-- DROP TABLE IF EXISTS rsn_appeals;
-- DROP TABLE IF EXISTS rsn_sanctions;
-- DROP TABLE IF EXISTS rsn_bans;
-- DROP TABLE IF EXISTS rsn_reports;
-- DROP TABLE IF EXISTS rsn_voice_usage;
-- DROP TABLE IF EXISTS rsn_comments;
-- DROP TABLE IF EXISTS rsn_reactions;
-- DROP TABLE IF EXISTS rsn_posts;
-- DROP TABLE IF EXISTS rsn_vouches;
-- DROP TABLE IF EXISTS rsn_collectibles;
-- DROP TABLE IF EXISTS rsn_collectible_rules;
-- DROP TABLE IF EXISTS rsn_trust_scores;
-- DROP TABLE IF EXISTS rsn_trust_events;
-- DROP TABLE IF EXISTS rsn_trusted_contacts;
-- DROP TABLE IF EXISTS rsn_reviews;
-- DROP TABLE IF EXISTS rsn_welfare_checks;
-- DROP TABLE IF EXISTS rsn_encounter_confirms;
-- DROP TABLE IF EXISTS rsn_checkins;
-- DROP TABLE IF EXISTS rsn_location_reveals;
-- DROP TABLE IF EXISTS rsn_guest_passes;
-- DROP TABLE IF EXISTS rsn_rsvps;
-- DROP TABLE IF EXISTS rsn_event_locations;
-- DROP TABLE IF EXISTS rsn_events;
-- DROP TABLE IF EXISTS rsn_event_series;
-- DROP TABLE IF EXISTS rsn_close_friends;
-- DROP TABLE IF EXISTS rsn_blocks;
-- DROP TABLE IF EXISTS rsn_group_memberships;
-- DROP TABLE IF EXISTS rsn_groups;
-- DROP TABLE IF EXISTS rsn_couple_links;
-- DROP TABLE IF EXISTS rsn_profiles;
-- DROP FUNCTION IF EXISTS rsn_touch_updated_at();

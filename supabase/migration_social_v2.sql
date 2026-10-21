-- =============================================================
-- TW-661 Twistor Social v2: RSVP opens-at anchor, held-seat
-- accounting, couple-RSVP atomicity (filed by Rosa, TW-660 review).
--
-- Builds on migration_social_v1 (TW-650). Three deltas:
--
-- 1. rsvp_opens_at on rsn_events (+ the series template default):
--    the anchor the Section 7.7 standing waves need. NULL means
--    the general opening is the publish moment, which is the
--    Section 7.7 default TW-660 recorded truthfully; a set value
--    is the GENERAL opening, and early windows are computed from
--    it (Keyholder -72h, Pillar -48h, Fixture -24h, Regular -12h,
--    per the reward catalog).
--
-- 2. rsn_event_seat_holds: host-held seat accounting. A hold is
--    NOT an RSVP and never lives in rsn_rsvps; capacity accounting
--    counts going RSVPs once (in rsn_rsvps) plus active held seats
--    once (here), so a seat is never double-counted. Release
--    rules: the host releases explicitly ('released'), a hold
--    expires at release_at ('expired', swept by
--    rsn_release_expired_seat_holds and lazily at the service
--    layer), or the host converts it ('consumed'). release_at is
--    never after the event start; that rule spans tables, so the
--    service enforces it at write time (a column CHECK cannot).
--
-- 3. rsn_rsvp_couple: the couple RSVP (two first-class rows, spec
--    Section 4) as one database transaction. TW-660 made it
--    atomic at the service layer by validating both partners
--    before either row existed; this RPC makes the PAIR WRITE
--    itself atomic and serializes concurrent pair writes per
--    event with an advisory lock, so two couples racing the last
--    seats cannot interleave. Any failure (including the
--    UNIQUE(event_id, user_id) on either row) aborts the
--    transaction: both rows or neither.
--
-- RLS deny-all (service role only), same posture as v1.
-- Idempotent: safe to re-run.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------
-- 1. RSVP opens-at anchors
-- ---------------------------------------------------------------

ALTER TABLE rsn_events
    ADD COLUMN IF NOT EXISTS rsvp_opens_at TIMESTAMPTZ;

ALTER TABLE rsn_event_series
    ADD COLUMN IF NOT EXISTS default_rsvp_opens_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_rsn_events_rsvp_opens_at
    ON rsn_events(rsvp_opens_at) WHERE rsvp_opens_at IS NOT NULL;

-- ---------------------------------------------------------------
-- 2. Held seats
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rsn_event_seat_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES rsn_events(id) ON DELETE CASCADE,
    held_by UUID NOT NULL REFERENCES idn_users(id),
    seats INTEGER NOT NULL CHECK (seats > 0),
    status TEXT NOT NULL DEFAULT 'held'
        CHECK (status IN ('held', 'released', 'expired', 'consumed')),
    release_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((status = 'held' AND released_at IS NULL)
        OR (status <> 'held' AND released_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_rsn_seat_holds_event_status
    ON rsn_event_seat_holds(event_id, status);

DROP TRIGGER IF EXISTS trg_rsn_event_seat_holds_touch ON rsn_event_seat_holds;
CREATE TRIGGER trg_rsn_event_seat_holds_touch
    BEFORE UPDATE ON rsn_event_seat_holds
    FOR EACH ROW EXECUTE FUNCTION rsn_touch_updated_at();

-- Seats actively held against an event at a moment: live holds
-- whose release time has not yet arrived. Expired-but-unswept
-- holds do not count here; the sweep only settles their status.
CREATE OR REPLACE FUNCTION rsn_active_held_seats(
    p_event_id UUID,
    p_now TIMESTAMPTZ DEFAULT NOW()
) RETURNS INTEGER AS $$
    SELECT COALESCE(SUM(seats), 0)::INTEGER
    FROM rsn_event_seat_holds
    WHERE event_id = p_event_id
      AND status = 'held'
      AND release_at > p_now;
$$ LANGUAGE sql STABLE;

-- Settle expired holds (release rule 2). p_event_id NULL sweeps
-- every event (the scheduler shape); a single event id scopes it
-- (the lazy service-layer shape). Returns rows settled.
CREATE OR REPLACE FUNCTION rsn_release_expired_seat_holds(
    p_event_id UUID DEFAULT NULL,
    p_now TIMESTAMPTZ DEFAULT NOW()
) RETURNS INTEGER AS $$
DECLARE
    settled INTEGER;
BEGIN
    UPDATE rsn_event_seat_holds
    SET status = 'expired', released_at = p_now
    WHERE status = 'held'
      AND release_at <= p_now
      AND (p_event_id IS NULL OR event_id = p_event_id);
    GET DIAGNOSTICS settled = ROW_COUNT;
    RETURN settled;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------
-- 3. Couple RSVP: both rows or neither, in one transaction
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION rsn_rsvp_couple(
    p_event_id UUID,
    p_user_id UUID,
    p_partner_id UUID,
    p_couple_link_id UUID,
    p_user_status TEXT,
    p_partner_status TEXT,
    p_rsvp_window TEXT DEFAULT 'general',
    p_user_plus_one_of UUID DEFAULT NULL,
    p_now TIMESTAMPTZ DEFAULT NOW()
) RETURNS SETOF rsn_rsvps AS $$
BEGIN
    -- Serialize pair writes per event: capacity decisions made by
    -- concurrent couple RSVPs must not interleave.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_event_id::text, 0));

    RETURN QUERY
    INSERT INTO rsn_rsvps (
        id, event_id, user_id, status, couple_link_id,
        plus_one_of_rsvp_id, rsvp_window, host_decided_by,
        host_decided_at, created_at
    ) VALUES (
        gen_random_uuid(), p_event_id, p_user_id, p_user_status,
        p_couple_link_id, p_user_plus_one_of, p_rsvp_window,
        NULL, NULL, p_now
    )
    RETURNING *;

    RETURN QUERY
    INSERT INTO rsn_rsvps (
        id, event_id, user_id, status, couple_link_id,
        plus_one_of_rsvp_id, rsvp_window, host_decided_by,
        host_decided_at, created_at
    ) VALUES (
        gen_random_uuid(), p_event_id, p_partner_id, p_partner_status,
        p_couple_link_id, NULL, p_rsvp_window, NULL, NULL, p_now
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role is the only reader/writer.
-- ---------------------------------------------------------------

ALTER TABLE rsn_event_seat_holds ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_event_seat_holds_deny_all') THEN
        CREATE POLICY rsn_event_seat_holds_deny_all ON rsn_event_seat_holds FOR ALL USING (false);
    END IF;
END;
$$;

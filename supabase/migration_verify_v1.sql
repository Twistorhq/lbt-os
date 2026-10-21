-- =============================================================
-- TW-650 Twistor Social V1: verification engine tables (vfy_).
-- Run BEFORE migration_social_v1.sql (rsn_ safety tables hold FKs
-- into vfy_identity_anchors).
--
-- Source contract: TW-646 architecture spec Section 4 + Section 5.7,
-- merged OpenSpec design (TW-648), tasks.md item 9.
--
-- PII doctrine (medallion, TW-547): everything here is bronze, the
-- restricted store inside the verify service, reachable by no API
-- response. Extracted document fields live ONLY as keyed HMAC hashes;
-- the minimum plaintext the match step needs rides in an encrypted
-- payload (key outside the database) and is cleared at decision.
-- Raw document/selfie images are never a permanent record: staging
-- reference + destruction timestamp only (72-hour hard ceiling,
-- spec Section 5.7). Face templates are AES-256 ciphertext BYTEA;
-- the key lives in the secrets store, never in the database.
-- The raw date of birth is never stored: dob_hash only, and the age
-- band is the only age fact that leaves this service (Section 5.8).
--
-- Retention (TW-654, Angela's three-trigger rule): templates and
-- door photos are destroyed at the earliest of purpose satisfied,
-- 24 months after last interaction (CO HB 24-1130), or within 45
-- days after storage is determined no longer necessary (the 45 days
-- extendable once by 45 for complexity, after an annual necessity
-- review). vfy_retention_actions is the executable record of that
-- rule (determination, deadline, hold, execution) and
-- vfy_verification_states is the current state the dormancy drop
-- acts on, so the retention job (tasks.md item 10a) proves the rule
-- from schema state instead of reconstructing it from outcomes.
--
-- Delete behavior: NOTHING in this file cascades. Consents,
-- sessions, documents, biometrics, and signals are the audit record
-- spec Section 6.3 retains for 7 years; the retention job deletes
-- or archives them explicitly, in legal order. An account delete
-- can never vaporize them as a side effect.
--
-- RLS deny-all (service role only). Idempotent: safe to re-run
-- (IF NOT EXISTS throughout; policies guarded by pg_policies).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Engine versions. Every verdict traces to the exact engine that
-- produced it (spec Section 5.6). One active version at a time.
CREATE TABLE IF NOT EXISTS vfy_engine_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version TEXT NOT NULL UNIQUE,
    pipeline_config JSONB NOT NULL DEFAULT '{}',
    model_refs JSONB NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vfy_engine_versions_one_active
    ON vfy_engine_versions(is_active) WHERE is_active;

-- Consent records (spec Section 5.7). T2 requires explicit biometric
-- consent before capture, separate from the terms of service. In
-- BIPA-class jurisdictions the consent and the published retention
-- schedule are legal instruments; this table is that record.
CREATE TABLE IF NOT EXISTS vfy_consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    consent_type TEXT NOT NULL
        CHECK (consent_type IN ('biometric_capture', 'school_id_biometric_capture')),
    policy_version TEXT NOT NULL,
    retention_schedule_version TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The (id, user_id) pair is the composite FK target that binds a
    -- biometric row to a consent OWNED BY THE SAME member (Rosa m4):
    -- a consent is a legal instrument and can never be borrowed.
    UNIQUE (id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_vfy_consents_user
    ON vfy_consents(user_id) WHERE revoked_at IS NULL;

-- Verification sessions (bronze). One verification attempt.
CREATE TABLE IF NOT EXISTS vfy_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    tier_requested SMALLINT NOT NULL DEFAULT 2
        CHECK (tier_requested IN (1, 2)),
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'document_received', 'matched',
                          'review', 'passed', 'failed', 'expired')),
    engine_version_id UUID REFERENCES vfy_engine_versions(id),
    decision_by TEXT,
    confidence_band TEXT
        CHECK (confidence_band IN ('low', 'medium', 'high')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vfy_sessions_user ON vfy_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_vfy_sessions_status
    ON vfy_sessions(status) WHERE status IN ('started', 'review');

-- Documents (bronze, highest sensitivity). Extracted fields are keyed
-- HMAC hashes only. The minimum plaintext the match step needs is an
-- encrypted payload, cleared at decision (match_payload_cleared_at).
-- The image itself is a staging reference plus a destruction stamp,
-- never a stored record.
CREATE TABLE IF NOT EXISTS vfy_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES vfy_sessions(id),
    document_type TEXT NOT NULL
        CHECK (document_type IN ('passport', 'drivers_license',
                                 'state_id', 'school_id')),
    issuing_region TEXT,
    name_hash TEXT NOT NULL,
    -- Nullable for exactly one class: a school ID may carry no
    -- birthdate, and spec Section 4 populates dob_hash only where
    -- the card or the accepted age evidence carries one. Government
    -- document classes require it (check below). Never fabricate a
    -- hash to satisfy a NOT NULL.
    dob_hash TEXT,
    doc_number_hash TEXT NOT NULL,
    match_payload_encrypted BYTEA,
    match_payload_cleared_at TIMESTAMPTZ,
    image_staging_ref TEXT,
    image_destroyed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id),
    CHECK (document_type = 'school_id' OR dob_hash IS NOT NULL),
    -- Tombstone: "cleared" means the payload is GONE. The stamp may
    -- never coexist with a live payload (Rosa M1).
    CHECK (match_payload_cleared_at IS NULL OR match_payload_encrypted IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_vfy_documents_doc_number
    ON vfy_documents(doc_number_hash);

-- Biometrics (bronze, highest sensitivity). The face template exists
-- for exactly two purposes: the 1:1 document match and the 1:N
-- duplicate search (spec Section 4). Encrypted at rest; the key is
-- never in the database. Destruction columns implement TW-654.
CREATE TABLE IF NOT EXISTS vfy_biometrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES vfy_sessions(id),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    consent_id UUID NOT NULL,
    template_encrypted BYTEA NOT NULL,
    template_key_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    destroyed_at TIMESTAMPTZ,
    destruction_trigger TEXT
        CHECK (destruction_trigger IN ('purpose_satisfied', 'dormancy_24mo',
                                       'no_longer_necessary', 'account_closure',
                                       'tier_surrender', 'verification_revocation')),
    UNIQUE (session_id),
    -- The consent must belong to the same member as the template
    -- (Rosa m4). The consent TYPE must also match the session's
    -- document class; that cross-row rule is the deferred
    -- constraint trigger further below.
    FOREIGN KEY (consent_id, user_id) REFERENCES vfy_consents(id, user_id),
    -- Tombstone pair (Rosa M1): the stamp and its trigger are set
    -- together or not at all, and a destroyed template is the empty
    -- bytea, never a live payload under a destruction stamp.
    CHECK ((destroyed_at IS NULL) = (destruction_trigger IS NULL)),
    CHECK (destroyed_at IS NULL OR template_encrypted = ''::bytea)
);
CREATE INDEX IF NOT EXISTS idx_vfy_biometrics_user
    ON vfy_biometrics(user_id) WHERE destroyed_at IS NULL;

-- Signals (bronze). Per-session fraud and quality signals: the audit
-- record of why a verdict was reached. Scores persist after images
-- are destroyed (spec Section 4).
CREATE TABLE IF NOT EXISTS vfy_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES vfy_sessions(id),
    signal_type TEXT NOT NULL
        CHECK (signal_type IN ('match_score', 'liveness_score', 'duplicate_hit',
                               'velocity', 'mrz_check', 'pdf417_agreement',
                               'front_barcode_agreement', 'ai_face_signal',
                               'capture_forensics', 'reviewer_note')),
    score NUMERIC,
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vfy_signals_session ON vfy_signals(session_id);

-- Identity anchors. The ban and dedupe anchor set: keyed HMAC of
-- normalized legal name + date of birth + document number. Every
-- cross-system reference to a verified identity uses this hash,
-- never the underlying values (spec Section 4).
CREATE TABLE IF NOT EXISTS vfy_identity_anchors (
    anchor_hash TEXT PRIMARY KEY,
    user_id UUID REFERENCES idn_users(id),
    is_banned BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vfy_anchors_user
    ON vfy_identity_anchors(user_id) WHERE user_id IS NOT NULL;

-- Age attestations (verify-service restricted store). The band is a
-- derived attestation computed at decision time. next_band_date is
-- held only inside this service and exposed by no endpoint
-- (spec Section 5.8).
CREATE TABLE IF NOT EXISTS vfy_age_attestations (
    identity_anchor_hash TEXT PRIMARY KEY
        REFERENCES vfy_identity_anchors(anchor_hash),
    band TEXT NOT NULL
        CHECK (band IN ('under_16', 'age_16_17', 'age_18_20', 'age_21_plus')),
    band_as_of DATE NOT NULL,
    next_band_date DATE,
    engine_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Set when the verification behind this attestation is dropped
    -- or destroyed (dormancy, revocation, surrender): the row stops
    -- reading as a live attestation without erasing the record that
    -- it once held (Rosa M1).
    superseded_at TIMESTAMPTZ
);

-- Current verification state (Rosa M1). Sessions are attempts; this
-- is the one row that says what the member holds NOW, and it is what
-- the retention job drops on dormancy in the same transaction that
-- tombstones the template and writes the retention action. RSN reads
-- bands and booleans from here via the verify service, never the
-- bronze rows behind it.
CREATE TABLE IF NOT EXISTS vfy_verification_states (
    identity_anchor_hash TEXT PRIMARY KEY
        REFERENCES vfy_identity_anchors(anchor_hash),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (status IN ('unverified', 'verified', 'dropped',
                          'revoked', 'surrendered')),
    current_tier SMALLINT NOT NULL DEFAULT 0
        CHECK (current_tier IN (0, 1, 2)),
    verified_at TIMESTAMPTZ,
    last_interaction_at TIMESTAMPTZ,
    dropped_at TIMESTAMPTZ,
    drop_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Tier and status can never disagree: a tier is held exactly
    -- while the state is verified.
    CHECK ((status = 'verified') = (current_tier > 0))
);
CREATE INDEX IF NOT EXISTS idx_vfy_verification_states_user
    ON vfy_verification_states(user_id);

-- Retention actions (Rosa M1; TW-654 rule). One row per scheduled
-- or executed retention action against a biometric template or a
-- document artifact. This table IS the destruction log Angela's
-- package treats as a legal record: it survives the subject rows
-- because destruction here is tombstoning in place (the payload is
-- emptied, the row and its history stay), and subject rows are
-- never hard-deleted while an action references them (the FKs are
-- NO ACTION on purpose).
--
-- determined_at is when the trigger condition was determined (for
-- no_longer_necessary: the determination itself; for dormancy_24mo:
-- 24 months after last_interaction_at). due_at is the deadline the
-- rule sets (determined_at + 45 days for no_longer_necessary, the
-- 24-month date for dormancy); extended_due_at records the one
-- 45-day complexity extension TW-654 allows after an annual
-- necessity review. hold/hold_reason record a counsel-confirmed
-- hold, the only lawful reason an overdue action stays unexecuted.
-- notice_sent_at and tier_dropped_at carry the dormancy flow: the
-- member is told, and the tier drop lands in
-- vfy_verification_states in the same transaction.
CREATE TABLE IF NOT EXISTS vfy_retention_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id),
    biometric_id UUID REFERENCES vfy_biometrics(id),
    document_id UUID REFERENCES vfy_documents(id),
    action TEXT NOT NULL
        CHECK (action IN ('destroy_biometric', 'clear_document_payload',
                          'destroy_document_image')),
    trigger TEXT NOT NULL
        CHECK (trigger IN ('purpose_satisfied', 'dormancy_24mo',
                           'no_longer_necessary', 'account_closure',
                           'tier_surrender', 'verification_revocation')),
    determined_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    extended_due_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    hold BOOLEAN NOT NULL DEFAULT FALSE,
    hold_reason TEXT,
    notice_sent_at TIMESTAMPTZ,
    tier_dropped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (biometric_id IS NOT NULL OR document_id IS NOT NULL),
    CHECK (action <> 'destroy_biometric' OR biometric_id IS NOT NULL),
    CHECK (action = 'destroy_biometric' OR document_id IS NOT NULL),
    CHECK (hold IS FALSE OR hold_reason IS NOT NULL),
    CHECK (due_at >= determined_at)
);
-- The retention job's work queue: unexecuted actions by deadline.
CREATE INDEX IF NOT EXISTS idx_vfy_retention_actions_open
    ON vfy_retention_actions(due_at) WHERE executed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_vfy_retention_actions_user
    ON vfy_retention_actions(user_id);

-- Consent-type match (Rosa m4), enforced in DDL because a consent is
-- a legal instrument and no service bug may misfile one: a session
-- whose document class is school_id must ride on a
-- school_id_biometric_capture consent, and every other class on a
-- biometric_capture consent. Deferred constraint triggers check at
-- commit, from both sides, because capture order within a session
-- is not fixed: whichever of the document or the biometric lands
-- second completes the pair and is checked against the first.
CREATE OR REPLACE FUNCTION vfy_assert_consent_type_match() RETURNS trigger AS $$
DECLARE
    v_doc_type TEXT;
    v_consent_type TEXT;
BEGIN
    SELECT document_type INTO v_doc_type
      FROM vfy_documents WHERE session_id = NEW.session_id;
    IF v_doc_type IS NULL THEN
        RETURN NULL;  -- no document yet; its trigger re-checks later
    END IF;
    SELECT c.consent_type INTO v_consent_type
      FROM vfy_biometrics b
      JOIN vfy_consents c ON c.id = b.consent_id
     WHERE b.session_id = NEW.session_id;
    IF v_consent_type IS NULL THEN
        RETURN NULL;  -- no biometric yet; its trigger re-checks later
    END IF;
    IF (v_doc_type = 'school_id')
       <> (v_consent_type = 'school_id_biometric_capture') THEN
        RAISE EXCEPTION
            'consent type % does not match document class % for session %',
            v_consent_type, v_doc_type, NEW.session_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vfy_biometrics_consent_type ON vfy_biometrics;
CREATE CONSTRAINT TRIGGER trg_vfy_biometrics_consent_type
    AFTER INSERT OR UPDATE OF consent_id, session_id ON vfy_biometrics
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION vfy_assert_consent_type_match();

DROP TRIGGER IF EXISTS trg_vfy_documents_consent_type ON vfy_documents;
CREATE CONSTRAINT TRIGGER trg_vfy_documents_consent_type
    AFTER INSERT OR UPDATE OF document_type, session_id ON vfy_documents
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION vfy_assert_consent_type_match();

-- RLS: deny-all. The verify service role is the only reader/writer.
ALTER TABLE vfy_engine_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_biometrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_identity_anchors ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_age_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_verification_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_retention_actions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_engine_versions_deny_all') THEN
        CREATE POLICY vfy_engine_versions_deny_all ON vfy_engine_versions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_consents_deny_all') THEN
        CREATE POLICY vfy_consents_deny_all ON vfy_consents FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_sessions_deny_all') THEN
        CREATE POLICY vfy_sessions_deny_all ON vfy_sessions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_documents_deny_all') THEN
        CREATE POLICY vfy_documents_deny_all ON vfy_documents FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_biometrics_deny_all') THEN
        CREATE POLICY vfy_biometrics_deny_all ON vfy_biometrics FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_signals_deny_all') THEN
        CREATE POLICY vfy_signals_deny_all ON vfy_signals FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_identity_anchors_deny_all') THEN
        CREATE POLICY vfy_identity_anchors_deny_all ON vfy_identity_anchors FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_age_attestations_deny_all') THEN
        CREATE POLICY vfy_age_attestations_deny_all ON vfy_age_attestations FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_verification_states_deny_all') THEN
        CREATE POLICY vfy_verification_states_deny_all ON vfy_verification_states FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'vfy_retention_actions_deny_all') THEN
        CREATE POLICY vfy_retention_actions_deny_all ON vfy_retention_actions FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order)
-- =============================================================
-- DROP TRIGGER IF EXISTS trg_vfy_documents_consent_type ON vfy_documents;
-- DROP TRIGGER IF EXISTS trg_vfy_biometrics_consent_type ON vfy_biometrics;
-- DROP FUNCTION IF EXISTS vfy_assert_consent_type_match();
-- DROP TABLE IF EXISTS vfy_retention_actions;
-- DROP TABLE IF EXISTS vfy_verification_states;
-- DROP TABLE IF EXISTS vfy_age_attestations;
-- DROP TABLE IF EXISTS vfy_identity_anchors;
-- DROP TABLE IF EXISTS vfy_signals;
-- DROP TABLE IF EXISTS vfy_biometrics;
-- DROP TABLE IF EXISTS vfy_documents;
-- DROP TABLE IF EXISTS vfy_sessions;
-- DROP TABLE IF EXISTS vfy_consents;
-- DROP TABLE IF EXISTS vfy_engine_versions;

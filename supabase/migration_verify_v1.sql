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
-- days after storage is determined no longer necessary. The
-- destruction columns below are the record that job writes.
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
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    consent_type TEXT NOT NULL
        CHECK (consent_type IN ('biometric_capture', 'school_id_biometric_capture')),
    policy_version TEXT NOT NULL,
    retention_schedule_version TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vfy_consents_user
    ON vfy_consents(user_id) WHERE revoked_at IS NULL;

-- Verification sessions (bronze). One verification attempt.
CREATE TABLE IF NOT EXISTS vfy_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
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
    session_id UUID NOT NULL REFERENCES vfy_sessions(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL
        CHECK (document_type IN ('passport', 'drivers_license',
                                 'state_id', 'school_id')),
    issuing_region TEXT,
    name_hash TEXT NOT NULL,
    dob_hash TEXT NOT NULL,
    doc_number_hash TEXT NOT NULL,
    match_payload_encrypted BYTEA,
    match_payload_cleared_at TIMESTAMPTZ,
    image_staging_ref TEXT,
    image_destroyed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS idx_vfy_documents_doc_number
    ON vfy_documents(doc_number_hash);

-- Biometrics (bronze, highest sensitivity). The face template exists
-- for exactly two purposes: the 1:1 document match and the 1:N
-- duplicate search (spec Section 4). Encrypted at rest; the key is
-- never in the database. Destruction columns implement TW-654.
CREATE TABLE IF NOT EXISTS vfy_biometrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES vfy_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    consent_id UUID NOT NULL REFERENCES vfy_consents(id),
    template_encrypted BYTEA NOT NULL,
    template_key_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    destroyed_at TIMESTAMPTZ,
    destruction_trigger TEXT
        CHECK (destruction_trigger IN ('purpose_satisfied', 'dormancy_24mo',
                                       'no_longer_necessary', 'account_closure',
                                       'tier_surrender', 'verification_revocation')),
    UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS idx_vfy_biometrics_user
    ON vfy_biometrics(user_id) WHERE destroyed_at IS NULL;

-- Signals (bronze). Per-session fraud and quality signals: the audit
-- record of why a verdict was reached. Scores persist after images
-- are destroyed (spec Section 4).
CREATE TABLE IF NOT EXISTS vfy_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES vfy_sessions(id) ON DELETE CASCADE,
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
        REFERENCES vfy_identity_anchors(anchor_hash) ON DELETE CASCADE,
    band TEXT NOT NULL
        CHECK (band IN ('under_16', 'age_16_17', 'age_18_20', 'age_21_plus')),
    band_as_of DATE NOT NULL,
    next_band_date DATE,
    engine_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS: deny-all. The verify service role is the only reader/writer.
ALTER TABLE vfy_engine_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_biometrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_identity_anchors ENABLE ROW LEVEL SECURITY;
ALTER TABLE vfy_age_attestations ENABLE ROW LEVEL SECURITY;

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
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order)
-- =============================================================
-- DROP TABLE IF EXISTS vfy_age_attestations;
-- DROP TABLE IF EXISTS vfy_identity_anchors;
-- DROP TABLE IF EXISTS vfy_signals;
-- DROP TABLE IF EXISTS vfy_biometrics;
-- DROP TABLE IF EXISTS vfy_documents;
-- DROP TABLE IF EXISTS vfy_sessions;
-- DROP TABLE IF EXISTS vfy_consents;
-- DROP TABLE IF EXISTS vfy_engine_versions;

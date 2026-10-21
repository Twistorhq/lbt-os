-- =============================================================
-- TW-658 Twistor Verification Engine: consent-type update guard.
-- Prepared by Twistor Holdings LLC
--
-- Run AFTER migration_verify_v1.sql.
--
-- Rosa's TW-650 nit, closed here in the engine build (TW-658):
-- the v1 consent-type constraint triggers watch the biometric and
-- document rows, but nothing watches UPDATE OF consent_type on
-- vfy_consents itself. A consent whose type was edited after the
-- fact (biometric_capture rewritten to school_id_biometric_capture
-- or the reverse) would silently break the pairing the v1 triggers
-- enforce, and no later insert would ever re-check it. A consent
-- is a legal instrument (TW-654 D1); its type is part of what the
-- member agreed to, and it does not drift.
--
-- This migration adds the missing third side: a deferred
-- constraint trigger on vfy_consents that revalidates, at commit,
-- every session whose biometric rides on the edited consent. If
-- any paired document's class no longer matches the consent's new
-- type, the transaction fails. Edits to consents with no paired
-- document yet are unconstrained here; the v1 triggers check the
-- pairing when the pair completes.
--
-- Idempotent like v1: CREATE OR REPLACE FUNCTION, DROP TRIGGER
-- IF EXISTS before CREATE. RLS is untouched (v1 deny-all stands).
-- =============================================================

CREATE OR REPLACE FUNCTION vfy_assert_consent_type_after_update() RETURNS trigger AS $$
DECLARE
    v_doc_type TEXT;
    v_session_id UUID;
BEGIN
    -- Every session whose biometric rides on this consent, where a
    -- document has landed, must still pair with the consent's type.
    FOR v_session_id, v_doc_type IN
        SELECT b.session_id, d.document_type
          FROM vfy_biometrics b
          JOIN vfy_documents d ON d.session_id = b.session_id
         WHERE b.consent_id = NEW.id
    LOOP
        IF (v_doc_type = 'school_id')
           <> (NEW.consent_type = 'school_id_biometric_capture') THEN
            RAISE EXCEPTION
                'consent type % does not match document class % for session %',
                NEW.consent_type, v_doc_type, v_session_id;
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vfy_consents_consent_type ON vfy_consents;
CREATE CONSTRAINT TRIGGER trg_vfy_consents_consent_type
    AFTER UPDATE OF consent_type ON vfy_consents
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION vfy_assert_consent_type_after_update();

-- =============================================================
-- DOWN section (manual rollback)
-- =============================================================
-- DROP TRIGGER IF EXISTS trg_vfy_consents_consent_type ON vfy_consents;
-- DROP FUNCTION IF EXISTS vfy_assert_consent_type_after_update();

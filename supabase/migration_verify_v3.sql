-- =============================================================
-- TW-658 Twistor Verification Engine: session document class.
-- Prepared by Twistor Holdings LLC
--
-- Run AFTER migration_verify_v2.sql.
--
-- Rosa's TW-658 M3: vfy_sessions carried no document_type, so the
-- class a session was created with existed only as an echo in the
-- store's return dict. get_session returned the raw row, which
-- could never contain it, and the production pipeline died on a
-- KeyError at the first document submission. Design call 3
-- (class as session working state) was rejected as implemented: a
-- working-state field only one store can reconstruct is a trap.
--
-- This migration gives the class its durable, schema-true
-- carrier: a document_type column on vfy_sessions, written at
-- creation, read back on every fetch, in both store
-- implementations. The per-document record stays on
-- vfy_documents.document_type, written at submission.
--
-- The column is nullable: sessions created before this migration
-- carry no class, and the CHECK never fabricates one for them.
-- For new rows the engine always writes the class, and the CHECK
-- holds it to the same set vfy_documents allows.
--
-- Idempotent like v1 and v2: ADD COLUMN IF NOT EXISTS, and the
-- CHECK is added inside a guarded DO block. RLS is untouched
-- (v1 deny-all stands).
-- =============================================================

ALTER TABLE vfy_sessions ADD COLUMN IF NOT EXISTS document_type TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'vfy_sessions_document_type_check'
    ) THEN
        ALTER TABLE vfy_sessions
            ADD CONSTRAINT vfy_sessions_document_type_check
            CHECK (document_type IS NULL OR document_type IN
                   ('passport', 'drivers_license', 'state_id', 'school_id'));
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback)
-- =============================================================
-- ALTER TABLE vfy_sessions
--     DROP CONSTRAINT IF EXISTS vfy_sessions_document_type_check;
-- ALTER TABLE vfy_sessions DROP COLUMN IF EXISTS document_type;

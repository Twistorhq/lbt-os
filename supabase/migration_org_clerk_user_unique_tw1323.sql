-- TW-1323 (Rex F1, CRITICAL): duplicate organizations permanently wedge
-- a Clerk user. organizations.clerk_user_id carried only a plain INDEX
-- (migration_add_clerk_user_id.sql); the check-then-insert race in
-- create_organization / bootstrap_demo_org could insert a second row,
-- after which get_auth's maybe_single() raised PGRST116 on EVERY
-- authed route — including the POST /organizations recovery path.
--
-- This migration adds the missing partial UNIQUE index. It REFUSES to
-- apply while duplicates exist: which row survives a dedupe is a human
-- decision (the code fix keeps the OLDEST row), never a silent merge.
--
-- Pre-flight (run first, expect 0 rows):
--   SELECT clerk_user_id, COUNT(*)
--     FROM organizations
--    WHERE clerk_user_id IS NOT NULL
--    GROUP BY clerk_user_id HAVING COUNT(*) > 1;
--
-- Apply AFTER supabase/schema.sql and
-- supabase/migration_add_clerk_user_id.sql.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM organizations
     WHERE clerk_user_id IS NOT NULL
     GROUP BY clerk_user_id
    HAVING COUNT(*) > 1
  ) THEN
    RAISE EXCEPTION 'TW-1323: duplicate organizations.clerk_user_id rows exist — dedupe (keep the oldest row per user) before applying this migration';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS organizations_clerk_user_id_key
  ON organizations (clerk_user_id)
  WHERE clerk_user_id IS NOT NULL;

-- DOWN (manual rollback):
--   DROP INDEX IF EXISTS organizations_clerk_user_id_key;

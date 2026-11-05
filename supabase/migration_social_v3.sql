-- =============================================================
-- TW-940 Twistor Social v3: Safety Desk Minimum.
--
-- STATUS: SHIPPED AS A FILE ONLY. NOT APPLIED. Do not apply
-- without Justynn's explicit word (standing rule: no migration
-- applied by an agent on this ticket). The Safety Desk endpoints
-- in backend/app/social (TW-940) read and write the objects below;
-- they must not be deployed as live until this migration has been
-- applied and verified, because rsn_safety_staff is the desk's
-- authorization record and it does not exist before this file runs.
--
-- What this adds, and why:
-- 1. rsn_safety_staff: the staff role record for the safety desk.
--    Social had ambassador role rows (rsn_ambassadors, v1) but no
--    staff record at all. 'staff' may triage, sanction, build
--    evidence packets, and intake disclosures; 'safety_lead' may
--    additionally ban, decide appeals, set legal holds, and decide
--    disclosures. Rows are provisioned by hand; there is no
--    self-service path. RLS deny-all, service role only, same
--    posture as v1 / v2.
-- 2. rsn_reports: resolution_note (the recorded resolution reason,
--    required for ambassador unfounded closures per the TW-648
--    design) and metro (intake-resolved case metro so ambassador
--    queue scoping can fail closed: no metro, staff only).
-- 3. rsn_sanctions: level vocabulary gains 'strike' and
--    'suspension', the desk's sanction actions (TW-940 ticket
--    vocabulary), alongside the existing ladder rungs 'warning'
--    and 'restriction' and the integrity pipeline's 'purge'; plus
--    a staff-side note column. A suspension row carries
--    expires_at and is capped at 72 hours by the service layer
--    (spec Section 6.2).
-- 4. rsn_bans: source_report_id (the case a ban issued from, so a
--    report can be resolved 'actioned' on a linked ban and the
--    disclosure view can assemble a case) and a staff-side note.
-- 5. rsn_appeals: ack_at (the Section 6.6 24-hour acknowledgement,
--    as a timestamp rather than a status alone) and decided_by
--    (the fresh reviewer who decided; the issuer may not decide).
-- 6. rsn_evidence_packets: retention_until (evidence retention is
--    a deadline, set at packet creation from the desk's retention
--    window; legal_hold already exists in v1 and freezes the
--    manifest at the service layer).
-- 7. rsn_disclosures: status (pending_review / disclosed / denied)
--    so an intake record is distinguishable from a completed
--    disclosure; disclosed_at (v1) stays NULL until disclosure
--    actually happens off-system. This workflow sends nothing.
--
-- Idempotent in the v1 / v2 style: IF NOT EXISTS / DROP ... IF
-- EXISTS guards throughout, safe to re-run.
-- =============================================================

-- 1. Staff role record -------------------------------------------
CREATE TABLE IF NOT EXISTS rsn_safety_staff (
    user_id UUID PRIMARY KEY REFERENCES idn_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'staff'
        CHECK (role IN ('staff', 'safety_lead')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'revoked')),
    granted_by UUID REFERENCES idn_users(id),
    status_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Reports: resolution reason + case metro ----------------------
ALTER TABLE rsn_reports ADD COLUMN IF NOT EXISTS resolution_note TEXT;
ALTER TABLE rsn_reports ADD COLUMN IF NOT EXISTS metro TEXT;

-- 3. Sanctions: desk levels + note --------------------------------
ALTER TABLE rsn_sanctions DROP CONSTRAINT IF EXISTS rsn_sanctions_level_check;
ALTER TABLE rsn_sanctions
    ADD CONSTRAINT rsn_sanctions_level_check
    CHECK (level IN ('warning', 'restriction', 'purge', 'strike', 'suspension'));
ALTER TABLE rsn_sanctions ADD COLUMN IF NOT EXISTS note TEXT;

-- 4. Bans: case linkage + note -------------------------------------
ALTER TABLE rsn_bans ADD COLUMN IF NOT EXISTS source_report_id UUID
    REFERENCES rsn_reports(id);
ALTER TABLE rsn_bans ADD COLUMN IF NOT EXISTS note TEXT;
CREATE INDEX IF NOT EXISTS idx_rsn_bans_source_report
    ON rsn_bans(source_report_id);

-- 5. Appeals: acknowledgement + decider ----------------------------
ALTER TABLE rsn_appeals ADD COLUMN IF NOT EXISTS ack_at TIMESTAMPTZ;
ALTER TABLE rsn_appeals ADD COLUMN IF NOT EXISTS decided_by UUID
    REFERENCES idn_users(id);

-- 6. Evidence packets: retention deadline ---------------------------
ALTER TABLE rsn_evidence_packets
    ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ;

-- 7. Disclosures: workflow status -----------------------------------
ALTER TABLE rsn_disclosures ADD COLUMN IF NOT EXISTS status TEXT
    NOT NULL DEFAULT 'pending_review'
    CHECK (status IN ('pending_review', 'disclosed', 'denied'));

-- RLS: deny-all. The service role is the only reader/writer.
ALTER TABLE rsn_safety_staff ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'rsn_safety_staff_deny_all') THEN
        CREATE POLICY rsn_safety_staff_deny_all ON rsn_safety_staff FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order)
-- =============================================================
-- ALTER TABLE rsn_disclosures DROP COLUMN IF EXISTS status;
-- ALTER TABLE rsn_evidence_packets DROP COLUMN IF EXISTS retention_until;
-- ALTER TABLE rsn_appeals DROP COLUMN IF EXISTS decided_by;
-- ALTER TABLE rsn_appeals DROP COLUMN IF EXISTS ack_at;
-- ALTER TABLE rsn_bans DROP COLUMN IF EXISTS note;
-- ALTER TABLE rsn_bans DROP COLUMN IF EXISTS source_report_id;
-- ALTER TABLE rsn_sanctions DROP COLUMN IF EXISTS note;
-- ALTER TABLE rsn_sanctions DROP CONSTRAINT IF EXISTS rsn_sanctions_level_check;
-- ALTER TABLE rsn_sanctions ADD CONSTRAINT rsn_sanctions_level_check
--     CHECK (level IN ('warning', 'restriction', 'purge'));
-- ALTER TABLE rsn_reports DROP COLUMN IF EXISTS metro;
-- ALTER TABLE rsn_reports DROP COLUMN IF EXISTS resolution_note;
-- DROP TABLE IF EXISTS rsn_safety_staff;

-- =============================================================
-- TW-800 Twistor Trades agent tool gateway (TW-764 Spec 2 companion).
--
-- The gateway sits in front of MCP-style tool calls (the Flows MCP
-- surface, Notify sends): plugbear's shape, adopted under MIT from
-- mikecann/plugbear - an ordered policy decides allow / ask / deny
-- per call, "ask" parks the call for an owner's per-call approval,
-- and every call lands in an append-only audit log.
--
-- The gateway holds NO permission truth of its own. A tool bound to
-- an agent action class composes with the TW-768 ledger
-- (agt_grants): the ledger check runs at invoke time AND again at
-- execution time after an approval, so a revocation that lands
-- while a call waits for approval still fences it.
--
-- agt_tool_calls is append-only and hash-chained per org: each row
-- carries the previous row's hash as the chain link, and row_hash
-- covers the row's canonical content - org, call, seq, kind, tool,
-- action class, channel, actor, grant attribution (grant_id), the
-- args fingerprint and key names, the detail payload, and the row
-- timestamp normalized to UTC Z form before hashing (the write and
-- verify paths share one content function, so a PostgREST '+00:00'
-- rendering of the same instant verifies identically). Rewriting
-- any covered field, or deleting a row, breaks verification
-- (service.verify_chain). Outside the hashed content: the
-- surrogate id, and prev_hash itself - prev_hash is the link
-- input, and a rewritten prev_hash fails the next row's link
-- check. The audit rows store an args fingerprint (sha256 + key
-- names), never arg values - the values live only on the approval
-- row, which needs them to execute after the owner approves, and
-- only until it is decided.
--
-- RLS deny-all (service role only). The backend filters org_id
-- manually on every query (database.py discipline).
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Append-only per-call audit. One row per call event; rows for one
-- call share call_id. seq is per-org monotonic; the unique
-- constraint makes a chain collision fail the write, and the
-- gateway fails the call closed when its audit write fails.
CREATE TABLE IF NOT EXISTS agt_tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    call_id UUID NOT NULL,
    seq BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'requested', 'approval_requested', 'approved', 'approval_denied',
        'approval_expired', 'executed', 'denied', 'failed'
    )),
    tool TEXT NOT NULL,
    action_class TEXT CHECK (action_class IS NULL OR action_class IN (
        'email.send', 'sms.send', 'call.outbound',
        'booking.create', 'booking.reschedule',
        'payment.link_send', 'payment.charge', 'purchase'
    )),
    channel TEXT,
    actor TEXT NOT NULL,
    grant_id UUID,
    args_hash TEXT NOT NULL,
    args_keys JSONB NOT NULL DEFAULT '[]',
    detail JSONB NOT NULL DEFAULT '{}',
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT agt_tool_calls_org_seq_unique UNIQUE (org_id, seq),
    CONSTRAINT agt_tool_calls_org_hash_unique UNIQUE (org_id, row_hash)
);
CREATE INDEX IF NOT EXISTS idx_agt_tool_calls_org_call ON agt_tool_calls(org_id, call_id);
CREATE INDEX IF NOT EXISTS idx_agt_tool_calls_org_at ON agt_tool_calls(org_id, at DESC);

-- Parked calls awaiting an owner's per-call decision ("ask" mode).
-- args are stored here, and only here, because execution after an
-- approval needs them; decided rows are terminal state, never
-- re-executed.
CREATE TABLE IF NOT EXISTS agt_tool_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    call_id UUID NOT NULL UNIQUE,
    tool TEXT NOT NULL,
    action_class TEXT CHECK (action_class IS NULL OR action_class IN (
        'email.send', 'sms.send', 'call.outbound',
        'booking.create', 'booking.reschedule',
        'payment.link_send', 'payment.charge', 'purchase'
    )),
    args JSONB NOT NULL DEFAULT '{}',
    args_hash TEXT NOT NULL,
    amount_cents BIGINT CHECK (amount_cents IS NULL OR amount_cents >= 0),
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'approved', 'denied', 'expired', 'executed', 'fenced',
        'execution_failed', 'tampered'
    )),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agt_tool_approvals_org_status
    ON agt_tool_approvals(org_id, status);

-- RLS: deny-all. The service role (backend) is the only writer/reader.
ALTER TABLE agt_tool_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_tool_approvals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_tool_calls_deny_all'
    ) THEN
        CREATE POLICY agt_tool_calls_deny_all ON agt_tool_calls FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_tool_approvals_deny_all'
    ) THEN
        CREATE POLICY agt_tool_approvals_deny_all
            ON agt_tool_approvals FOR ALL USING (false);
    END IF;
END $$;

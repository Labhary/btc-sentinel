PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (3, '2026-09-02T00:00:00Z');

CREATE TABLE IF NOT EXISTS state_api_nonces (
    nonce TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_state_api_nonces_expiry
ON state_api_nonces(expires_at);

CREATE TABLE IF NOT EXISTS workflow_dispatches (
    dispatch_key TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('CLAIMED', 'SENT', 'FAILED')),
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS ix_workflow_dispatches_time
ON workflow_dispatches(scheduled_at DESC);

CREATE TRIGGER IF NOT EXISTS state_api_nonces_no_update
BEFORE UPDATE ON state_api_nonces
BEGIN
    SELECT RAISE(ABORT, 'state API nonces are immutable');
END;

CREATE TRIGGER IF NOT EXISTS workflow_dispatches_identity_immutable
BEFORE UPDATE ON workflow_dispatches
WHEN
    NEW.dispatch_key IS NOT OLD.dispatch_key OR
    NEW.scheduled_at IS NOT OLD.scheduled_at OR
    NEW.claimed_at IS NOT OLD.claimed_at
BEGIN
    SELECT RAISE(ABORT, 'workflow dispatch identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS workflow_dispatches_no_delete
BEFORE DELETE ON workflow_dispatches
BEGIN
    SELECT RAISE(ABORT, 'workflow dispatch history cannot be deleted');
END;

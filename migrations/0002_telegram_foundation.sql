PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (2, '2026-08-02T00:00:00Z');

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (
        processing_status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'IGNORED')
    ),
    lease_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_audit (
    audit_id TEXT PRIMARY KEY,
    update_id INTEGER NOT NULL UNIQUE REFERENCES telegram_updates(update_id),
    command TEXT NOT NULL CHECK (
        command IN ('START', 'HELP', 'STATUS', 'PAUSE', 'RESUME', 'UNKNOWN')
    ),
    occurred_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK (
        result IN ('SENT', 'DELIVERY_FAILED', 'DELIVERY_UNKNOWN', 'NO_RESPONSE')
    )
);

CREATE INDEX IF NOT EXISTS ix_command_audit_time
ON command_audit(occurred_at DESC);

CREATE TRIGGER IF NOT EXISTS telegram_updates_identity_immutable
BEFORE UPDATE ON telegram_updates
WHEN NEW.update_id IS NOT OLD.update_id OR NEW.received_at IS NOT OLD.received_at
BEGIN
    SELECT RAISE(ABORT, 'telegram update identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS telegram_updates_no_delete
BEFORE DELETE ON telegram_updates
BEGIN
    SELECT RAISE(ABORT, 'telegram update IDs cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS command_audit_no_update
BEFORE UPDATE ON command_audit
BEGIN
    SELECT RAISE(ABORT, 'command audit is append-only');
END;

CREATE TRIGGER IF NOT EXISTS command_audit_no_delete
BEFORE DELETE ON command_audit
BEGIN
    SELECT RAISE(ABORT, 'command audit is append-only');
END;

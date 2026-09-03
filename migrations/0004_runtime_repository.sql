PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (4, '2026-09-03T00:00:00Z');

CREATE TABLE IF NOT EXISTS runtime_mutations (
    dedupe_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_assertions (
    assertion_id INTEGER PRIMARY KEY,
    changed_rows INTEGER NOT NULL CHECK (changed_rows = 1)
);

CREATE TRIGGER IF NOT EXISTS runtime_mutations_no_update
BEFORE UPDATE ON runtime_mutations
BEGIN
    SELECT RAISE(ABORT, 'runtime mutation receipts are immutable');
END;

CREATE TRIGGER IF NOT EXISTS runtime_mutations_no_delete
BEFORE DELETE ON runtime_mutations
BEGIN
    SELECT RAISE(ABORT, 'runtime mutation receipts are immutable');
END;

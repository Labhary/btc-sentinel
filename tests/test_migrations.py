import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    ROOT / "migrations" / "0001_initial.sql",
    ROOT / "migrations" / "0002_telegram_foundation.sql",
]


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "migrations.sqlite3"
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def migrate_all(self) -> None:
        for migration in MIGRATIONS:
            self.connection.executescript(migration.read_text(encoding="utf-8"))

    def test_all_migrations_are_replayable(self) -> None:
        self.migrate_all()
        self.migrate_all()

        versions = self.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual([row["version"] for row in versions], [1, 2])

    def test_telegram_update_identity_is_immutable(self) -> None:
        self.migrate_all()
        self.connection.execute(
            """
            INSERT INTO telegram_updates(
                update_id, received_at, processing_status, lease_until,
                attempt_count, updated_at
            ) VALUES (1, '2026-08-02T12:00:00Z', 'PROCESSING', NULL, 1,
                      '2026-08-02T12:00:00Z')
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE telegram_updates SET update_id = 2 WHERE update_id = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM telegram_updates WHERE update_id = 1")

    def test_command_audit_is_append_only(self) -> None:
        self.migrate_all()
        self.connection.execute(
            """
            INSERT INTO telegram_updates(
                update_id, received_at, processing_status, lease_until,
                attempt_count, updated_at
            ) VALUES (1, '2026-08-02T12:00:00Z', 'COMPLETED', NULL, 1,
                      '2026-08-02T12:00:00Z')
            """
        )
        self.connection.execute(
            """
            INSERT INTO command_audit(audit_id, update_id, command, occurred_at, result)
            VALUES ('audit-1', 1, 'STATUS', '2026-08-02T12:00:00Z', 'SENT')
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE command_audit SET result = 'DELIVERY_FAILED' WHERE audit_id = 'audit-1'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM command_audit WHERE audit_id = 'audit-1'")


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from btc_sentinel.domain.enums import (
    OutcomeResult,
    OutcomeVariant,
    SignalStatus,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.errors import (
    DuplicateRecordError,
    InvalidTransitionError,
    SecurityError,
)
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from tests.factories import long_signal, short_signal

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"
NOW = datetime(2026, 8, 2, 1, 30, tzinfo=UTC)


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "test.sqlite3"
        self.repository = SqliteRepository(self.database, MIGRATION)
        self.repository.migrate()

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_migration_is_idempotent(self) -> None:
        self.repository.migrate()
        row = self.repository._connection.execute(
            "SELECT COUNT(*) AS count FROM schema_migrations WHERE version = 1"
        ).fetchone()
        self.assertEqual(row["count"], 1)

    def test_allocates_monotonic_daily_ids(self) -> None:
        self.assertEqual(self.repository.allocate_signal_id(date(2026, 8, 2)), "BTC-20260802-001")
        self.assertEqual(self.repository.allocate_signal_id(date(2026, 8, 2)), "BTC-20260802-002")
        self.assertEqual(self.repository.allocate_signal_id(date(2026, 8, 3)), "BTC-20260803-001")

    def test_create_signal_is_audited_and_duplicate_safe(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        self.assertIs(self.repository.get_signal_status(signal.signal_id), SignalStatus.PENDING)
        self.assertEqual(self.repository.count_events(signal.signal_id), 1)
        with self.assertRaises(DuplicateRecordError):
            self.repository.create_signal(signal)

    def test_original_terms_cannot_be_rewritten(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute(
                "UPDATE signals SET original_stop = '99' WHERE signal_id = ?",
                (signal.signal_id,),
            )

    def test_events_cannot_be_updated_or_deleted(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute(
                "DELETE FROM trade_events WHERE signal_id = ?", (signal.signal_id,)
            )

    def test_activation_creates_two_independent_tracks(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        self.repository.activate_signal(
            signal.signal_id, Decimal("101"), NOW, f"{signal.signal_id}:activate"
        )
        self.assertIs(self.repository.get_signal_status(signal.signal_id), SignalStatus.ACTIVE)
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.MANAGED),
            TrackStatus.ACTIVE,
        )
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.FIXED),
            TrackStatus.ACTIVE,
        )
        self.assertEqual(self.repository.count_events(signal.signal_id), 2)

    def test_only_one_managed_btc_signal_can_be_active(self) -> None:
        first = long_signal("BTC-20260802-001")
        second = short_signal("BTC-20260802-002")
        self.repository.create_signal(first)
        self.repository.create_signal(second)
        self.repository.activate_signal(first.signal_id, Decimal("101"), NOW, "first:activate")
        with self.assertRaises(DuplicateRecordError):
            self.repository.activate_signal(
                second.signal_id, Decimal("99"), NOW + timedelta(minutes=1), "second:activate"
            )
        self.assertIs(self.repository.get_signal_status(second.signal_id), SignalStatus.PENDING)

    def test_managed_close_does_not_fabricate_fixed_outcome(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        self.repository.activate_signal(signal.signal_id, Decimal("101"), NOW, "activate")
        self.repository.close_track(
            signal_id=signal.signal_id,
            variant=OutcomeVariant.MANAGED,
            result=OutcomeResult.EARLY_EXIT,
            result_r=Decimal("0.4"),
            result_percent=Decimal("0.20"),
            close_reason="Verified structural invalidation.",
            close_event=TradeEventType.EARLY_EXIT,
            price=Decimal("103.5"),
            occurred_at=NOW + timedelta(hours=1),
            dedupe_key="managed:close",
        )
        self.assertIs(self.repository.get_signal_status(signal.signal_id), SignalStatus.CLOSED)
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.MANAGED),
            TrackStatus.CLOSED,
        )
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.FIXED),
            TrackStatus.ACTIVE,
        )
        outcomes = self.repository._connection.execute(
            "SELECT variant FROM outcomes WHERE signal_id = ?", (signal.signal_id,)
        ).fetchall()
        self.assertEqual([row["variant"] for row in outcomes], ["MANAGED"])

    def test_fixed_track_can_close_after_managed_signal(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        self.repository.activate_signal(signal.signal_id, Decimal("101"), NOW, "activate")
        self.repository.close_track(
            signal.signal_id,
            OutcomeVariant.MANAGED,
            OutcomeResult.EARLY_EXIT,
            Decimal("0.4"),
            Decimal("0.2"),
            "Verified structural invalidation.",
            TradeEventType.EARLY_EXIT,
            Decimal("103.5"),
            NOW + timedelta(hours=1),
            "managed",
        )
        self.repository.close_track(
            signal.signal_id,
            OutcomeVariant.FIXED,
            OutcomeResult.WIN,
            Decimal("3"),
            Decimal("1.5"),
            "Original TP2 reached.",
            TradeEventType.TP2_HIT,
            Decimal("120"),
            NOW + timedelta(hours=3),
            "fixed",
        )
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.FIXED),
            TrackStatus.CLOSED,
        )
        count = self.repository._connection.execute(
            "SELECT COUNT(*) AS count FROM outcomes WHERE signal_id = ?", (signal.signal_id,)
        ).fetchone()["count"]
        self.assertEqual(count, 2)

    def test_managed_close_allows_a_new_signal_while_fixed_track_continues(self) -> None:
        first = long_signal("BTC-20260802-001")
        second = short_signal("BTC-20260802-002")
        self.repository.create_signal(first)
        self.repository.create_signal(second)
        self.repository.activate_signal(first.signal_id, Decimal("101"), NOW, "first:activate")
        self.repository.close_track(
            first.signal_id,
            OutcomeVariant.MANAGED,
            OutcomeResult.BREAK_EVEN,
            Decimal("0"),
            Decimal("0"),
            "Protected stop reached.",
            TradeEventType.BREAK_EVEN,
            Decimal("101"),
            NOW + timedelta(hours=1),
            "first:managed:close",
        )
        self.repository.activate_signal(
            second.signal_id,
            Decimal("99"),
            NOW + timedelta(hours=1, minutes=5),
            "second:activate",
        )
        self.assertIs(self.repository.get_signal_status(second.signal_id), SignalStatus.ACTIVE)
        self.assertIs(
            self.repository.get_track_status(first.signal_id, OutcomeVariant.FIXED),
            TrackStatus.ACTIVE,
        )

    def test_expired_signal_cannot_activate(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        self.repository.expire_signal(signal.signal_id, NOW, "expire")
        self.assertIs(self.repository.get_signal_status(signal.signal_id), SignalStatus.EXPIRED)
        with self.assertRaises(InvalidTransitionError):
            self.repository.activate_signal(signal.signal_id, Decimal("101"), NOW, "activate")

    def test_outbox_deduplicates_messages(self) -> None:
        self.repository.enqueue_alert("HEALTH", {"status": "ok"}, "health:1", NOW)
        with self.assertRaises(DuplicateRecordError):
            self.repository.enqueue_alert("HEALTH", {"status": "ok"}, "health:1", NOW)
        self.assertEqual(self.repository.count_outbox(), 1)

    def test_operational_settings_cannot_store_secrets_or_identity(self) -> None:
        self.repository.put_setting("PAUSED", "true", NOW)
        self.assertEqual(self.repository.get_setting("paused"), "true")
        with self.assertRaises(SecurityError):
            self.repository.put_setting("TELEGRAM_BOT_TOKEN", "forbidden", NOW)
        with self.assertRaises(SecurityError):
            self.repository.put_setting("ADMIN_USER_ID", "forbidden", NOW)


if __name__ == "__main__":
    unittest.main()

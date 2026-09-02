import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from btc_sentinel.domain.enums import (
    OutcomeResult,
    OutcomeVariant,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from btc_sentinel.statistics import OutcomeSample, calculate_statistics
from tests.factories import long_signal

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def sample(
    signal_id: str,
    variant: OutcomeVariant,
    result: OutcomeResult,
    result_r: str,
    offset: int,
    strategy_version: str = "rules-v0.6.0",
) -> OutcomeSample:
    return OutcomeSample(
        signal_id=signal_id,
        variant=variant,
        result=result,
        result_r=Decimal(result_r),
        closed_at=NOW + timedelta(minutes=offset),
        strategy_version=strategy_version,
    )


class StatisticsCalculatorTests(TestCase):
    def test_strict_win_rate_does_not_relabel_break_even_or_early_exit(self) -> None:
        samples = (
            sample("BTC-20260902-001", OutcomeVariant.MANAGED, OutcomeResult.WIN, "2", 0),
            sample("BTC-20260902-002", OutcomeVariant.MANAGED, OutcomeResult.LOSS, "-1", 1),
            sample(
                "BTC-20260902-003",
                OutcomeVariant.MANAGED,
                OutcomeResult.BREAK_EVEN,
                "0",
                2,
            ),
            sample(
                "BTC-20260902-004",
                OutcomeVariant.MANAGED,
                OutcomeResult.EARLY_EXIT,
                "0.5",
                3,
            ),
        )
        managed = calculate_statistics(samples, NOW + timedelta(hours=1)).managed
        self.assertEqual(managed.resolved, 4)
        self.assertEqual(managed.wins, 1)
        self.assertEqual(managed.break_even, 1)
        self.assertEqual(managed.early_exits, 1)
        self.assertEqual(managed.strict_win_rate_percent, Decimal("25"))
        assert managed.strict_win_rate_95_low_percent is not None
        assert managed.strict_win_rate_95_high_percent is not None
        self.assertLess(managed.strict_win_rate_95_low_percent, Decimal("25"))
        self.assertGreater(managed.strict_win_rate_95_high_percent, Decimal("25"))
        self.assertEqual(managed.decisive_win_rate_percent, Decimal("50"))
        self.assertEqual(managed.positive_rate_percent, Decimal("50"))
        self.assertEqual(managed.net_r, Decimal("1.5"))
        self.assertEqual(managed.average_r, Decimal("0.375"))
        self.assertEqual(managed.median_r, Decimal("0.25"))
        self.assertEqual(managed.profit_factor, Decimal("2.5"))
        self.assertEqual(managed.max_drawdown_r, Decimal("1"))

    def test_empty_variant_rates_are_unknown_not_zero(self) -> None:
        report = calculate_statistics((), NOW)
        self.assertEqual(report.fixed.resolved, 0)
        self.assertIsNone(report.fixed.strict_win_rate_percent)
        self.assertIsNone(report.fixed.strict_win_rate_95_low_percent)
        self.assertIsNone(report.fixed.strict_win_rate_95_high_percent)
        self.assertIsNone(report.fixed.decisive_win_rate_percent)
        self.assertIsNone(report.fixed.profit_factor)

    def test_fixed_and_managed_pairs_are_compared_without_inventing_results(self) -> None:
        samples = (
            sample("BTC-20260902-001", OutcomeVariant.MANAGED, OutcomeResult.WIN, "2", 0),
            sample("BTC-20260902-001", OutcomeVariant.FIXED, OutcomeResult.WIN, "2.2", 1),
            sample(
                "BTC-20260902-002",
                OutcomeVariant.MANAGED,
                OutcomeResult.BREAK_EVEN,
                "0",
                2,
            ),
            sample("BTC-20260902-003", OutcomeVariant.FIXED, OutcomeResult.LOSS, "-1", 3),
        )
        comparison = calculate_statistics(samples, NOW + timedelta(hours=1)).comparison
        self.assertEqual(comparison.completed_pairs, 1)
        self.assertEqual(comparison.fixed_better, 1)
        self.assertEqual(comparison.managed_better, 0)
        self.assertEqual(comparison.unresolved_fixed, 1)
        self.assertEqual(comparison.unresolved_managed, 1)
        self.assertEqual(comparison.average_managed_delta_r, Decimal("-0.2"))

    def test_strategy_cohorts_remain_visible(self) -> None:
        samples = (
            sample("BTC-20260902-001", OutcomeVariant.MANAGED, OutcomeResult.WIN, "2", 0),
            sample(
                "BTC-20260902-002",
                OutcomeVariant.MANAGED,
                OutcomeResult.LOSS,
                "-1",
                1,
                "rules-v0.7.0",
            ),
        )
        report = calculate_statistics(samples, NOW + timedelta(hours=1))
        self.assertEqual(
            report.strategy_counts,
            (("rules-v0.6.0", 1), ("rules-v0.7.0", 1)),
        )


class StatisticsPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "statistics.sqlite3"
        self.repository = SqliteRepository(database, MIGRATION)
        self.repository.migrate()
        self.signal = long_signal()
        self.repository.create_signal(self.signal)
        self.repository.activate_signal(
            self.signal.signal_id,
            self.signal.terms.conservative_entry,
            NOW,
            "statistics:activate",
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def close(
        self,
        variant: OutcomeVariant,
        result: OutcomeResult,
        result_r: str,
        event: TradeEventType,
        offset: int,
    ) -> None:
        value = Decimal(result_r)
        self.repository.close_track(
            signal_id=self.signal.signal_id,
            variant=variant,
            result=result,
            result_r=value,
            result_percent=value * Decimal("0.5"),
            close_reason=f"Test {variant.value} close.",
            close_event=event,
            price=Decimal("101") + value,
            occurred_at=NOW + timedelta(minutes=offset),
            dedupe_key=f"statistics:{variant.value}:close",
        )

    def test_every_close_creates_an_atomic_append_only_snapshot(self) -> None:
        self.close(
            OutcomeVariant.MANAGED,
            OutcomeResult.BREAK_EVEN,
            "0",
            TradeEventType.BREAK_EVEN,
            1,
        )
        first = self.repository.get_latest_statistics_snapshot()
        assert first is not None
        self.assertEqual(first["triggering_variant"], OutcomeVariant.MANAGED.value)
        self.assertEqual(first["strategy_version"], "statistics-v0.9.0")
        self.assertEqual(first["payload"]["managed"]["resolved"], 1)
        self.assertEqual(first["payload"]["managed"]["wins"], 0)
        self.assertEqual(first["payload"]["managed"]["strict_win_rate_percent"], "0")
        self.assertIsNone(first["payload"]["managed"]["decisive_win_rate_percent"])
        self.assertEqual(first["payload"]["comparison"]["unresolved_fixed"], 1)

        self.close(
            OutcomeVariant.FIXED,
            OutcomeResult.WIN,
            "2",
            TradeEventType.TP1_HIT,
            2,
        )
        second = self.repository.get_latest_statistics_snapshot()
        assert second is not None
        comparison = second["payload"]["comparison"]
        self.assertEqual(second["triggering_variant"], OutcomeVariant.FIXED.value)
        self.assertEqual(comparison["completed_pairs"], 1)
        self.assertEqual(comparison["unresolved_fixed"], 0)
        self.assertEqual(comparison["fixed_better"], 1)
        self.assertEqual(comparison["average_managed_delta_r"], "-2")
        count = self.repository._connection.execute(
            "SELECT COUNT(*) AS count FROM statistics_snapshots"
        ).fetchone()["count"]
        self.assertEqual(count, 2)

    def test_statistics_snapshots_cannot_be_rewritten_or_deleted(self) -> None:
        self.close(
            OutcomeVariant.MANAGED,
            OutcomeResult.LOSS,
            "-1",
            TradeEventType.STOP_LOSS_HIT,
            1,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute(
                "UPDATE statistics_snapshots SET payload_json = '{}'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute("DELETE FROM statistics_snapshots")

    def test_inconsistent_outcome_rolls_back_close_and_snapshot(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "WIN outcome"):
            self.close(
                OutcomeVariant.MANAGED,
                OutcomeResult.WIN,
                "-1",
                TradeEventType.TP1_HIT,
                1,
            )
        self.assertIs(
            self.repository.get_track_status(self.signal.signal_id, OutcomeVariant.MANAGED),
            TrackStatus.ACTIVE,
        )
        outcome_count = self.repository._connection.execute(
            "SELECT COUNT(*) AS count FROM outcomes"
        ).fetchone()["count"]
        snapshot_count = self.repository._connection.execute(
            "SELECT COUNT(*) AS count FROM statistics_snapshots"
        ).fetchone()["count"]
        self.assertEqual(outcome_count, 0)
        self.assertEqual(snapshot_count, 0)

    def test_no_snapshot_exists_before_an_outcome(self) -> None:
        database = Path(self.temporary_directory.name) / "empty.sqlite3"
        with SqliteRepository(database, MIGRATION) as repository:
            repository.migrate()
            self.assertIsNone(repository.get_latest_statistics_snapshot())

import json
import sqlite3
import tempfile
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from btc_sentinel.domain.enums import (
    ManagementAction,
    OutcomeResult,
    OutcomeVariant,
    SignalStatus,
    TrackStatus,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.lifecycle import LifecycleReplayEngine
from btc_sentinel.management import ManagementPolicy, PositionManagementEngine
from btc_sentinel.market_data.enums import MarketVenue
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from tests.factories import long_signal, short_signal
from tests.lifecycle_fixtures import LIFECYCLE_START, after, minute_candle, minute_series

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"


class ManagementEngineTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "management.sqlite3"
        self.repository = SqliteRepository(database, MIGRATION)
        self.repository.migrate()
        self.lifecycle = LifecycleReplayEngine(self.repository)
        self.engine = PositionManagementEngine(self.repository)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def activate_long(self):
        signal = long_signal()
        self.repository.create_signal(signal)
        self.repository.activate_signal(
            signal.signal_id,
            signal.terms.conservative_entry,
            LIFECYCLE_START,
            "test:activate",
        )
        return signal

    def managed_row(self, signal_id: str):
        return self.repository._connection.execute(
            """
            SELECT * FROM trade_tracks
            WHERE signal_id = ? AND variant = 'MANAGED'
            """,
            (signal_id,),
        ).fetchone()

    def decisions(self, signal_id: str):
        return self.repository._connection.execute(
            """
            SELECT * FROM management_decisions
            WHERE signal_id = ? ORDER BY decided_at
            """,
            (signal_id,),
        ).fetchall()

    def process_candle(self, signal_id: str, candle) -> None:
        series = minute_series(candle)
        self.lifecycle.replay(signal_id, series, after(candle))
        managed_status = self.repository.get_track_status(signal_id, OutcomeVariant.MANAGED)
        if managed_status is TrackStatus.ACTIVE:
            self.engine.replay(signal_id, series, after(candle))

    def test_activation_candle_is_audited_hold(self) -> None:
        signal = self.activate_long()
        candle = minute_candle(
            0,
            open_price=Decimal("101"),
            high=Decimal("112"),
            low=Decimal("100"),
            close=Decimal("111"),
        )
        self.process_candle(signal.signal_id, candle)
        rows = self.decisions(signal.signal_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], ManagementAction.HOLD.value)
        self.assertIn("activation candle", rows[0]["reason"])
        self.assertEqual(self.managed_row(signal.signal_id)["current_stop"], "95")

    def test_profit_threshold_moves_stop_to_cost_adjusted_break_even(self) -> None:
        signal = self.activate_long()
        first = minute_candle(0)
        second = minute_candle(
            1,
            open_price=Decimal("106"),
            high=Decimal("112"),
            low=Decimal("100"),
            close=Decimal("111"),
        )
        self.process_candle(signal.signal_id, first)
        self.process_candle(signal.signal_id, second)
        row = self.managed_row(signal.signal_id)
        self.assertEqual(Decimal(row["current_stop"]), Decimal("101.1515"))
        decision = self.decisions(signal.signal_id)[-1]
        self.assertEqual(decision["action"], ManagementAction.MOVE_STOP_TO_BREAK_EVEN.value)
        evidence = json.loads(decision["evidence_json"])
        self.assertTrue(evidence["effective_from_next_candle"])

    def test_stop_change_does_not_rewrite_fixed_track(self) -> None:
        signal = self.activate_long()
        self.process_candle(signal.signal_id, minute_candle(0))
        self.process_candle(
            signal.signal_id,
            minute_candle(
                1,
                open_price=Decimal("106"),
                high=Decimal("112"),
                low=Decimal("105"),
                close=Decimal("111"),
            ),
        )
        fixed = self.repository._connection.execute(
            """
            SELECT current_stop FROM trade_tracks
            WHERE signal_id = ? AND variant = 'FIXED'
            """,
            (signal.signal_id,),
        ).fetchone()
        self.assertEqual(Decimal(fixed["current_stop"]), signal.terms.original_stop)

    def test_protected_stop_closes_managed_at_true_break_even_only_next_candle(self) -> None:
        signal = self.activate_long()
        first = minute_candle(0)
        decision_candle = minute_candle(
            1,
            open_price=Decimal("106"),
            high=Decimal("112"),
            low=Decimal("100"),
            close=Decimal("111"),
        )
        self.process_candle(signal.signal_id, first)
        self.process_candle(signal.signal_id, decision_candle)
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.MANAGED),
            TrackStatus.ACTIVE,
        )
        protected_stop = minute_candle(
            2,
            open_price=Decimal("105"),
            high=Decimal("106"),
            low=Decimal("101"),
            close=Decimal("102"),
        )
        result = self.lifecycle.replay(
            signal.signal_id, minute_series(protected_stop), after(protected_stop)
        )
        self.assertIs(result.final_status, SignalStatus.CLOSED)
        outcome = self.repository._connection.execute(
            "SELECT * FROM outcomes WHERE signal_id = ? AND variant = 'MANAGED'",
            (signal.signal_id,),
        ).fetchone()
        self.assertEqual(outcome["result"], OutcomeResult.BREAK_EVEN.value)
        self.assertEqual(Decimal(outcome["result_r"]), Decimal("0"))
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.FIXED),
            TrackStatus.ACTIVE,
        )

    def test_default_policy_never_takes_partial_profit(self) -> None:
        signal = self.activate_long()
        candles = (
            minute_candle(0),
            minute_candle(
                1,
                open_price=Decimal("106"),
                high=Decimal("112"),
                low=Decimal("105"),
                close=Decimal("111"),
            ),
            minute_candle(
                2,
                open_price=Decimal("111"),
                high=Decimal("113"),
                low=Decimal("110"),
                close=Decimal("112"),
            ),
        )
        for candle in candles:
            self.process_candle(signal.signal_id, candle)
        row = self.managed_row(signal.signal_id)
        self.assertEqual(Decimal(row["remaining_fraction"]), Decimal("1"))
        self.assertNotIn(
            ManagementAction.TAKE_PARTIAL_PROFIT.value,
            [item["action"] for item in self.decisions(signal.signal_id)],
        )

    def test_opt_in_partial_accounting_combines_realized_and_remaining_r(self) -> None:
        signal = self.activate_long()
        self.engine = PositionManagementEngine(
            self.repository,
            ManagementPolicy(partial_trigger_r=Decimal("1.7")),
        )
        candles = (
            minute_candle(0),
            minute_candle(
                1,
                open_price=Decimal("106"),
                high=Decimal("112"),
                low=Decimal("105"),
                close=Decimal("111"),
            ),
            minute_candle(
                2,
                open_price=Decimal("111"),
                high=Decimal("113"),
                low=Decimal("110"),
                close=Decimal("112"),
            ),
        )
        for candle in candles:
            self.process_candle(signal.signal_id, candle)
        managed = self.managed_row(signal.signal_id)
        self.assertEqual(Decimal(managed["remaining_fraction"]), Decimal("0.5"))
        self.assertGreater(Decimal(managed["realized_r"]), Decimal("0"))

        target = minute_candle(
            3,
            open_price=Decimal("112"),
            high=Decimal("115"),
            low=Decimal("110"),
            close=Decimal("114"),
        )
        self.lifecycle.replay(signal.signal_id, minute_series(target), after(target))
        rows = self.repository._connection.execute(
            "SELECT variant, result_r FROM outcomes WHERE signal_id = ? ORDER BY variant",
            (signal.signal_id,),
        ).fetchall()
        result_by_variant = {row["variant"]: Decimal(row["result_r"]) for row in rows}
        self.assertLess(
            result_by_variant[OutcomeVariant.MANAGED.value],
            result_by_variant[OutcomeVariant.FIXED.value],
        )

    def test_decision_replay_after_missing_checkpoint_is_idempotent(self) -> None:
        signal = self.activate_long()
        candle = minute_candle(0)
        self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        count = len(self.decisions(signal.signal_id))
        self.repository._connection.execute(
            "DELETE FROM processing_checkpoints WHERE checkpoint_key = ?",
            (f"management:{signal.signal_id}",),
        )
        result = self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        self.assertEqual(result.processed_candles, 1)
        self.assertEqual(result.decisions, ())
        self.assertEqual(len(self.decisions(signal.signal_id)), count)

    def test_management_decisions_are_append_only(self) -> None:
        signal = self.activate_long()
        candle = minute_candle(0)
        self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute(
                "UPDATE management_decisions SET reason = 'rewritten' WHERE signal_id = ?",
                (signal.signal_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository._connection.execute(
                "DELETE FROM management_decisions WHERE signal_id = ?",
                (signal.signal_id,),
            )

    def test_management_gap_fails_closed(self) -> None:
        signal = self.activate_long()
        candle = minute_candle(1)
        with self.assertRaisesRegex(DomainValidationError, "Management replay gap"):
            self.engine.replay(signal.signal_id, minute_series(candle), after(candle))

    def test_incomplete_and_futures_candles_are_rejected(self) -> None:
        signal = self.activate_long()
        candle = minute_candle(0)
        with self.assertRaisesRegex(DomainValidationError, "incomplete"):
            self.engine.replay(signal.signal_id, minute_series(candle), candle.close_time)
        futures = minute_candle(0, venue=MarketVenue.FUTURES)
        with self.assertRaisesRegex(DomainValidationError, "Spot"):
            self.engine.replay(signal.signal_id, minute_series(futures), after(futures))

    def test_short_break_even_stop_includes_costs(self) -> None:
        original = short_signal()
        signal = replace(
            original,
            terms=replace(
                original.terms,
                created_at=LIFECYCLE_START,
                data_timestamp=LIFECYCLE_START - timedelta(seconds=1),
                expires_at=LIFECYCLE_START + timedelta(hours=4),
            ),
        )
        self.repository.create_signal(signal)
        self.repository.activate_signal(
            signal.signal_id,
            signal.terms.conservative_entry,
            LIFECYCLE_START,
            "short:activate",
        )
        first = minute_candle(
            0,
            open_price=Decimal("99"),
            high=Decimal("100"),
            low=Decimal("95"),
            close=Decimal("96"),
        )
        second = minute_candle(
            1,
            open_price=Decimal("95"),
            high=Decimal("96"),
            low=Decimal("88"),
            close=Decimal("89"),
        )
        self.process_candle(signal.signal_id, first)
        self.process_candle(signal.signal_id, second)
        stop = Decimal(self.managed_row(signal.signal_id)["current_stop"])
        self.assertEqual(stop, Decimal("98.8515"))

    def test_invalid_partial_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ManagementPolicy(partial_fraction=Decimal("1"))
        with self.assertRaises(ValueError):
            ManagementPolicy(partial_trigger_r=Decimal("1.4"))

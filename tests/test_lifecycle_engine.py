import json
import tempfile
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from btc_sentinel.domain.enums import (
    OutcomeResult,
    OutcomeVariant,
    SignalStatus,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.lifecycle import LifecycleAction, LifecycleReplayEngine
from btc_sentinel.market_data.enums import MarketVenue
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from tests.factories import long_signal, short_signal
from tests.lifecycle_fixtures import (
    LIFECYCLE_START,
    after,
    minute_candle,
    minute_series,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"


class LifecycleEngineTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "lifecycle.sqlite3"
        self.repository = SqliteRepository(database, MIGRATION)
        self.repository.migrate()
        self.engine = LifecycleReplayEngine(self.repository)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def create_long(self):
        signal = long_signal()
        self.repository.create_signal(signal)
        return signal

    def outcomes(self, signal_id: str):
        return self.repository._connection.execute(
            "SELECT * FROM outcomes WHERE signal_id = ? ORDER BY variant", (signal_id,)
        ).fetchall()

    def test_replays_pending_activation_and_tp1_win(self) -> None:
        signal = self.create_long()
        candles = (
            minute_candle(0),
            minute_candle(
                1,
                open_price=Decimal("102"),
                high=Decimal("103"),
                low=Decimal("100.5"),
                close=Decimal("102"),
            ),
            minute_candle(
                2,
                open_price=Decimal("110"),
                high=Decimal("115"),
                low=Decimal("109"),
                close=Decimal("114"),
            ),
        )
        result = self.engine.replay(signal.signal_id, minute_series(*candles), after(*candles))
        self.assertEqual(
            result.actions,
            (LifecycleAction.ACTIVATED, LifecycleAction.TARGET_CLOSED),
        )
        self.assertIs(result.final_status, SignalStatus.CLOSED)
        self.assertEqual(len(self.outcomes(signal.signal_id)), 2)
        outcomes = self.outcomes(signal.signal_id)
        self.assertTrue(all(row["result"] == OutcomeResult.WIN.value for row in outcomes))
        self.assertGreater(Decimal(outcomes[0]["result_r"]), Decimal("2"))

    def test_same_candle_tp_and_stop_is_counted_as_loss(self) -> None:
        signal = self.create_long()
        candles = (
            minute_candle(
                0,
                open_price=Decimal("102"),
                high=Decimal("103"),
                low=Decimal("100"),
                close=Decimal("102"),
            ),
            minute_candle(
                1,
                open_price=Decimal("102"),
                high=Decimal("115"),
                low=Decimal("94"),
                close=Decimal("100"),
            ),
        )
        result = self.engine.replay(signal.signal_id, minute_series(*candles), after(*candles))
        self.assertIn(LifecycleAction.AMBIGUOUS_STOP_FIRST, result.actions)
        rows = self.outcomes(signal.signal_id)
        self.assertTrue(all(row["result"] == OutcomeResult.LOSS.value for row in rows))
        self.assertTrue(all(Decimal(row["result_r"]) == Decimal("-1") for row in rows))
        self.assertTrue(
            all(json.loads(row["details_json"])["ambiguous_same_candle"] for row in rows)
        )

    def test_activation_candle_stop_is_charged_conservatively(self) -> None:
        signal = self.create_long()
        candle = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("115"),
            low=Decimal("94"),
            close=Decimal("100"),
        )
        result = self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        self.assertEqual(
            result.actions,
            (LifecycleAction.ACTIVATED, LifecycleAction.AMBIGUOUS_STOP_FIRST),
        )
        self.assertIs(result.final_status, SignalStatus.CLOSED)

    def test_gap_through_stop_uses_adverse_open_price(self) -> None:
        signal = self.create_long()
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("103"),
            low=Decimal("100"),
            close=Decimal("102"),
        )
        gap = minute_candle(
            1,
            open_price=Decimal("90"),
            high=Decimal("94"),
            low=Decimal("89"),
            close=Decimal("92"),
        )
        result = self.engine.replay(
            signal.signal_id, minute_series(activation, gap), after(activation, gap)
        )
        self.assertIn(LifecycleAction.STOP_CLOSED, result.actions)
        rows = self.outcomes(signal.signal_id)
        self.assertTrue(all(Decimal(row["result_r"]) < Decimal("-1") for row in rows))
        events = self.repository._connection.execute(
            """
            SELECT price FROM trade_events
            WHERE signal_id = ? AND event_type = 'STOP_LOSS_HIT'
            """,
            (signal.signal_id,),
        ).fetchall()
        self.assertTrue(all(Decimal(row["price"]) == Decimal("90") for row in events))

    def test_activation_candle_target_is_not_credited(self) -> None:
        signal = self.create_long()
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("115"),
            low=Decimal("100"),
            close=Decimal("114"),
        )
        first = self.engine.replay(signal.signal_id, minute_series(activation), after(activation))
        self.assertEqual(
            first.actions,
            (LifecycleAction.ACTIVATED, LifecycleAction.ACTIVATION_TARGET_DEFERRED),
        )
        self.assertIs(first.final_status, SignalStatus.ACTIVE)
        target = minute_candle(
            1,
            open_price=Decimal("114"),
            high=Decimal("115"),
            low=Decimal("110"),
            close=Decimal("114"),
        )
        second = self.engine.replay(signal.signal_id, minute_series(target), after(target))
        self.assertEqual(second.actions, (LifecycleAction.TARGET_CLOSED,))

    def test_restart_before_checkpoint_preserves_activation_candle_rule(self) -> None:
        signal = self.create_long()
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("115"),
            low=Decimal("100"),
            close=Decimal("114"),
        )
        self.repository.activate_signal(
            signal.signal_id,
            signal.terms.conservative_entry,
            activation.open_time,
            "simulated-crash-after-activation",
        )
        result = self.engine.replay(signal.signal_id, minute_series(activation), after(activation))
        self.assertEqual(result.actions, (LifecycleAction.ACTIVATION_TARGET_DEFERRED,))
        self.assertIs(result.final_status, SignalStatus.ACTIVE)

    def test_expiry_wins_over_entry_when_order_inside_minute_is_unknown(self) -> None:
        signal = long_signal()
        signal = replace(
            signal,
            terms=replace(
                signal.terms,
                expires_at=LIFECYCLE_START + timedelta(minutes=1, seconds=30),
            ),
        )
        self.repository.create_signal(signal)
        candles = (
            minute_candle(0),
            minute_candle(
                1,
                open_price=Decimal("102"),
                high=Decimal("103"),
                low=Decimal("100"),
                close=Decimal("102"),
            ),
        )
        result = self.engine.replay(signal.signal_id, minute_series(*candles), after(*candles))
        self.assertIn(LifecycleAction.EXPIRED, result.actions)
        self.assertIs(result.final_status, SignalStatus.EXPIRED)

    def test_replay_is_idempotent_after_checkpoint(self) -> None:
        signal = self.create_long()
        candle = minute_candle(0)
        first = self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        event_count = self.repository.count_events(signal.signal_id)
        second = self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        self.assertEqual(first.processed_candles, 1)
        self.assertEqual(second.processed_candles, 0)
        self.assertEqual(self.repository.count_events(signal.signal_id), event_count)

    def test_gap_after_signal_creation_fails_without_checkpoint(self) -> None:
        signal = self.create_long()
        candle = minute_candle(1)
        with self.assertRaisesRegex(DomainValidationError, "replay gap"):
            self.engine.replay(signal.signal_id, minute_series(candle), after(candle))
        self.assertIsNone(self.repository.get_checkpoint(f"lifecycle:{signal.signal_id}"))

    def test_incomplete_candle_is_rejected(self) -> None:
        signal = self.create_long()
        candle = minute_candle(0)
        with self.assertRaisesRegex(DomainValidationError, "incomplete"):
            self.engine.replay(signal.signal_id, minute_series(candle), candle.close_time)

    def test_futures_candle_is_rejected(self) -> None:
        signal = self.create_long()
        candle = minute_candle(0, venue=MarketVenue.FUTURES)
        with self.assertRaisesRegex(DomainValidationError, "Spot"):
            self.engine.replay(signal.signal_id, minute_series(candle), after(candle))

    def test_short_signal_uses_conservative_lower_entry_and_tp(self) -> None:
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
        candles = (
            minute_candle(
                0,
                open_price=Decimal("98"),
                high=Decimal("100"),
                low=Decimal("97"),
                close=Decimal("98"),
            ),
            minute_candle(
                1,
                open_price=Decimal("90"),
                high=Decimal("92"),
                low=Decimal("85"),
                close=Decimal("86"),
            ),
        )
        result = self.engine.replay(signal.signal_id, minute_series(*candles), after(*candles))
        self.assertIs(result.final_status, SignalStatus.CLOSED)
        trade = self.repository._connection.execute(
            "SELECT fill_price FROM trades WHERE signal_id = ?", (signal.signal_id,)
        ).fetchone()
        self.assertEqual(Decimal(trade["fill_price"]), signal.terms.entry_low)

    def test_fixed_track_continues_after_managed_close(self) -> None:
        signal = self.create_long()
        self.repository.activate_signal(
            signal.signal_id, signal.terms.conservative_entry, LIFECYCLE_START, "activate"
        )
        self.repository.close_track(
            signal_id=signal.signal_id,
            variant=OutcomeVariant.MANAGED,
            result=OutcomeResult.EARLY_EXIT,
            result_r=Decimal("0.2"),
            result_percent=Decimal("0.1"),
            close_reason="Phase 8 simulation",
            close_event=TradeEventType.EARLY_EXIT,
            price=Decimal("103"),
            occurred_at=LIFECYCLE_START,
            dedupe_key="managed-early",
        )
        self.repository.advance_checkpoint(f"lifecycle:{signal.signal_id}", LIFECYCLE_START, {})
        target = minute_candle(
            1,
            open_price=Decimal("110"),
            high=Decimal("115"),
            low=Decimal("109"),
            close=Decimal("114"),
        )
        result = self.engine.replay(signal.signal_id, minute_series(target), after(target))
        self.assertEqual(result.actions, (LifecycleAction.TARGET_CLOSED,))
        self.assertIs(
            self.repository.get_track_status(signal.signal_id, OutcomeVariant.FIXED),
            TrackStatus.CLOSED,
        )
        self.assertEqual(len(self.outcomes(signal.signal_id)), 2)

    def test_checkpoint_cannot_move_backwards(self) -> None:
        key = "lifecycle:test"
        self.repository.advance_checkpoint(key, LIFECYCLE_START + timedelta(minutes=2), {})
        self.repository.advance_checkpoint(key, LIFECYCLE_START + timedelta(minutes=1), {})
        self.assertEqual(
            self.repository.get_checkpoint(key),
            LIFECYCLE_START + timedelta(minutes=2),
        )

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import TestCase

from btc_sentinel.backtesting import (
    BacktestComparisonReport,
    BacktestEngine,
    BacktestOutcome,
    BacktestRunSpec,
    BacktestTrade,
    BacktestVerdict,
    HistoricalSignalCase,
    WalkForwardPolicy,
    simulate_fixed_case,
    simulate_managed_case,
)
from btc_sentinel.domain.enums import MarketRegime, OutcomeVariant, Side
from btc_sentinel.errors import DomainValidationError
from tests.factories import long_signal
from tests.lifecycle_fixtures import after, minute_candle, minute_series

NOW = datetime(2027, 1, 1, tzinfo=UTC)


def policy(**changes: object) -> WalkForwardPolicy:
    values = {
        "train_size": 4,
        "test_size": 4,
        "purge_size": 1,
        "minimum_train_trades": 2,
        "minimum_out_of_sample_trades": 8,
        "minimum_folds": 2,
        "minimum_regime_trades": 2,
        "score_thresholds": (75, 80, 85),
    }
    values.update(changes)
    return WalkForwardPolicy(**values)


def run_spec() -> BacktestRunSpec:
    return BacktestRunSpec(
        dataset_id="synthetic-test-v1",
        coverage_start=datetime(2025, 12, 31, tzinfo=UTC),
        coverage_end=datetime(2026, 12, 31, tzinfo=UTC),
        strategy_version="rules-v0.6.0",
        source_coverage=("SPOT_1m", "SPOT_15m_to_1M"),
        excluded_features=("historical_order_book", "historical_liquidations"),
        exhaustive_candidate_scan=True,
    )


def trade(
    index: int,
    outcome: BacktestOutcome = BacktestOutcome.WIN,
    *,
    score: int = 90,
    regime: MarketRegime | None = None,
    result_r: str | None = None,
) -> BacktestTrade:
    created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    chosen_regime = regime or (
        MarketRegime.BULLISH_TREND if index % 2 == 0 else MarketRegime.BEARISH_TREND
    )
    if outcome is BacktestOutcome.WIN:
        result = Decimal(result_r or "2")
        exit_price = Decimal("110.45")
    elif outcome is BacktestOutcome.LOSS:
        result = Decimal(result_r or "-1")
        exit_price = Decimal("95")
    else:
        result = None
        exit_price = None
    return BacktestTrade(
        signal_id=f"BTC-20260101-{index + 1:03d}",
        created_at=created,
        terminal_at=created + timedelta(hours=1),
        side=Side.LONG,
        regime=chosen_regime,
        variant=OutcomeVariant.FIXED,
        setup_score=score,
        strategy_version="rules-v0.6.0",
        planned_rr=Decimal("2"),
        outcome=outcome,
        result_r=result,
        entry_price=Decimal("100") if result is not None else None,
        exit_price=exit_price,
        original_stop=Decimal("95"),
        estimated_cost_rate=Decimal("0.0015"),
    )


class FixedSimulatorTests(TestCase):
    def test_later_target_is_a_cost_aware_win_above_two_r(self) -> None:
        signal = long_signal()
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("103"),
            low=Decimal("100"),
            close=Decimal("102"),
        )
        target = minute_candle(
            1,
            open_price=Decimal("110"),
            high=Decimal("115"),
            low=Decimal("109"),
            close=Decimal("114"),
        )
        result = simulate_fixed_case(
            HistoricalSignalCase(
                signal, minute_series(activation, target), after(activation, target)
            )
        )
        self.assertIs(result.outcome, BacktestOutcome.WIN)
        self.assertGreaterEqual(result.result_r, Decimal("2"))
        self.assertEqual(result.entry_price, signal.terms.entry_high)

    def test_same_candle_target_and_stop_is_a_loss(self) -> None:
        candle = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("115"),
            low=Decimal("94"),
            close=Decimal("100"),
        )
        result = simulate_fixed_case(
            HistoricalSignalCase(long_signal(), minute_series(candle), after(candle))
        )
        self.assertIs(result.outcome, BacktestOutcome.LOSS)
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.result_r, Decimal("-1"))

    def test_gap_through_stop_is_worse_than_minus_one_r(self) -> None:
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("103"),
            low=Decimal("100"),
            close=Decimal("102"),
        )
        gap = minute_candle(
            1, open_price=Decimal("90"), high=Decimal("94"), low=Decimal("89"), close=Decimal("92")
        )
        result = simulate_fixed_case(
            HistoricalSignalCase(
                long_signal(), minute_series(activation, gap), after(activation, gap)
            )
        )
        self.assertIs(result.outcome, BacktestOutcome.LOSS)
        self.assertLess(result.result_r, Decimal("-1"))
        self.assertEqual(result.exit_price, Decimal("90"))

    def test_expiry_without_entry_is_no_fill_not_a_loss(self) -> None:
        original = long_signal()
        signal = replace(
            original,
            terms=replace(
                original.terms, expires_at=original.terms.created_at + timedelta(minutes=1)
            ),
        )
        first = minute_candle(0)
        second = minute_candle(1)
        result = simulate_fixed_case(
            HistoricalSignalCase(signal, minute_series(first, second), after(first, second))
        )
        self.assertIs(result.outcome, BacktestOutcome.NO_FILL)
        self.assertIsNone(result.result_r)

    def test_open_trade_at_dataset_end_is_unresolved(self) -> None:
        activation = minute_candle(
            0,
            open_price=Decimal("102"),
            high=Decimal("103"),
            low=Decimal("100"),
            close=Decimal("102"),
        )
        result = simulate_fixed_case(
            HistoricalSignalCase(long_signal(), minute_series(activation), after(activation))
        )
        self.assertIs(result.outcome, BacktestOutcome.UNRESOLVED)
        self.assertIsNone(result.result_r)

    def test_incomplete_candle_and_history_gap_fail_closed(self) -> None:
        candle = minute_candle(0)
        with self.assertRaisesRegex(DomainValidationError, "incomplete"):
            simulate_fixed_case(
                HistoricalSignalCase(long_signal(), minute_series(candle), candle.close_time)
            )
        late = minute_candle(1)
        with self.assertRaisesRegex(DomainValidationError, "first safe minute"):
            simulate_fixed_case(
                HistoricalSignalCase(long_signal(), minute_series(late), after(late))
            )

    def test_managed_break_even_uses_next_candle_and_fixed_stays_open(self) -> None:
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
                open_price=Decimal("106"),
                high=Decimal("112"),
                low=Decimal("105"),
                close=Decimal("111"),
            ),
            minute_candle(
                2,
                open_price=Decimal("108"),
                high=Decimal("109"),
                low=Decimal("101"),
                close=Decimal("102"),
            ),
        )
        case = HistoricalSignalCase(long_signal(), minute_series(*candles), after(*candles))
        fixed = simulate_fixed_case(case)
        managed = simulate_managed_case(case)
        self.assertIs(fixed.outcome, BacktestOutcome.UNRESOLVED)
        self.assertIs(managed.outcome, BacktestOutcome.BREAK_EVEN)
        self.assertEqual(managed.result_r, 0)


class WalkForwardTests(TestCase):
    def test_fixed_virtual_tracks_may_overlap_but_managed_tracks_may_not(self) -> None:
        first = trade(0)
        overlapping = replace(trade(1), created_at=first.created_at + timedelta(minutes=30))
        fixed = BacktestEngine(policy()).evaluate((first, overlapping), NOW, run_spec())
        self.assertIs(fixed.verdict, BacktestVerdict.INCONCLUSIVE)

        managed = tuple(
            replace(item, variant=OutcomeVariant.MANAGED) for item in (first, overlapping)
        )
        with self.assertRaisesRegex(DomainValidationError, "Managed backtest trades overlap"):
            BacktestEngine(policy(), OutcomeVariant.MANAGED).evaluate(managed, NOW, run_spec())

    def test_training_outcomes_resolved_after_test_start_are_not_future_knowledge(self) -> None:
        trades = tuple(
            replace(item, terminal_at=item.created_at + timedelta(days=6))
            for item in (trade(index) for index in range(13))
        )
        report = BacktestEngine(policy()).evaluate(trades, NOW, run_spec())
        self.assertIs(report.verdict, BacktestVerdict.INCONCLUSIVE)
        self.assertEqual(report.folds[0].training_resolved, 0)
        self.assertIn("insufficient resolved training", " ".join(report.reasons))

    def test_streamed_variant_comparison_requires_the_same_signal_universe(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "universes do not match"):
            BacktestEngine(policy()).compare_trades(
                (trade(0),),
                (replace(trade(1), variant=OutcomeVariant.MANAGED),),
                NOW,
                run_spec(),
            )

    def test_all_win_out_of_sample_can_pass_only_with_confident_lower_bound(self) -> None:
        report = BacktestEngine(policy()).evaluate(
            tuple(trade(index) for index in range(13)), NOW, run_spec()
        )
        self.assertIs(report.verdict, BacktestVerdict.PASSED)
        self.assertEqual(report.statistics.resolved, 8)
        self.assertEqual(report.statistics.strict_win_rate_percent, Decimal("100"))
        self.assertGreater(report.statistics.strict_win_rate_95_low_percent, Decimal("60"))
        self.assertTrue(all(item.average_r > 0 for item in report.cost_stress))

    def test_strong_training_cannot_hide_failed_out_of_sample_windows(self) -> None:
        trades = tuple(
            trade(index, BacktestOutcome.WIN if index < 5 else BacktestOutcome.LOSS)
            for index in range(13)
        )
        report = BacktestEngine(policy()).evaluate(trades, NOW, run_spec())
        self.assertIs(report.verdict, BacktestVerdict.FAILED)
        self.assertIn("observed strict win rate", " ".join(report.reasons))
        self.assertLess(report.statistics.net_r, 0)

    def test_small_sample_is_inconclusive_not_failed_or_passed(self) -> None:
        report = BacktestEngine(policy()).evaluate(
            tuple(trade(index) for index in range(8)), NOW, run_spec()
        )
        self.assertIs(report.verdict, BacktestVerdict.INCONCLUSIVE)
        self.assertIn("walk-forward folds", " ".join(report.reasons))

    def test_missing_bearish_regime_coverage_is_inconclusive(self) -> None:
        trades = tuple(trade(index, regime=MarketRegime.BULLISH_TREND) for index in range(13))
        report = BacktestEngine(policy()).evaluate(trades, NOW, run_spec())
        self.assertIs(report.verdict, BacktestVerdict.INCONCLUSIVE)
        self.assertIn("BEARISH_TREND", " ".join(report.reasons))

    def test_no_fill_and_unresolved_are_visible_but_not_wins_or_losses(self) -> None:
        trades = (
            *(trade(index) for index in range(13)),
            trade(13, BacktestOutcome.NO_FILL),
            trade(14, BacktestOutcome.UNRESOLVED),
        )
        report = BacktestEngine(policy()).evaluate(trades, NOW, run_spec())
        self.assertEqual(report.no_fill_count, 1)
        self.assertEqual(report.unresolved_count, 1)
        self.assertLessEqual(report.statistics.resolved, report.candidate_count - 2)

    def test_overlap_duplicate_identity_and_future_result_are_rejected(self) -> None:
        first = trade(0)
        overlap = replace(trade(1), created_at=first.created_at + timedelta(minutes=30))
        with self.assertRaisesRegex(DomainValidationError, "overlap"):
            BacktestEngine(policy(), OutcomeVariant.MANAGED).evaluate(
                tuple(replace(item, variant=OutcomeVariant.MANAGED) for item in (first, overlap)),
                NOW,
                run_spec(),
            )
        with self.assertRaisesRegex(DomainValidationError, "unique"):
            BacktestEngine(policy()).evaluate(
                (first, replace(trade(1), signal_id=first.signal_id)), NOW, run_spec()
            )
        with self.assertRaisesRegex(DomainValidationError, "after generation"):
            BacktestEngine(policy()).evaluate(
                (replace(first, terminal_at=NOW + timedelta(hours=1)),), NOW, run_spec()
            )

    def test_threshold_selection_is_reproducible(self) -> None:
        engine = BacktestEngine(policy())
        trades = tuple(trade(index) for index in range(13))
        self.assertEqual(
            engine.evaluate(trades, NOW, run_spec()),
            engine.evaluate(trades, NOW, run_spec()),
        )

    def test_sensitivity_can_use_independent_threshold_replay_paths(self) -> None:
        selected_policy = policy()
        primary = tuple(trade(index, score=80) for index in range(13))
        independent = {
            75: tuple(trade(index, BacktestOutcome.LOSS, score=75) for index in range(13)),
            80: primary,
            85: tuple(trade(index, score=90) for index in range(13)),
        }

        report = BacktestEngine(selected_policy).evaluate(
            primary,
            NOW,
            run_spec(),
            independent,
        )

        sensitivity = {item.threshold: item for item in report.sensitivity}
        self.assertLess(sensitivity[75].average_r, 0)
        self.assertGreater(sensitivity[80].average_r, 0)
        self.assertGreater(sensitivity[85].average_r, 0)
        self.assertIn("score-threshold sensitivity is negative", " ".join(report.reasons))

        with self.assertRaisesRegex(DomainValidationError, "every declared"):
            BacktestEngine(selected_policy).evaluate(
                primary,
                NOW,
                run_spec(),
                {80: primary},
            )

    def test_run_spec_rejects_declared_cherry_picking_and_missing_price_coverage(self) -> None:
        spec = run_spec()
        with self.assertRaisesRegex(DomainValidationError, "candidate universe"):
            replace(spec, exhaustive_candidate_scan=False)
        with self.assertRaisesRegex(DomainValidationError, "SPOT_1m"):
            replace(spec, source_coverage=("SPOT_15m_to_1M",))

    def test_mixed_strategy_versions_and_inconsistent_r_are_rejected(self) -> None:
        first = trade(0)
        with self.assertRaisesRegex(DomainValidationError, "strategy versions"):
            BacktestEngine(policy()).evaluate(
                (replace(first, strategy_version="rules-v9"),), NOW, run_spec()
            )
        with self.assertRaisesRegex(DomainValidationError, "does not match"):
            replace(first, result_r=Decimal("9"))

    def test_fixed_and_managed_runs_cannot_mix_variants(self) -> None:
        managed = replace(trade(0), variant=OutcomeVariant.MANAGED)
        with self.assertRaisesRegex(DomainValidationError, "selected track variant"):
            BacktestEngine(policy()).evaluate((managed,), NOW, run_spec())
        report = BacktestEngine(policy(), OutcomeVariant.MANAGED).evaluate(
            tuple(replace(trade(index), variant=OutcomeVariant.MANAGED) for index in range(13)),
            NOW,
            run_spec(),
        )
        self.assertIs(report.variant, OutcomeVariant.MANAGED)

    def test_variant_comparison_keeps_unresolved_pairs_visible(self) -> None:
        winning_candles = (
            minute_candle(
                0,
                open_price=Decimal("102"),
                high=Decimal("103"),
                low=Decimal("100"),
                close=Decimal("102"),
            ),
            minute_candle(
                1,
                open_price=Decimal("110"),
                high=Decimal("115"),
                low=Decimal("109"),
                close=Decimal("114"),
            ),
        )
        first = HistoricalSignalCase(
            long_signal(), minute_series(*winning_candles), after(*winning_candles)
        )

        delta = timedelta(days=1)
        original = long_signal("BTC-20260803-001")
        shifted_signal = replace(
            original,
            terms=replace(
                original.terms,
                created_at=original.terms.created_at + delta,
                data_timestamp=original.terms.data_timestamp + delta,
                expires_at=original.terms.expires_at + delta,
            ),
        )
        management_candles = tuple(
            replace(
                candle,
                open_time=candle.open_time + delta,
                close_time=candle.close_time + delta,
            )
            for candle in (
                minute_candle(
                    0,
                    open_price=Decimal("102"),
                    high=Decimal("103"),
                    low=Decimal("100"),
                    close=Decimal("102"),
                ),
                minute_candle(
                    1,
                    open_price=Decimal("106"),
                    high=Decimal("112"),
                    low=Decimal("105"),
                    close=Decimal("111"),
                ),
                minute_candle(
                    2,
                    open_price=Decimal("108"),
                    high=Decimal("109"),
                    low=Decimal("101"),
                    close=Decimal("102"),
                ),
            )
        )
        second = HistoricalSignalCase(
            shifted_signal,
            minute_series(*management_candles),
            after(*management_candles),
        )
        comparison_policy = policy(
            train_size=1,
            test_size=1,
            purge_size=0,
            minimum_train_trades=1,
            minimum_out_of_sample_trades=1,
            minimum_folds=1,
            minimum_regime_trades=1,
            score_thresholds=(80,),
        )
        comparison = BacktestEngine(comparison_policy).compare_variants(
            (first, second),
            NOW,
            replace(run_spec(), strategy_version=first.signal.strategy_version),
        )
        self.assertEqual(comparison.completed_pairs, 0)
        self.assertEqual(comparison.unresolved_fixed, 1)
        self.assertEqual(comparison.unresolved_managed, 0)

        with self.assertRaisesRegex(DomainValidationError, "contradictory"):
            BacktestComparisonReport(
                fixed=comparison.fixed,
                managed=comparison.managed,
                completed_pairs=1,
                managed_better=0,
                fixed_better=0,
                ties=0,
                unresolved_fixed=0,
                unresolved_managed=0,
                average_managed_delta_r=Decimal("0"),
            )

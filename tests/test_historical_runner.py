from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import TestCase

from btc_sentinel.backtesting import (
    BacktestOutcome,
    HistoricalMarketView,
    HistoricalReplayRunner,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle
from btc_sentinel.news.models import RiskAssessment, RiskDecision
from tests.analysis_fixtures import analysis_snapshot

START = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
END = START + timedelta(hours=5)


@dataclass(frozen=True)
class ClearRiskProvider:
    source_coverage: tuple[str, ...] = ("TEST_POINT_IN_TIME_NEWS",)
    excluded_features: tuple[str, ...] = ()
    performance_eligible: bool = True
    offset: timedelta = timedelta(0)

    def assessment_at(self, as_of: datetime) -> RiskAssessment:
        return RiskAssessment(
            evaluated_at=as_of + self.offset,
            decision=RiskDecision.CLEAR,
            block_until=None,
            events=(),
            scheduled_events=(),
            reasons=(),
            coverage_issues=(),
        )


class FakeReplayStore:
    def __init__(self, candidates=(START,)):
        self.candidates = candidates

    def coverage(self):
        return START, END

    def metadata(self, key):
        if key == "dataset_id":
            return "synthetic-historical-run-v1"
        raise AssertionError(f"Unexpected metadata key: {key}")

    def candidate_times(self, start, end):
        return self.candidates

    def market_view(self, as_of):
        return HistoricalMarketView(as_of, analysis_snapshot().spot_series)

    def iter_candles(self, interval, start, end):
        self.iterated = True
        current = start
        while current < end:
            yield Candle(
                venue=MarketVenue.SPOT,
                interval=MarketInterval.ONE_MINUTE,
                open_time=current,
                close_time=MarketInterval.ONE_MINUTE.expected_close_time(current),
                open=Decimal("2000"),
                high=Decimal("2001"),
                low=Decimal("1999"),
                close=Decimal("2000"),
                volume=Decimal("10"),
                quote_volume=Decimal("20000"),
                trade_count=10,
                taker_buy_base_volume=Decimal("5"),
                taker_buy_quote_volume=Decimal("10000"),
            )
            current += timedelta(minutes=1)


class GappedReplayStore(FakeReplayStore):
    def iter_candles(self, interval, start, end):
        for index, candle in enumerate(super().iter_candles(interval, start, end)):
            if index == 5:
                raise DomainValidationError("Historical streaming range contains a candle gap")
            yield candle


class HistoricalRunnerTests(TestCase):
    def test_exhaustive_candidate_creates_both_tracks_and_expires_without_fill(self) -> None:
        store = FakeReplayStore()
        run = HistoricalReplayRunner().run(store, START, END, ClearRiskProvider())

        self.assertEqual(run.candidate_count, 1)
        self.assertEqual(run.created_signal_count, 1)
        self.assertTrue(run.performance_eligible)
        self.assertEqual(run.fixed_trades[0].outcome, BacktestOutcome.NO_FILL)
        self.assertEqual(run.managed_trades[0].outcome, BacktestOutcome.NO_FILL)
        self.assertEqual(run.fixed_trades[0].terminal_at, START + timedelta(hours=4))
        report = run.evaluate(END)
        self.assertEqual(report.fixed.run_spec.dataset_id, "synthetic-historical-run-v1")
        self.assertIn("TEST_POINT_IN_TIME_NEWS", report.fixed.run_spec.source_coverage)

    def test_missing_historical_risk_blocks_instead_of_inventing_clear_state(self) -> None:
        store = FakeReplayStore()
        store.iterated = False
        run = HistoricalReplayRunner().run(store, START, END)

        self.assertEqual(run.candidate_count, 1)
        self.assertEqual(run.created_signal_count, 0)
        self.assertFalse(run.performance_eligible)
        self.assertFalse(store.iterated)
        self.assertIn("required historical news", " ".join(dict(run.rejection_counts)))
        with self.assertRaisesRegex(DomainValidationError, "eligible risk coverage"):
            run.evaluate(END)

    def test_future_archive_gap_marks_open_tracks_unresolved(self) -> None:
        run = HistoricalReplayRunner().run(
            GappedReplayStore(),
            START,
            END,
            ClearRiskProvider(),
        )

        self.assertEqual(run.created_signal_count, 1)
        self.assertEqual(run.fixed_trades[0].outcome, BacktestOutcome.UNRESOLVED)
        self.assertEqual(run.managed_trades[0].outcome, BacktestOutcome.UNRESOLVED)
        self.assertEqual(run.fixed_trades[0].terminal_at, START + timedelta(minutes=5))

    def test_future_or_misaligned_risk_assessment_is_rejected(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "match candidate time"):
            HistoricalReplayRunner().run(
                FakeReplayStore(),
                START,
                END,
                ClearRiskProvider(offset=timedelta(minutes=1)),
            )

    def test_active_managed_trade_blocks_candidates_until_its_terminal_time(self) -> None:
        candidates = (START, START + timedelta(minutes=15), START + timedelta(hours=4))
        run = HistoricalReplayRunner().run(
            FakeReplayStore(candidates),
            START,
            END,
            ClearRiskProvider(),
        )
        self.assertEqual(run.candidate_count, 3)
        self.assertEqual(run.created_signal_count, 2)
        self.assertEqual(
            dict(run.rejection_counts)["a managed BTC signal is already active"],
            1,
        )

    def test_run_must_stay_inside_immutable_store_coverage(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "outside"):
            HistoricalReplayRunner().run(
                FakeReplayStore(),
                START - timedelta(minutes=15),
                END,
                ClearRiskProvider(),
            )

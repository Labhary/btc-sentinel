from dataclasses import replace
from decimal import Decimal
from unittest import TestCase

from btc_sentinel.analysis import MultiTimeframeAnalyzer
from btc_sentinel.analysis.models import AnalysisStatus, Direction, MarketRegime
from btc_sentinel.market_data.enums import MarketInterval
from tests.analysis_fixtures import analysis_snapshot


class AnalysisEngineTests(TestCase):
    def test_aligned_bullish_market_has_bullish_bias(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        self.assertIs(result.directional_bias, Direction.BULLISH)
        self.assertGreaterEqual(result.setup_quality_score, 80)

    def test_aligned_bearish_market_has_bearish_bias(self) -> None:
        slopes = {interval: Decimal("-0.8") for interval in MarketInterval}
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot(slopes))
        self.assertIs(result.directional_bias, Direction.BEARISH)

    def test_major_timeframe_conflict_fails_closed(self) -> None:
        slopes = {MarketInterval.ONE_WEEK: Decimal("-0.8")}
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot(slopes))
        self.assertIn("major timeframes conflict", result.no_trade_reasons)
        self.assertLessEqual(result.setup_quality_score, 59)

    def test_missing_optional_derivatives_degrades_analysis(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot(derivatives=False))
        self.assertIs(result.status, AnalysisStatus.DEGRADED)
        self.assertIn("optional derivatives confirmation unavailable", result.issues)

    def test_missing_required_timeframe_rejects_analysis(self) -> None:
        snapshot = analysis_snapshot()
        snapshot = replace(snapshot, spot_series=snapshot.spot_series[:-1])
        result = MultiTimeframeAnalyzer().analyze(snapshot)
        self.assertIs(result.status, AnalysisStatus.REJECTED)
        self.assertEqual(result.setup_quality_score, 0)

    def test_incomplete_candle_rejects_analysis(self) -> None:
        snapshot = analysis_snapshot()
        result = MultiTimeframeAnalyzer().analyze(
            replace(snapshot, captured_at=snapshot.spot_series[-1].latest.close_time)
        )
        self.assertIs(result.status, AnalysisStatus.REJECTED)
        self.assertIn("incomplete candle", result.issues[0])

    def test_abnormal_volatility_blocks_trade_context(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(
            analysis_snapshot(shock_interval=MarketInterval.FOUR_HOURS)
        )
        self.assertIs(result.regime, MarketRegime.ABNORMALLY_VOLATILE)
        self.assertIn("abnormally volatile regime", result.no_trade_reasons)

    def test_score_is_explicitly_not_probability(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        self.assertFalse(result.score_is_probability)

    def test_analysis_uses_required_hierarchy_order(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        self.assertEqual(
            tuple(item.interval for item in result.timeframes),
            (
                MarketInterval.ONE_MONTH,
                MarketInterval.ONE_WEEK,
                MarketInterval.ONE_DAY,
                MarketInterval.FOUR_HOURS,
                MarketInterval.ONE_HOUR,
                MarketInterval.FIFTEEN_MINUTES,
            ),
        )

    def test_evidence_weights_total_one_hundred(self) -> None:
        result = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        self.assertEqual(sum(group.weight for group in result.evidence_groups), 100)

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import TestCase

from btc_sentinel.analysis import MultiTimeframeAnalyzer
from btc_sentinel.analysis.models import Direction, PriceZone
from btc_sentinel.analysis.models import MarketRegime as AnalysisRegime
from btc_sentinel.domain.enums import Side, SignalStatus
from btc_sentinel.news.models import RiskDecision
from btc_sentinel.signals import SignalDecision, SignalEngine, SignalHistory
from btc_sentinel.signals.engine import SignalPolicy
from tests.analysis_fixtures import ANALYSIS_NOW, analysis_snapshot
from tests.signal_fixtures import news_risk


class StaticAnalyzer:
    def __init__(self, result):
        self.result = result

    def analyze(self, snapshot):
        return self.result


class SignalEngineTests(TestCase):
    def evaluate(self, **kwargs):
        return SignalEngine().evaluate(
            "BTC-20260802-001",
            kwargs.pop("snapshot", analysis_snapshot()),
            kwargs.pop("news", news_risk()),
            kwargs.pop("as_of", ANALYSIS_NOW),
            kwargs.pop("history", SignalHistory()),
        )

    def test_aligned_bullish_context_creates_pending_signal(self) -> None:
        result = self.evaluate()
        self.assertIs(result.decision, SignalDecision.CREATED)
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertIs(result.signal.status, SignalStatus.PENDING)
        self.assertIs(result.signal.terms.side, Side.LONG)
        self.assertEqual(result.signal.strategy_version, "rules-v0.6.0")

    def test_targets_exceed_minimum_net_rr_after_costs(self) -> None:
        signal = self.evaluate().signal
        assert signal is not None
        self.assertGreaterEqual(
            signal.terms.planned_r_for(signal.terms.targets[0]), Decimal("2.25")
        )
        self.assertGreaterEqual(
            signal.terms.planned_r_for(signal.terms.targets[1]), Decimal("3.25")
        )
        self.assertEqual(signal.terms.estimated_round_trip_cost_rate, Decimal("0.0015"))

    def test_entry_stop_and_expiry_are_deterministic(self) -> None:
        first = self.evaluate().signal
        second = self.evaluate().signal
        assert first is not None and second is not None
        self.assertEqual(first.terms, second.terms)
        self.assertLess(first.terms.original_stop, first.terms.entry_low)
        self.assertEqual(first.terms.expires_at, ANALYSIS_NOW + timedelta(hours=4))

    def test_aligned_bearish_context_creates_short(self) -> None:
        from btc_sentinel.market_data.enums import MarketInterval

        slopes = {interval: Decimal("-0.8") for interval in MarketInterval}
        snapshot = analysis_snapshot(slopes)
        analysis = MultiTimeframeAnalyzer().analyze(snapshot)
        timeframes = list(analysis.timeframes)
        operational = timeframes[-2]
        timeframes[-2] = replace(
            operational,
            structure=replace(operational.structure, support_zones=()),
        )
        engine = SignalEngine(
            analyzer=StaticAnalyzer(
                replace(
                    analysis,
                    regime=AnalysisRegime.BEARISH_TREND,
                    timeframes=tuple(timeframes),
                )
            )
        )
        result = engine.evaluate("BTC-20260802-001", snapshot, news_risk(), ANALYSIS_NOW)
        self.assertIs(result.decision, SignalDecision.CREATED)
        assert result.signal is not None
        self.assertIs(result.signal.terms.side, Side.SHORT)
        self.assertGreater(result.signal.terms.original_stop, result.signal.terms.entry_high)

    def test_blocking_news_rejects_signal(self) -> None:
        result = self.evaluate(
            news=news_risk(RiskDecision.BLOCK, reasons=("scheduled high event",))
        )
        self.assertIs(result.decision, SignalDecision.NO_SIGNAL)
        self.assertIn("news-risk gate blocks", " ".join(result.rejection_reasons))

    def test_caution_reduces_risk_without_creating_direction(self) -> None:
        result = self.evaluate(
            news=news_risk(RiskDecision.CAUTION, reasons=("optional coverage degraded",))
        )
        assert result.signal is not None
        self.assertEqual(result.signal.terms.recommended_risk_percent, Decimal("0.25"))
        self.assertIn("suggested risk is reduced", " ".join(result.signal.risks))

    def test_clear_context_uses_standard_conservative_risk(self) -> None:
        signal = self.evaluate().signal
        assert signal is not None
        self.assertEqual(signal.terms.recommended_risk_percent, Decimal("0.50"))

    def test_degraded_analysis_reduces_risk(self) -> None:
        signal = self.evaluate(snapshot=analysis_snapshot(derivatives=False)).signal
        assert signal is not None
        self.assertEqual(signal.terms.recommended_risk_percent, Decimal("0.25"))
        self.assertIn("market context is degraded", " ".join(signal.risks))

    def test_active_managed_signal_blocks_new_signal(self) -> None:
        result = self.evaluate(history=SignalHistory(active_managed_signal=True))
        self.assertIn("already active", " ".join(result.rejection_reasons))

    def test_four_hour_cooldown_blocks_duplicate_churn(self) -> None:
        result = self.evaluate(
            history=SignalHistory(last_signal_at=ANALYSIS_NOW - timedelta(hours=3))
        )
        self.assertIn("cooldown", " ".join(result.rejection_reasons))

    def test_cooldown_boundary_allows_evaluation(self) -> None:
        result = self.evaluate(
            history=SignalHistory(last_signal_at=ANALYSIS_NOW - timedelta(hours=4))
        )
        self.assertIs(result.decision, SignalDecision.CREATED)

    def test_stale_market_snapshot_fails_closed(self) -> None:
        result = self.evaluate(as_of=ANALYSIS_NOW + timedelta(minutes=6))
        self.assertIn("snapshot is stale", " ".join(result.rejection_reasons))

    def test_stale_news_assessment_fails_closed(self) -> None:
        old_news = replace(news_risk(), evaluated_at=ANALYSIS_NOW - timedelta(minutes=16))
        result = self.evaluate(news=old_news)
        self.assertIn("news-risk assessment is stale", " ".join(result.rejection_reasons))

    def test_future_signal_history_fails_closed(self) -> None:
        result = self.evaluate(
            history=SignalHistory(last_signal_at=ANALYSIS_NOW + timedelta(minutes=1))
        )
        self.assertIn("history is future-dated", " ".join(result.rejection_reasons))

    def test_major_timeframe_conflict_rejects_signal(self) -> None:
        from btc_sentinel.market_data.enums import MarketInterval

        result = self.evaluate(
            snapshot=analysis_snapshot({MarketInterval.ONE_WEEK: Decimal("-0.8")})
        )
        self.assertIs(result.decision, SignalDecision.NO_SIGNAL)
        self.assertIn("major timeframes conflict", result.rejection_reasons)

    def test_abnormal_volatility_rejects_signal(self) -> None:
        from btc_sentinel.market_data.enums import MarketInterval

        result = self.evaluate(snapshot=analysis_snapshot(shock_interval=MarketInterval.FOUR_HOURS))
        self.assertIn("abnormally volatile regime", result.rejection_reasons)

    def test_below_threshold_analysis_rejects_signal(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        engine = SignalEngine(analyzer=StaticAnalyzer(replace(analysis, setup_quality_score=79)))
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("below selective threshold", " ".join(result.rejection_reasons))

    def test_execution_direction_must_align(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        timeframes = list(analysis.timeframes)
        timeframes[-1] = replace(timeframes[-1], direction=Direction.NEUTRAL)
        engine = SignalEngine(
            analyzer=StaticAnalyzer(replace(analysis, timeframes=tuple(timeframes)))
        )
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("15m execution direction is not aligned", result.rejection_reasons)

    def test_incomplete_analysis_hierarchy_fails_closed(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        engine = SignalEngine(
            analyzer=StaticAnalyzer(replace(analysis, timeframes=analysis.timeframes[1:]))
        )
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("hierarchy is incomplete", " ".join(result.rejection_reasons))

    def test_missing_structure_zone_rejects_signal(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        timeframes = list(analysis.timeframes)
        execution = timeframes[-1]
        timeframes[-1] = replace(
            execution,
            structure=replace(execution.structure, support_zones=()),
        )
        engine = SignalEngine(
            analyzer=StaticAnalyzer(replace(analysis, timeframes=tuple(timeframes)))
        )
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("no usable", " ".join(result.rejection_reasons))

    def test_distant_structure_zone_rejects_chasing(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        timeframes = list(analysis.timeframes)
        execution = timeframes[-1]
        timeframes[-1] = replace(
            execution,
            structure=replace(
                execution.structure,
                support_zones=(PriceZone(Decimal("1000"), Decimal("1001"), 2),),
            ),
        )
        engine = SignalEngine(
            analyzer=StaticAnalyzer(replace(analysis, timeframes=tuple(timeframes)))
        )
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("more than 2.5 ATR", " ".join(result.rejection_reasons))

    def test_operational_obstacle_rejects_nominal_rr(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        timeframes = list(analysis.timeframes)
        operational = timeframes[-2]
        timeframes[-2] = replace(
            operational,
            structure=replace(
                operational.structure,
                resistance_zones=(PriceZone(Decimal("1195"), Decimal("1197"), 2),),
            ),
        )
        engine = SignalEngine(
            analyzer=StaticAnalyzer(replace(analysis, timeframes=tuple(timeframes)))
        )
        result = engine.evaluate("BTC-20260802-001", analysis_snapshot(), news_risk(), ANALYSIS_NOW)
        self.assertIn("resistance obstructs", " ".join(result.rejection_reasons))

    def test_policy_rejects_target_below_two_r(self) -> None:
        with self.assertRaises(ValueError):
            SignalPolicy(first_target_r=Decimal("1.99"))

    def test_invalid_signal_id_fails_closed(self) -> None:
        result = SignalEngine().evaluate(
            "not-a-signal-id", analysis_snapshot(), news_risk(), ANALYSIS_NOW
        )
        self.assertIs(result.decision, SignalDecision.NO_SIGNAL)
        self.assertIn("identity is invalid", " ".join(result.rejection_reasons))

    def test_news_headline_direction_does_not_change_signal_side(self) -> None:
        clear = self.evaluate(news=news_risk(RiskDecision.CLEAR)).signal
        caution = self.evaluate(
            news=news_risk(RiskDecision.CAUTION, reasons=("negative headline",))
        ).signal
        assert clear is not None and caution is not None
        self.assertIs(clear.terms.side, caution.terms.side)
        self.assertEqual(clear.terms.entry_low, caution.terms.entry_low)
        self.assertEqual(clear.terms.targets, caution.terms.targets)

"""Phase 6 deterministic signal gate; no persistence, delivery, or trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from btc_sentinel.analysis import MultiTimeframeAnalyzer
from btc_sentinel.analysis.models import (
    AnalysisResult,
    AnalysisStatus,
    Direction,
    PriceZone,
    TimeframeAnalysis,
)
from btc_sentinel.analysis.models import (
    MarketRegime as AnalysisRegime,
)
from btc_sentinel.domain.enums import Bias, MarketRegime, Side
from btc_sentinel.domain.ids import validate_signal_id
from btc_sentinel.domain.models import Signal, SignalTerms, Target, TimeframeBiases
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import MarketSnapshot
from btc_sentinel.news.models import RiskAssessment, RiskDecision
from btc_sentinel.signals.models import SignalDecision, SignalEvaluation, SignalHistory
from btc_sentinel.time_utils import ensure_utc

_PRICE_STEP = Decimal("0.01")
_REQUIRED_INTERVALS = (
    MarketInterval.ONE_MONTH,
    MarketInterval.ONE_WEEK,
    MarketInterval.ONE_DAY,
    MarketInterval.FOUR_HOURS,
    MarketInterval.ONE_HOUR,
    MarketInterval.FIFTEEN_MINUTES,
)


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    minimum_setup_score: int = 80
    cooldown: timedelta = timedelta(hours=4)
    expiry: timedelta = timedelta(hours=4)
    maximum_snapshot_age: timedelta = timedelta(minutes=5)
    maximum_news_age: timedelta = timedelta(minutes=15)
    maximum_clock_skew: timedelta = timedelta(seconds=5)
    maximum_entry_distance_atr: Decimal = Decimal("2.5")
    stop_buffer_atr: Decimal = Decimal("0.5")
    estimated_round_trip_cost_rate: Decimal = Decimal("0.0015")
    first_target_r: Decimal = Decimal("2.25")
    second_target_r: Decimal = Decimal("3.25")
    standard_risk_percent: Decimal = Decimal("0.50")
    cautious_risk_percent: Decimal = Decimal("0.25")

    def __post_init__(self) -> None:
        if not 60 <= self.minimum_setup_score <= 100:
            raise ValueError("Signal threshold must be between 60 and 100")
        if self.cooldown <= timedelta(0) or self.expiry <= timedelta(0):
            raise ValueError("Cooldown and expiry must be positive")
        if self.maximum_snapshot_age <= timedelta(0) or self.maximum_news_age <= timedelta(0):
            raise ValueError("Freshness limits must be positive")
        if self.maximum_clock_skew < timedelta(0):
            raise ValueError("Clock skew cannot be negative")
        if self.maximum_entry_distance_atr <= 0 or self.stop_buffer_atr <= 0:
            raise ValueError("ATR distance and stop buffer must be positive")
        if not Decimal("2") <= self.first_target_r < self.second_target_r:
            raise ValueError("Signal targets must increase from at least 2R")
        if not Decimal("0") <= self.estimated_round_trip_cost_rate <= Decimal("0.01"):
            raise ValueError("Signal cost rate must be between zero and one percent")
        if not Decimal("0.01") <= self.cautious_risk_percent <= self.standard_risk_percent:
            raise ValueError("Risk percentages must be positive and cautious risk cannot be higher")
        if self.standard_risk_percent > Decimal("1"):
            raise ValueError("Standard recommended risk cannot exceed one percent")


class SignalEngine:
    """Recompute analysis and admit only selective, auditable pending setups."""

    strategy_version = "rules-v0.6.0"

    def __init__(
        self,
        policy: SignalPolicy | None = None,
        analyzer: MultiTimeframeAnalyzer | None = None,
    ) -> None:
        self.policy = policy or SignalPolicy()
        self.analyzer = analyzer or MultiTimeframeAnalyzer()

    def evaluate(
        self,
        signal_id: str,
        snapshot: MarketSnapshot,
        news_risk: RiskAssessment,
        as_of: datetime,
        history: SignalHistory | None = None,
    ) -> SignalEvaluation:
        now = ensure_utc(as_of)
        history = history or SignalHistory()
        analysis = self.analyzer.analyze(snapshot)
        reasons = self._admission_rejections(analysis, snapshot, news_risk, now, history)
        try:
            validate_signal_id(signal_id)
        except DomainValidationError as exc:
            reasons.append(f"signal identity is invalid: {exc}")
        if reasons:
            return SignalEvaluation(
                now,
                SignalDecision.NO_SIGNAL,
                analysis,
                news_risk,
                None,
                tuple(dict.fromkeys(reasons)),
            )

        side = Side.LONG if analysis.directional_bias is Direction.BULLISH else Side.SHORT
        execution = self._timeframe(analysis, MarketInterval.FIFTEEN_MINUTES)
        zone = self._entry_zone(execution, side)
        if zone is None:
            return self._rejected(now, analysis, news_risk, "no usable 15-minute structure zone")

        current = snapshot.series_for(MarketVenue.SPOT, MarketInterval.FIFTEEN_MINUTES).latest.close
        atr = execution.indicators.atr_14
        if not self._entry_is_reachable(zone, current, atr, side):
            return self._rejected(
                now,
                analysis,
                news_risk,
                "nearest structure entry is more than 2.5 ATR from price",
            )

        try:
            terms = self._terms(
                side,
                zone,
                atr,
                snapshot.captured_at,
                now,
                reduced_risk=(
                    news_risk.decision is RiskDecision.CAUTION
                    or analysis.status is AnalysisStatus.DEGRADED
                ),
            )
        except DomainValidationError as exc:
            return self._rejected(
                now,
                analysis,
                news_risk,
                f"constructed signal terms are unsafe: {exc}",
            )
        obstruction = self._target_obstruction(analysis, terms)
        if obstruction:
            return self._rejected(now, analysis, news_risk, obstruction)

        signal = Signal(
            signal_id=signal_id,
            terms=terms,
            setup_score=analysis.setup_quality_score,
            regime=MarketRegime(analysis.regime.value),
            biases=self._biases(analysis),
            reasons=self._setup_reasons(analysis, side),
            risks=self._risk_descriptions(analysis, news_risk),
            strategy_version=self.strategy_version,
        )
        return SignalEvaluation(
            now,
            SignalDecision.CREATED,
            analysis,
            news_risk,
            signal,
            (),
        )

    def _admission_rejections(
        self,
        analysis: AnalysisResult,
        snapshot: MarketSnapshot,
        news_risk: RiskAssessment,
        now: datetime,
        history: SignalHistory,
    ) -> list[str]:
        reasons: list[str] = []
        if analysis.status is AnalysisStatus.REJECTED or not analysis.reliable:
            reasons.append("multi-timeframe analysis is not reliable")
            reasons.extend(analysis.no_trade_reasons)
            reasons.extend(analysis.issues)
        if analysis.setup_quality_score < self.policy.minimum_setup_score:
            reasons.append(
                f"setup score below selective threshold {self.policy.minimum_setup_score}"
            )
        intervals = tuple(item.interval for item in analysis.timeframes)
        if intervals != _REQUIRED_INTERVALS:
            reasons.append("analysis timeframe hierarchy is incomplete or unordered")
        expected_regime = (
            AnalysisRegime.BULLISH_TREND
            if analysis.directional_bias is Direction.BULLISH
            else AnalysisRegime.BEARISH_TREND
            if analysis.directional_bias is Direction.BEARISH
            else None
        )
        if expected_regime is None or analysis.regime is not expected_regime:
            reasons.append("overall regime is not an aligned directional trend")
        if analysis.directional_bias is not Direction.NEUTRAL:
            for interval in (MarketInterval.ONE_HOUR, MarketInterval.FIFTEEN_MINUTES):
                timeframe = self._timeframe_or_none(analysis, interval)
                if timeframe is None or timeframe.direction is not analysis.directional_bias:
                    reasons.append(f"{interval.value} execution direction is not aligned")
        if snapshot.captured_at > now + self.policy.maximum_clock_skew:
            reasons.append("market snapshot is future-dated")
        elif now - snapshot.captured_at > self.policy.maximum_snapshot_age:
            reasons.append("market snapshot is stale")
        if news_risk.evaluated_at > now + self.policy.maximum_clock_skew:
            reasons.append("news-risk assessment is future-dated")
        elif now - news_risk.evaluated_at > self.policy.maximum_news_age:
            reasons.append("news-risk assessment is stale")
        if news_risk.decision is RiskDecision.BLOCK:
            reasons.append("news-risk gate blocks new signals")
            reasons.extend(news_risk.reasons)
        if history.active_managed_signal:
            reasons.append("a managed BTC signal is already active")
        if history.last_signal_at is not None:
            if history.last_signal_at > now + self.policy.maximum_clock_skew:
                reasons.append("signal history is future-dated")
            elif now - history.last_signal_at < self.policy.cooldown:
                reasons.append("signal cooldown is still active")
        return reasons

    @staticmethod
    def _timeframe_or_none(
        analysis: AnalysisResult, interval: MarketInterval
    ) -> TimeframeAnalysis | None:
        return next((item for item in analysis.timeframes if item.interval is interval), None)

    def _timeframe(self, analysis: AnalysisResult, interval: MarketInterval) -> TimeframeAnalysis:
        value = self._timeframe_or_none(analysis, interval)
        if value is None:
            raise ValueError(f"Analysis is missing {interval.value}")
        return value

    @staticmethod
    def _entry_zone(execution: TimeframeAnalysis, side: Side) -> PriceZone | None:
        zones = (
            execution.structure.support_zones
            if side is Side.LONG
            else execution.structure.resistance_zones
        )
        return zones[0] if zones else None

    def _entry_is_reachable(
        self, zone: PriceZone, current: Decimal, atr: Decimal, side: Side
    ) -> bool:
        distance = (
            max(Decimal("0"), current - zone.upper)
            if side is Side.LONG
            else max(Decimal("0"), zone.lower - current)
        )
        return atr > 0 and distance <= atr * self.policy.maximum_entry_distance_atr

    def _terms(
        self,
        side: Side,
        zone: PriceZone,
        atr: Decimal,
        data_timestamp: datetime,
        now: datetime,
        *,
        reduced_risk: bool,
    ) -> SignalTerms:
        entry_low = zone.lower.quantize(_PRICE_STEP, rounding=ROUND_DOWN)
        entry_high = zone.upper.quantize(_PRICE_STEP, rounding=ROUND_UP)
        if side is Side.LONG:
            stop = (zone.lower - atr * self.policy.stop_buffer_atr).quantize(
                _PRICE_STEP, rounding=ROUND_DOWN
            )
        else:
            stop = (zone.upper + atr * self.policy.stop_buffer_atr).quantize(
                _PRICE_STEP, rounding=ROUND_UP
            )
        conservative_entry = entry_high if side is Side.LONG else entry_low
        cost = conservative_entry * self.policy.estimated_round_trip_cost_rate
        gross_risk = conservative_entry - stop if side is Side.LONG else stop - conservative_entry
        total_risk = gross_risk + cost
        targets = tuple(
            Target(
                ordinal,
                self._target_price(side, conservative_entry, cost, total_risk, target_r),
            )
            for ordinal, target_r in enumerate(
                (self.policy.first_target_r, self.policy.second_target_r), start=1
            )
        )
        risk_percent = (
            self.policy.cautious_risk_percent if reduced_risk else self.policy.standard_risk_percent
        )
        side_word = "below" if side is Side.LONG else "above"
        return SignalTerms(
            side=side,
            entry_low=entry_low,
            entry_high=entry_high,
            original_stop=stop,
            targets=targets,
            created_at=now,
            data_timestamp=data_timestamp,
            expires_at=now + self.policy.expiry,
            invalidation_condition=(
                f"A completed 15m candle closes {side_word} the structure stop at {stop}."
            ),
            expiration_condition="The entry zone is not touched within four hours.",
            recommended_risk_percent=risk_percent,
            estimated_round_trip_cost_rate=self.policy.estimated_round_trip_cost_rate,
            minimum_planned_rr=Decimal("2"),
        )

    @staticmethod
    def _target_price(
        side: Side,
        entry: Decimal,
        cost: Decimal,
        total_risk: Decimal,
        target_r: Decimal,
    ) -> Decimal:
        gross_reward = target_r * total_risk + cost
        if side is Side.LONG:
            return (entry + gross_reward).quantize(_PRICE_STEP, rounding=ROUND_UP)
        return (entry - gross_reward).quantize(_PRICE_STEP, rounding=ROUND_DOWN)

    def _target_obstruction(self, analysis: AnalysisResult, terms: SignalTerms) -> str | None:
        operational = self._timeframe(analysis, MarketInterval.ONE_HOUR)
        tp1 = terms.targets[0].price
        if terms.side is Side.LONG:
            obstacles = [
                zone.lower
                for zone in operational.structure.resistance_zones
                if zone.lower > terms.conservative_entry
            ]
            if obstacles and min(obstacles) < tp1:
                return "one-hour resistance obstructs the minimum-net-2R target"
        else:
            obstacles = [
                zone.upper
                for zone in operational.structure.support_zones
                if zone.upper < terms.conservative_entry
            ]
            if obstacles and max(obstacles) > tp1:
                return "one-hour support obstructs the minimum-net-2R target"
        return None

    @staticmethod
    def _bias(direction: Direction) -> Bias:
        return Bias(direction.value)

    def _biases(self, analysis: AnalysisResult) -> TimeframeBiases:
        values = {item.interval: self._bias(item.direction) for item in analysis.timeframes}
        return TimeframeBiases(
            monthly=values[MarketInterval.ONE_MONTH],
            weekly=values[MarketInterval.ONE_WEEK],
            daily=values[MarketInterval.ONE_DAY],
            four_hour=values[MarketInterval.FOUR_HOURS],
            one_hour=values[MarketInterval.ONE_HOUR],
            fifteen_minute=values[MarketInterval.FIFTEEN_MINUTES],
        )

    @staticmethod
    def _setup_reasons(analysis: AnalysisResult, side: Side) -> tuple[str, ...]:
        return (
            f"Higher-timeframe bias and regime support {side.value}.",
            "One-hour and 15-minute execution directions are aligned.",
            f"Independent evidence-group agreement scored {analysis.setup_quality_score}/100.",
            "Entry and stop are derived from completed-candle 15-minute structure and ATR.",
        )

    @staticmethod
    def _risk_descriptions(analysis: AnalysisResult, news_risk: RiskAssessment) -> tuple[str, ...]:
        risks = list(analysis.issues)
        if analysis.status is AnalysisStatus.DEGRADED:
            risks.append("Optional market context is degraded; suggested risk is reduced.")
        if news_risk.decision is RiskDecision.CAUTION:
            risks.append("News coverage or context is degraded; suggested risk is reduced.")
            risks.extend(news_risk.reasons)
        return tuple(dict.fromkeys(risks))

    @staticmethod
    def _rejected(
        now: datetime,
        analysis: AnalysisResult,
        news_risk: RiskAssessment,
        reason: str,
    ) -> SignalEvaluation:
        return SignalEvaluation(
            now,
            SignalDecision.NO_SIGNAL,
            analysis,
            news_risk,
            None,
            (reason,),
        )

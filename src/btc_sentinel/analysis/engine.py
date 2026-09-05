"""Phase 4 hierarchical analysis without signal generation."""

from __future__ import annotations

from decimal import Decimal

from btc_sentinel.analysis.indicators import calculate_indicators
from btc_sentinel.analysis.models import (
    AnalysisResult,
    AnalysisStatus,
    Direction,
    EvidenceGroup,
    IndicatorSnapshot,
    MarketRegime,
    TimeframeAnalysis,
)
from btc_sentinel.analysis.structure import analyze_structure
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import MarketSnapshot

ANALYSIS_INTERVALS = (
    MarketInterval.ONE_MONTH,
    MarketInterval.ONE_WEEK,
    MarketInterval.ONE_DAY,
    MarketInterval.FOUR_HOURS,
    MarketInterval.ONE_HOUR,
    MarketInterval.FIFTEEN_MINUTES,
)


def _indicator_direction(indicators: IndicatorSnapshot, close: Decimal) -> Direction:
    long_ema = indicators.ema_200 or indicators.ema_100 or indicators.ema_50
    bullish = (
        close > indicators.ema_20 > indicators.ema_50
        and close > long_ema
        and indicators.macd_histogram > 0
        and indicators.rsi_14 >= Decimal("50")
    )
    bearish = (
        close < indicators.ema_20 < indicators.ema_50
        and close < long_ema
        and indicators.macd_histogram < 0
        and indicators.rsi_14 <= Decimal("50")
    )
    if bullish:
        return Direction.BULLISH
    if bearish:
        return Direction.BEARISH
    return Direction.NEUTRAL


def _regime(
    indicators: IndicatorSnapshot,
    indicator_direction: Direction,
    structure_direction: Direction,
) -> MarketRegime:
    if indicators.abnormal_volatility:
        return MarketRegime.ABNORMALLY_VOLATILE
    aligned = (
        indicator_direction is not Direction.NEUTRAL
        and indicator_direction is structure_direction
        and indicators.adx_14 >= Decimal("23")
    )
    if aligned and indicator_direction is Direction.BULLISH:
        return MarketRegime.BULLISH_TREND
    if aligned and indicator_direction is Direction.BEARISH:
        return MarketRegime.BEARISH_TREND
    band_width = (
        indicators.bollinger_upper - indicators.bollinger_lower
    ) / indicators.bollinger_middle
    if indicators.adx_14 < Decimal("18") and band_width < Decimal("0.12"):
        return MarketRegime.RANGE
    if indicator_direction is not structure_direction and (
        indicator_direction is not Direction.NEUTRAL or structure_direction is not Direction.NEUTRAL
    ):
        return MarketRegime.TRANSITION
    return MarketRegime.NO_RELIABLE_REGIME


def _timeframe_direction(
    indicator_direction: Direction,
    structure_direction: Direction,
) -> Direction:
    if indicator_direction is structure_direction:
        return indicator_direction
    if indicator_direction is Direction.NEUTRAL:
        return structure_direction
    if structure_direction is Direction.NEUTRAL:
        return indicator_direction
    return Direction.NEUTRAL


def _group(
    name: str,
    analyses: list[TimeframeAnalysis],
    weight: int,
) -> EvidenceGroup:
    bullish = sum(analysis.direction is Direction.BULLISH for analysis in analyses)
    bearish = sum(analysis.direction is Direction.BEARISH for analysis in analyses)
    total = len(analyses)
    if bullish > bearish:
        direction = Direction.BULLISH
        agreement = Decimal(bullish) / Decimal(total)
    elif bearish > bullish:
        direction = Direction.BEARISH
        agreement = Decimal(bearish) / Decimal(total)
    else:
        direction = Direction.NEUTRAL
        agreement = Decimal("0")
    return EvidenceGroup(
        name=name,
        direction=direction,
        agreement=agreement,
        weight=weight,
        available=True,
        reasons=tuple(
            f"{analysis.interval.value}:{analysis.direction.value}/{analysis.regime.value}"
            for analysis in analyses
        ),
    )


def _derivatives_group(snapshot: MarketSnapshot) -> EvidenceGroup:
    votes: list[Direction] = []
    reasons: list[str] = []
    if snapshot.taker_volume:
        ratio = snapshot.taker_volume[-1].buy_sell_ratio
        direction = (
            Direction.BULLISH
            if ratio > Decimal("1.05")
            else Direction.BEARISH
            if ratio < Decimal("0.95")
            else Direction.NEUTRAL
        )
        votes.append(direction)
        reasons.append(f"taker_ratio:{ratio}")
    if snapshot.order_book is not None:
        imbalance = snapshot.order_book.quantity_imbalance
        direction = (
            Direction.BULLISH
            if imbalance > Decimal("0.10")
            else Direction.BEARISH
            if imbalance < Decimal("-0.10")
            else Direction.NEUTRAL
        )
        votes.append(direction)
        reasons.append(f"book_imbalance:{imbalance}")
    if len(snapshot.open_interest_history) >= 2:
        start = snapshot.open_interest_history[0].open_interest
        end = snapshot.open_interest_history[-1].open_interest
        change = Decimal("0") if start == 0 else (end - start) / start
        reasons.append(f"open_interest_change:{change}")
    if not votes:
        return EvidenceGroup(
            name="derivatives_context",
            direction=Direction.NEUTRAL,
            agreement=Decimal("0"),
            weight=10,
            available=False,
            reasons=("optional derivatives confirmation unavailable",),
        )
    bullish = votes.count(Direction.BULLISH)
    bearish = votes.count(Direction.BEARISH)
    if bullish > bearish:
        direction = Direction.BULLISH
        agreement = Decimal(bullish) / Decimal(len(votes))
    elif bearish > bullish:
        direction = Direction.BEARISH
        agreement = Decimal(bearish) / Decimal(len(votes))
    else:
        direction = Direction.NEUTRAL
        agreement = Decimal("0")
    return EvidenceGroup(
        name="derivatives_context",
        direction=direction,
        agreement=agreement,
        weight=10,
        available=True,
        reasons=tuple(reasons),
    )


def _volatility_group(analyses: list[TimeframeAnalysis]) -> EvidenceGroup:
    abnormal = [
        analysis.interval.value for analysis in analyses if analysis.indicators.abnormal_volatility
    ]
    return EvidenceGroup(
        name="volatility_quality",
        direction=Direction.NEUTRAL,
        agreement=Decimal("0") if abnormal else Decimal("1"),
        weight=5,
        available=True,
        reasons=(f"abnormal:{','.join(abnormal)}",)
        if abnormal
        else ("normal relative volatility",),
    )


class MultiTimeframeAnalyzer:
    """Convert one validated Phase 3 snapshot into transparent Phase 4 context."""

    def analyze(self, snapshot: MarketSnapshot) -> AnalysisResult:
        try:
            analyses = self._analyze_timeframes(snapshot)
        except (MarketDataValidationError, ValueError) as exc:
            return AnalysisResult(
                status=AnalysisStatus.REJECTED,
                directional_bias=Direction.NEUTRAL,
                regime=MarketRegime.NO_RELIABLE_REGIME,
                setup_quality_score=0,
                score_is_probability=False,
                timeframes=(),
                evidence_groups=(),
                no_trade_reasons=("required analysis input rejected",),
                issues=(str(exc),),
            )

        higher = _group("higher_timeframe_bias", analyses[:3], 40)
        structure = _group("operational_structure", analyses[2:4], 25)
        execution = _group("execution_confirmation", analyses[4:6], 20)
        derivatives = _derivatives_group(snapshot)
        volatility = _volatility_group(analyses)
        groups = (higher, structure, execution, derivatives, volatility)
        bias = higher.direction
        no_trade: list[str] = []
        major_directions = {analysis.direction for analysis in analyses[:3]}
        if Direction.BULLISH in major_directions and Direction.BEARISH in major_directions:
            no_trade.append("major timeframes conflict")
        if bias is Direction.NEUTRAL:
            no_trade.append("higher-timeframe bias is not reliable")
        if volatility.agreement == 0:
            no_trade.append("abnormally volatile regime")
        if analyses[0].regime in {MarketRegime.RANGE, MarketRegime.NO_RELIABLE_REGIME} and (
            analyses[1].regime in {MarketRegime.RANGE, MarketRegime.NO_RELIABLE_REGIME}
        ):
            no_trade.append("monthly and weekly regimes are not directional")

        score = Decimal("0")
        for group in groups:
            if group.name == "volatility_quality" or (
                group.available and group.direction is bias and bias is not Direction.NEUTRAL
            ):
                score += Decimal(group.weight) * group.agreement
        if no_trade:
            score = min(score, Decimal("59"))
        status = AnalysisStatus.ACCEPTED if derivatives.available else AnalysisStatus.DEGRADED
        overall_regime = self._overall_regime(analyses, bias, no_trade)
        issues = () if derivatives.available else ("optional derivatives confirmation unavailable",)
        return AnalysisResult(
            status=status,
            directional_bias=bias,
            regime=overall_regime,
            setup_quality_score=int(score.quantize(Decimal("1"))),
            score_is_probability=False,
            timeframes=tuple(analyses),
            evidence_groups=groups,
            no_trade_reasons=tuple(dict.fromkeys(no_trade)),
            issues=issues,
        )

    def _analyze_timeframes(self, snapshot: MarketSnapshot) -> list[TimeframeAnalysis]:
        return [self._analyze_timeframe(snapshot, interval) for interval in ANALYSIS_INTERVALS]

    @staticmethod
    def _analyze_timeframe(snapshot: MarketSnapshot, interval: MarketInterval) -> TimeframeAnalysis:
        series = snapshot.series_for(MarketVenue.SPOT, interval)
        if not series.latest.is_closed_at(snapshot.captured_at):
            raise ValueError(f"{interval.value} analysis received an incomplete candle")
        indicators = calculate_indicators(series)
        structure = analyze_structure(series, indicators.atr_14)
        indicator_direction = _indicator_direction(indicators, series.latest.close)
        direction = _timeframe_direction(indicator_direction, structure.direction)
        regime = _regime(indicators, indicator_direction, structure.direction)
        reasons = (
            f"indicator_direction:{indicator_direction.value}",
            f"structure_direction:{structure.direction.value}",
            f"adx:{indicators.adx_14}",
            f"normalized_atr:{indicators.normalized_atr}",
        )
        return TimeframeAnalysis(interval, direction, regime, indicators, structure, reasons)

    @staticmethod
    def _overall_regime(
        analyses: list[TimeframeAnalysis],
        bias: Direction,
        no_trade: list[str],
    ) -> MarketRegime:
        if any(item.indicators.abnormal_volatility for item in analyses[:4]):
            return MarketRegime.ABNORMALLY_VOLATILE
        if "major timeframes conflict" in no_trade:
            return MarketRegime.TRANSITION
        aligned_trends = sum(
            item.regime
            is (
                MarketRegime.BULLISH_TREND
                if bias is Direction.BULLISH
                else MarketRegime.BEARISH_TREND
            )
            for item in analyses[:4]
        )
        if bias is Direction.BULLISH and aligned_trends >= 2:
            return MarketRegime.BULLISH_TREND
        if bias is Direction.BEARISH and aligned_trends >= 2:
            return MarketRegime.BEARISH_TREND
        if sum(item.regime is MarketRegime.RANGE for item in analyses[:4]) >= 2:
            return MarketRegime.RANGE
        if bias is not Direction.NEUTRAL:
            return MarketRegime.TRANSITION
        return MarketRegime.NO_RELIABLE_REGIME

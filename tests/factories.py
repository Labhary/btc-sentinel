"""Small valid records that individual tests can modify."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_sentinel.domain.enums import Bias, MarketRegime, Side
from btc_sentinel.domain.models import Signal, SignalTerms, Target, TimeframeBiases


def biases() -> TimeframeBiases:
    return TimeframeBiases(
        monthly=Bias.BULLISH,
        weekly=Bias.BULLISH,
        daily=Bias.BULLISH,
        four_hour=Bias.BULLISH,
        one_hour=Bias.BULLISH,
        fifteen_minute=Bias.BULLISH,
    )


def long_signal(signal_id: str = "BTC-20260802-001") -> Signal:
    created = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
    terms = SignalTerms(
        side=Side.LONG,
        entry_low=Decimal("100"),
        entry_high=Decimal("101"),
        original_stop=Decimal("95"),
        targets=(Target(1, Decimal("114")), Target(2, Decimal("120"))),
        created_at=created,
        data_timestamp=created - timedelta(seconds=1),
        expires_at=created + timedelta(hours=4),
        invalidation_condition="A closed 15m candle invalidates the demand zone.",
        expiration_condition="Entry is not touched before four hours.",
        recommended_risk_percent=Decimal("0.50"),
    )
    return Signal(
        signal_id=signal_id,
        terms=terms,
        setup_score=88,
        regime=MarketRegime.BULLISH_TREND,
        biases=biases(),
        reasons=("Higher-timeframe structure is aligned.", "Volume confirms the reclaim."),
        risks=("A high-impact release is scheduled later in the day.",),
        strategy_version="rules-v0.1.0",
    )


def short_signal(signal_id: str = "BTC-20260802-002") -> Signal:
    created = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    terms = SignalTerms(
        side=Side.SHORT,
        entry_low=Decimal("99"),
        entry_high=Decimal("100"),
        original_stop=Decimal("105"),
        targets=(Target(1, Decimal("86")), Target(2, Decimal("80"))),
        created_at=created,
        data_timestamp=created - timedelta(seconds=1),
        expires_at=created + timedelta(hours=4),
        invalidation_condition="A closed 15m candle reclaims supply.",
        expiration_condition="Entry is not touched before four hours.",
        recommended_risk_percent=Decimal("0.50"),
    )
    return Signal(
        signal_id=signal_id,
        terms=terms,
        setup_score=90,
        regime=MarketRegime.BEARISH_TREND,
        biases=TimeframeBiases(
            monthly=Bias.NEUTRAL,
            weekly=Bias.BEARISH,
            daily=Bias.BEARISH,
            four_hour=Bias.BEARISH,
            one_hour=Bias.BEARISH,
            fifteen_minute=Bias.BEARISH,
        ),
        reasons=("Daily structure is bearish.", "The supply retest has volume confirmation."),
        risks=(),
        strategy_version="rules-v0.1.0",
    )

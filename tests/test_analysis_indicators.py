from decimal import Decimal
from unittest import TestCase

from btc_sentinel.analysis.indicators import calculate_indicators, ema_values
from btc_sentinel.market_data.enums import MarketInterval
from tests.analysis_fixtures import analysis_series


class IndicatorTests(TestCase):
    def test_ema_constant_series_remains_constant(self) -> None:
        self.assertEqual(ema_values([Decimal("5")] * 20, 10)[-1], Decimal("5"))

    def test_ema_rejects_short_series(self) -> None:
        with self.assertRaisesRegex(ValueError, "EMA 20"):
            ema_values([Decimal("1")] * 19, 20)

    def test_uptrend_indicators_are_directional(self) -> None:
        result = calculate_indicators(analysis_series(MarketInterval.ONE_DAY))
        assert result.ema_100 is not None
        assert result.ema_200 is not None
        self.assertGreater(result.ema_20, result.ema_50)
        self.assertGreater(result.ema_50, result.ema_100)
        self.assertGreater(result.ema_100, result.ema_200)
        self.assertGreater(result.rsi_14, Decimal("50"))

    def test_downtrend_indicators_are_directional(self) -> None:
        result = calculate_indicators(
            analysis_series(MarketInterval.ONE_DAY, slope=Decimal("-0.8"))
        )
        assert result.ema_100 is not None
        assert result.ema_200 is not None
        self.assertLess(result.ema_20, result.ema_50)
        self.assertLess(result.ema_50, result.ema_100)
        self.assertLess(result.ema_100, result.ema_200)
        self.assertLess(result.rsi_14, Decimal("50"))

    def test_monthly_history_uses_available_long_ema(self) -> None:
        result = calculate_indicators(analysis_series(MarketInterval.ONE_MONTH, count=60))
        self.assertGreater(result.ema_50, 0)
        self.assertIsNone(result.ema_100)
        self.assertIsNone(result.ema_200)

    def test_indicator_engine_requires_fifty_candles(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires 50"):
            calculate_indicators(analysis_series(MarketInterval.ONE_DAY, count=49))

    def test_bollinger_bands_are_ordered(self) -> None:
        result = calculate_indicators(analysis_series(MarketInterval.ONE_HOUR))
        self.assertLess(result.bollinger_lower, result.bollinger_middle)
        self.assertLess(result.bollinger_middle, result.bollinger_upper)

    def test_relative_shock_marks_abnormal_volatility(self) -> None:
        result = calculate_indicators(analysis_series(MarketInterval.ONE_HOUR, shock=True))
        self.assertTrue(result.abnormal_volatility)

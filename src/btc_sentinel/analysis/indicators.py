"""Small dependency-free technical indicators using Decimal arithmetic."""

from __future__ import annotations

from decimal import Decimal, localcontext
from itertools import pairwise

from btc_sentinel.analysis.models import IndicatorSnapshot
from btc_sentinel.market_data.models import CandleSeries

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("Cannot average an empty sequence")
    return sum(values, ZERO) / Decimal(len(values))


def ema_values(values: list[Decimal], period: int) -> list[Decimal]:
    if period < 1 or len(values) < period:
        raise ValueError(f"EMA {period} requires at least {period} values")
    multiplier = Decimal("2") / Decimal(period + 1)
    result = [_mean(values[:period])]
    for value in values[period:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _wilder(values: list[Decimal], period: int) -> list[Decimal]:
    if len(values) < period:
        raise ValueError(f"Wilder average {period} requires at least {period} values")
    result = [_mean(values[:period])]
    for value in values[period:]:
        result.append((result[-1] * Decimal(period - 1) + value) / Decimal(period))
    return result


def _true_ranges(series: CandleSeries) -> list[Decimal]:
    candles = series.candles
    result = [candles[0].high - candles[0].low]
    for previous, candle in pairwise(candles):
        result.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous.close),
                abs(candle.low - previous.close),
            )
        )
    return result


def _rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    changes = [current - previous for previous, current in pairwise(closes)]
    gains = [max(change, ZERO) for change in changes]
    losses = [max(-change, ZERO) for change in changes]
    average_gain = _wilder(gains, period)[-1]
    average_loss = _wilder(losses, period)[-1]
    if average_loss == 0:
        return ONE_HUNDRED if average_gain > 0 else Decimal("50")
    rs = average_gain / average_loss
    return ONE_HUNDRED - ONE_HUNDRED / (Decimal("1") + rs)


def _adx(series: CandleSeries, period: int = 14) -> Decimal:
    candles = series.candles
    true_ranges = _true_ranges(series)[1:]
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    for previous, candle in pairwise(candles):
        up = candle.high - previous.high
        down = previous.low - candle.low
        plus_dm.append(up if up > down and up > 0 else ZERO)
        minus_dm.append(down if down > up and down > 0 else ZERO)
    atrs = _wilder(true_ranges, period)
    plus = _wilder(plus_dm, period)
    minus = _wilder(minus_dm, period)
    dx: list[Decimal] = []
    for atr, positive, negative in zip(atrs, plus, minus, strict=True):
        if atr == 0:
            dx.append(ZERO)
            continue
        plus_di = ONE_HUNDRED * positive / atr
        minus_di = ONE_HUNDRED * negative / atr
        denominator = plus_di + minus_di
        dx.append(ZERO if denominator == 0 else ONE_HUNDRED * abs(plus_di - minus_di) / denominator)
    return _wilder(dx, period)[-1] if len(dx) >= period else _mean(dx)


def _historical_atr_ratios(series: CandleSeries, period: int = 14) -> tuple[Decimal, Decimal]:
    true_ranges = _true_ranges(series)
    atrs = _wilder(true_ranges, period)
    closes = [candle.close for candle in series.candles][period - 1 :]
    ratios = [atr / close for atr, close in zip(atrs, closes, strict=True)]
    recent = ratios[-1]
    ordered = sorted(ratios[-100:])
    median = ordered[len(ordered) // 2]
    return recent, median


def calculate_indicators(series: CandleSeries) -> IndicatorSnapshot:
    if len(series.candles) < 50:
        raise ValueError(f"{series.interval.value} analysis requires 50 completed candles")
    closes = [candle.close for candle in series.candles]
    volumes = [candle.volume for candle in series.candles]
    ema_20 = ema_values(closes, 20)[-1]
    ema_50 = ema_values(closes, 50)[-1]
    ema_100 = ema_values(closes, 100)[-1] if len(closes) >= 100 else None
    ema_200 = ema_values(closes, 200)[-1] if len(closes) >= 200 else None
    fast = ema_values(closes, 12)
    slow = ema_values(closes, 26)
    aligned_fast = fast[len(fast) - len(slow) :]
    macd_values = [left - right for left, right in zip(aligned_fast, slow, strict=True)]
    macd_signal_values = ema_values(macd_values, 9)
    macd = macd_values[-1]
    macd_signal = macd_signal_values[-1]
    true_ranges = _true_ranges(series)
    atr = _wilder(true_ranges, 14)[-1]
    middle = _mean(closes[-20:])
    variance = _mean([(value - middle) ** 2 for value in closes[-20:]])
    with localcontext() as context:
        context.prec = 28
        deviation = variance.sqrt()
    typical_volume = [
        ((candle.high + candle.low + candle.close) / Decimal("3")) * candle.volume
        for candle in series.candles[-20:]
    ]
    volume_sum = sum(volumes[-20:], ZERO)
    vwap = closes[-1] if volume_sum == 0 else sum(typical_volume, ZERO) / volume_sum
    average_volume = _mean(volumes[-20:])
    normalized_atr, median_atr = _historical_atr_ratios(series)
    abnormal = median_atr > 0 and normalized_atr > median_atr * Decimal("2.5")
    return IndicatorSnapshot(
        ema_20=ema_20,
        ema_50=ema_50,
        ema_100=ema_100,
        ema_200=ema_200,
        rsi_14=_rsi(closes),
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd - macd_signal,
        adx_14=_adx(series),
        atr_14=atr,
        normalized_atr=normalized_atr,
        bollinger_upper=middle + Decimal("2") * deviation,
        bollinger_middle=middle,
        bollinger_lower=middle - Decimal("2") * deviation,
        rolling_vwap=vwap,
        volume_ratio=ZERO if average_volume == 0 else volumes[-1] / average_volume,
        abnormal_volatility=abnormal,
    )

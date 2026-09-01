"""Deterministic swing structure and support/resistance zones."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from btc_sentinel.analysis.models import Direction, PriceZone, StructureSnapshot
from btc_sentinel.market_data.models import CandleSeries


@dataclass(frozen=True, slots=True)
class _Swing:
    index: int
    price: Decimal


def _swings(series: CandleSeries, radius: int = 2) -> tuple[list[_Swing], list[_Swing]]:
    candles = series.candles
    highs: list[_Swing] = []
    lows: list[_Swing] = []
    for index in range(radius, len(candles) - radius):
        window = candles[index - radius : index + radius + 1]
        candle = candles[index]
        if (
            candle.high == max(item.high for item in window)
            and sum(item.high == candle.high for item in window) == 1
        ):
            highs.append(_Swing(index, candle.high))
        if (
            candle.low == min(item.low for item in window)
            and sum(item.low == candle.low for item in window) == 1
        ):
            lows.append(_Swing(index, candle.low))
    return highs, lows


def _zones(
    swings: list[_Swing], atr: Decimal, *, latest: Decimal, support: bool
) -> tuple[PriceZone, ...]:
    width = max(atr * Decimal("0.25"), latest * Decimal("0.001"))
    candidates = [swing.price for swing in swings[-12:]]
    groups: list[list[Decimal]] = []
    for price in sorted(candidates):
        if groups and price - groups[-1][-1] <= width * Decimal("2"):
            groups[-1].append(price)
        else:
            groups.append([price])
    zones = []
    for group in groups:
        center = sum(group, Decimal("0")) / Decimal(len(group))
        if (support and center <= latest) or (not support and center >= latest):
            zones.append(PriceZone(center - width, center + width, len(group)))
    zones.sort(key=lambda zone: abs((zone.lower + zone.upper) / Decimal("2") - latest))
    return tuple(zones[:3])


def analyze_structure(series: CandleSeries, atr: Decimal) -> StructureSnapshot:
    highs, lows = _swings(series)
    recent_highs = highs[-3:]
    recent_lows = lows[-3:]
    higher_highs = len(recent_highs) >= 2 and all(
        right.price > left.price for left, right in pairwise(recent_highs)
    )
    lower_highs = len(recent_highs) >= 2 and all(
        right.price < left.price for left, right in pairwise(recent_highs)
    )
    higher_lows = len(recent_lows) >= 2 and all(
        right.price > left.price for left, right in pairwise(recent_lows)
    )
    lower_lows = len(recent_lows) >= 2 and all(
        right.price < left.price for left, right in pairwise(recent_lows)
    )
    if higher_highs and higher_lows:
        direction = Direction.BULLISH
    elif lower_highs and lower_lows:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    close = series.latest.close
    prior_high = highs[-1].price if highs else max(candle.high for candle in series.candles[:-1])
    prior_low = lows[-1].price if lows else min(candle.low for candle in series.candles[:-1])
    break_direction = Direction.NEUTRAL
    if close > prior_high:
        break_direction = Direction.BULLISH
    elif close < prior_low:
        break_direction = Direction.BEARISH
    change = Direction.NEUTRAL
    if direction is Direction.BEARISH and break_direction is Direction.BULLISH:
        change = Direction.BULLISH
    elif direction is Direction.BULLISH and break_direction is Direction.BEARISH:
        change = Direction.BEARISH

    return StructureSnapshot(
        direction=direction,
        higher_highs=higher_highs,
        higher_lows=higher_lows,
        lower_highs=lower_highs,
        lower_lows=lower_lows,
        break_of_structure=break_direction,
        change_of_character=change,
        support_zones=_zones(lows, atr, latest=close, support=True),
        resistance_zones=_zones(highs, atr, latest=close, support=False),
    )

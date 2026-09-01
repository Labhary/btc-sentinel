from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.models import (
    Candle,
    CandleSeries,
    FundingSnapshot,
    MarketSnapshot,
    OpenInterestPoint,
    TakerVolumePoint,
)
from tests.market_data_fixtures import current_open, shift_open

ANALYSIS_NOW = datetime(2026, 8, 2, 12, 0, 30, tzinfo=UTC)


def analysis_series(
    interval: MarketInterval,
    *,
    slope: Decimal = Decimal("0.8"),
    count: int = 250,
    shock: bool = False,
) -> CandleSeries:
    latest_open = shift_open(current_open(interval, ANALYSIS_NOW), interval, -1)
    first_open = shift_open(latest_open, interval, -(count - 1))
    candles: list[Candle] = []
    previous = Decimal("1000")
    for index in range(count):
        wave = Decimal((index % 12) - 6) * Decimal("0.8")
        close = Decimal("1000") + slope * Decimal(index) + wave
        if shock and index == count - 1:
            close += Decimal("700") if slope >= 0 else Decimal("-700")
        open_price = previous
        padding = Decimal("3") + abs(wave) / Decimal("4")
        high = max(open_price, close) + padding
        low = min(open_price, close) - padding
        open_time = shift_open(first_open, interval, index)
        candles.append(
            Candle(
                venue=MarketVenue.SPOT,
                interval=interval,
                open_time=open_time,
                close_time=interval.expected_close_time(open_time),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=Decimal("100") + Decimal(index % 20),
                quote_volume=Decimal("120000"),
                trade_count=100,
                taker_buy_base_volume=Decimal("55"),
                taker_buy_quote_volume=Decimal("60000"),
            )
        )
        previous = close
    return CandleSeries(tuple(candles))


def analysis_snapshot(
    slopes: dict[MarketInterval, Decimal] | None = None,
    *,
    derivatives: bool = True,
    shock_interval: MarketInterval | None = None,
) -> MarketSnapshot:
    slopes = slopes or {}
    spot = []
    for interval in (
        MarketInterval.ONE_MONTH,
        MarketInterval.ONE_WEEK,
        MarketInterval.ONE_DAY,
        MarketInterval.FOUR_HOURS,
        MarketInterval.ONE_HOUR,
        MarketInterval.FIFTEEN_MINUTES,
    ):
        count = 60 if interval is MarketInterval.ONE_MONTH else 250
        spot.append(
            analysis_series(
                interval,
                slope=slopes.get(interval, Decimal("0.8")),
                count=count,
                shock=interval is shock_interval,
            )
        )
    futures = analysis_series(MarketInterval.ONE_MINUTE, count=60)
    futures = CandleSeries(
        tuple(
            Candle(
                venue=MarketVenue.FUTURES,
                interval=candle.interval,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                quote_volume=candle.quote_volume,
                trade_count=candle.trade_count,
                taker_buy_base_volume=candle.taker_buy_base_volume,
                taker_buy_quote_volume=candle.taker_buy_quote_volume,
            )
            for candle in futures.candles
        )
    )
    taker = (
        (
            TakerVolumePoint(
                period=DerivativesPeriod.FIVE_MINUTES,
                buy_volume=Decimal("110"),
                sell_volume=Decimal("100"),
                buy_sell_ratio=Decimal("1.1"),
                period_start=ANALYSIS_NOW - timedelta(minutes=5),
            ),
        )
        if derivatives
        else ()
    )
    return MarketSnapshot(
        captured_at=ANALYSIS_NOW,
        spot_series=tuple(spot),
        futures_series=(futures,),
        funding=FundingSnapshot(
            mark_price=Decimal("1200"),
            index_price=Decimal("1200"),
            last_funding_rate=Decimal("0.0001"),
            next_funding_time=ANALYSIS_NOW + timedelta(hours=4),
            observed_at=ANALYSIS_NOW,
        ),
        open_interest=OpenInterestPoint(Decimal("1000"), ANALYSIS_NOW),
        taker_volume=taker,
    )

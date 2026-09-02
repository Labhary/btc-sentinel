from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle, CandleSeries

LIFECYCLE_START = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)


def minute_candle(
    offset: int,
    *,
    open_price: Decimal = Decimal("105"),
    high: Decimal = Decimal("106"),
    low: Decimal = Decimal("104"),
    close: Decimal = Decimal("105"),
    venue: MarketVenue = MarketVenue.SPOT,
) -> Candle:
    opened = LIFECYCLE_START + timedelta(minutes=offset)
    return Candle(
        venue=venue,
        interval=MarketInterval.ONE_MINUTE,
        open_time=opened,
        close_time=MarketInterval.ONE_MINUTE.expected_close_time(opened),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=10,
        taker_buy_base_volume=Decimal("5"),
        taker_buy_quote_volume=Decimal("500"),
    )


def minute_series(*candles: Candle) -> CandleSeries:
    return CandleSeries(tuple(candles))


def after(*candles: Candle) -> datetime:
    return candles[-1].close_time + timedelta(seconds=1)

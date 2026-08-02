from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle, CandleSeries

NOW = datetime(2026, 8, 2, 12, 0, 30, tzinfo=UTC)


def add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + (value.month - 1) + months
    return value.replace(year=absolute // 12, month=absolute % 12 + 1)


def shift_open(value: datetime, interval: MarketInterval, steps: int) -> datetime:
    if interval is MarketInterval.ONE_MONTH:
        return add_months(value, steps)
    duration = interval.fixed_duration
    assert duration is not None
    return value + duration * steps


def current_open(interval: MarketInterval, as_of: datetime = NOW) -> datetime:
    value = as_of.astimezone(UTC).replace(second=0, microsecond=0)
    if interval is MarketInterval.ONE_MINUTE:
        return value
    if interval is MarketInterval.FIVE_MINUTES:
        return value.replace(minute=value.minute - value.minute % 5)
    if interval is MarketInterval.FIFTEEN_MINUTES:
        return value.replace(minute=value.minute - value.minute % 15)
    if interval is MarketInterval.ONE_HOUR:
        return value.replace(minute=0)
    if interval is MarketInterval.FOUR_HOURS:
        return value.replace(hour=value.hour - value.hour % 4, minute=0)
    if interval is MarketInterval.ONE_DAY:
        return value.replace(hour=0, minute=0)
    if interval is MarketInterval.ONE_WEEK:
        return (value - timedelta(days=value.weekday())).replace(hour=0, minute=0)
    return value.replace(day=1, hour=0, minute=0)


def make_candle(
    open_time: datetime,
    interval: MarketInterval,
    venue: MarketVenue = MarketVenue.SPOT,
    *,
    close: str = "100",
) -> Candle:
    close_price = Decimal(close)
    return Candle(
        venue=venue,
        interval=interval,
        open_time=open_time,
        close_time=interval.expected_close_time(open_time),
        open=Decimal("100"),
        high=max(Decimal("101"), close_price),
        low=min(Decimal("99"), close_price),
        close=close_price,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=50,
        taker_buy_base_volume=Decimal("6"),
        taker_buy_quote_volume=Decimal("600"),
    )


def make_series(
    interval: MarketInterval,
    venue: MarketVenue = MarketVenue.SPOT,
    *,
    count: int = 2,
    as_of: datetime = NOW,
    close: str = "100",
) -> CandleSeries:
    latest_open = shift_open(current_open(interval, as_of), interval, -1)
    first_open = shift_open(latest_open, interval, -(count - 1))
    return CandleSeries(
        tuple(
            make_candle(shift_open(first_open, interval, index), interval, venue, close=close)
            for index in range(count)
        )
    )


def milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    difference = value - epoch
    return (
        difference.days * 86_400_000
        + difference.seconds * 1000
        + difference.microseconds // 1000
    )


def candle_row(candle: Candle) -> list[object]:
    return [
        milliseconds(candle.open_time),
        str(candle.open),
        str(candle.high),
        str(candle.low),
        str(candle.close),
        str(candle.volume),
        milliseconds(candle.close_time),
        str(candle.quote_volume),
        candle.trade_count,
        str(candle.taker_buy_base_volume),
        str(candle.taker_buy_quote_volume),
        "0",
    ]

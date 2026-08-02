"""BTC market venues and UTC Binance candle intervals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from btc_sentinel.market_data.errors import MarketDataValidationError


class MarketVenue(StrEnum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"


class MarketInterval(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"
    ONE_MONTH = "1M"

    @property
    def fixed_duration(self) -> timedelta | None:
        return _FIXED_DURATIONS.get(self)

    def next_open_time(self, open_time: datetime) -> datetime:
        value = _as_utc(open_time)
        if not self.is_open_time_aligned(value):
            raise MarketDataValidationError(
                f"{self.value} candle open time is not aligned to a UTC boundary"
            )
        if self is MarketInterval.ONE_MONTH:
            if value.month == 12:
                return value.replace(year=value.year + 1, month=1)
            return value.replace(month=value.month + 1)
        duration = self.fixed_duration
        assert duration is not None
        return value + duration

    def expected_close_time(self, open_time: datetime) -> datetime:
        return self.next_open_time(open_time) - timedelta(milliseconds=1)

    def is_open_time_aligned(self, open_time: datetime) -> bool:
        value = _as_utc(open_time)
        if value.second != 0 or value.microsecond != 0:
            return False
        if self is MarketInterval.ONE_MINUTE:
            return True
        if self is MarketInterval.FIVE_MINUTES:
            return value.minute % 5 == 0
        if self is MarketInterval.FIFTEEN_MINUTES:
            return value.minute % 15 == 0
        if self is MarketInterval.ONE_HOUR:
            return value.minute == 0
        if self is MarketInterval.FOUR_HOURS:
            return value.minute == 0 and value.hour % 4 == 0
        if self is MarketInterval.ONE_DAY:
            return value.minute == 0 and value.hour == 0
        if self is MarketInterval.ONE_WEEK:
            return value.minute == 0 and value.hour == 0 and value.weekday() == 0
        return (
            value.minute == 0
            and value.hour == 0
            and value.day == 1
        )


class DerivativesPeriod(StrEnum):
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"
    SIX_HOURS = "6h"
    TWELVE_HOURS = "12h"
    ONE_DAY = "1d"


_FIXED_DURATIONS: dict[MarketInterval, timedelta] = {
    MarketInterval.ONE_MINUTE: timedelta(minutes=1),
    MarketInterval.FIVE_MINUTES: timedelta(minutes=5),
    MarketInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    MarketInterval.ONE_HOUR: timedelta(hours=1),
    MarketInterval.FOUR_HOURS: timedelta(hours=4),
    MarketInterval.ONE_DAY: timedelta(days=1),
    MarketInterval.ONE_WEEK: timedelta(days=7),
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataValidationError("Market-data timestamps must be timezone-aware")
    return value.astimezone(UTC)


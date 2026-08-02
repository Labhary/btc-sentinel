"""Fail-closed coherence and freshness checks for market-data snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import MarketInterval
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import CandleSeries, api_decimal, utc_datetime

MAXIMUM_CANDLE_AGE: dict[MarketInterval, timedelta] = {
    MarketInterval.ONE_MINUTE: timedelta(minutes=6),
    MarketInterval.FIVE_MINUTES: timedelta(minutes=15),
    MarketInterval.FIFTEEN_MINUTES: timedelta(minutes=35),
    MarketInterval.ONE_HOUR: timedelta(hours=2),
    MarketInterval.FOUR_HOURS: timedelta(hours=6),
    MarketInterval.ONE_DAY: timedelta(hours=30),
    MarketInterval.ONE_WEEK: timedelta(days=8),
    MarketInterval.ONE_MONTH: timedelta(days=36),
}


def ensure_series_usable(
    series: CandleSeries,
    *,
    as_of: datetime,
    minimum_closed_candles: int,
) -> None:
    reference = utc_datetime(as_of, "as_of")
    if minimum_closed_candles < 1:
        raise ValueError("minimum_closed_candles must be positive")
    if len(series.candles) < minimum_closed_candles:
        raise MarketDataValidationError(
            f"{series.venue.value} {series.interval.value} has too few closed candles"
        )
    if any(not candle.is_closed_at(reference) for candle in series.candles):
        raise MarketDataValidationError("Incomplete candles cannot enter analysis")
    age = reference - series.latest.close_time
    if age < timedelta(0):
        raise MarketDataValidationError("Latest candle is timestamped in the future")
    if age > MAXIMUM_CANDLE_AGE[series.interval]:
        raise MarketDataValidationError(
            f"{series.venue.value} {series.interval.value} candle data is stale"
        )


def coherent_reference_time(
    *,
    spot_server_time: datetime,
    futures_server_time: datetime,
    local_time: datetime,
    maximum_exchange_skew: timedelta = timedelta(seconds=5),
    maximum_local_skew: timedelta = timedelta(seconds=60),
) -> datetime:
    spot = utc_datetime(spot_server_time, "spot_server_time")
    futures = utc_datetime(futures_server_time, "futures_server_time")
    local = utc_datetime(local_time, "local_time")
    if abs(spot - futures) > maximum_exchange_skew:
        raise MarketDataValidationError("Spot and futures server clocks disagree")
    conservative = min(spot, futures)
    if abs(local - conservative) > maximum_local_skew:
        raise MarketDataValidationError("Runner clock is not coherent with Binance server time")
    return conservative


def ensure_price_coherence(
    spot_price: Decimal,
    futures_price: Decimal,
    *,
    maximum_fractional_divergence: Decimal = Decimal("0.03"),
) -> None:
    spot = api_decimal(spot_price, "spot_price")
    futures = api_decimal(futures_price, "futures_price")
    maximum = api_decimal(maximum_fractional_divergence, "maximum_fractional_divergence")
    if spot <= 0 or futures <= 0 or not Decimal("0") < maximum <= Decimal("0.25"):
        raise MarketDataValidationError("Price-coherence inputs are outside safe ranges")
    divergence = abs(futures - spot) / spot
    if divergence > maximum:
        raise MarketDataValidationError("Spot and futures prices are severely dislocated")


def ensure_observation_fresh(
    observed_at: datetime,
    *,
    as_of: datetime,
    maximum_age: timedelta,
    name: str,
) -> None:
    observed = utc_datetime(observed_at, f"{name} observed_at")
    reference = utc_datetime(as_of, "as_of")
    age = reference - observed
    if age < timedelta(seconds=-5):
        raise MarketDataValidationError(f"{name} is timestamped in the future")
    if age > maximum_age:
        raise MarketDataValidationError(f"{name} is stale")

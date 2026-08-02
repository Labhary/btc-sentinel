"""Immutable, strictly validated BTC/USDT market-data records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError

BTCUSDT = "BTCUSDT"


def api_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise MarketDataValidationError(f"{name} must be a decimal string or integer")
    try:
        result = Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataValidationError(f"{name} is not a valid decimal") from exc
    if not result.is_finite():
        raise MarketDataValidationError(f"{name} must be finite")
    return result


def api_integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MarketDataValidationError(f"{name} must be an integer >= {minimum}")
    return value


def utc_from_milliseconds(value: object, name: str) -> datetime:
    milliseconds = api_integer(value, name)
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)
    except OverflowError as exc:
        raise MarketDataValidationError(f"{name} is outside the supported time range") from exc


def utc_datetime(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def validate_btc_symbol(value: object) -> str:
    if value != BTCUSDT:
        raise MarketDataValidationError("Version 1 market data accepts BTCUSDT only")
    return BTCUSDT


@dataclass(frozen=True, slots=True)
class Candle:
    venue: MarketVenue
    interval: MarketInterval
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        object.__setattr__(self, "open_time", utc_datetime(self.open_time, "open_time"))
        object.__setattr__(self, "close_time", utc_datetime(self.close_time, "close_time"))
        for field_name in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ):
            object.__setattr__(
                self,
                field_name,
                api_decimal(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "trade_count",
            api_integer(self.trade_count, "trade_count"),
        )

        if not self.interval.is_open_time_aligned(self.open_time):
            raise MarketDataValidationError("Candle open time is not aligned to its UTC interval")
        if self.close_time != self.interval.expected_close_time(self.open_time):
            raise MarketDataValidationError("Candle close time does not match its interval")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise MarketDataValidationError("Candle prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise MarketDataValidationError("Candle OHLC values are contradictory")
        if self.high < self.low:
            raise MarketDataValidationError("Candle high cannot be below candle low")
        if (
            min(
                self.volume,
                self.quote_volume,
                self.taker_buy_base_volume,
                self.taker_buy_quote_volume,
            )
            < 0
        ):
            raise MarketDataValidationError("Candle volumes cannot be negative")
        if self.taker_buy_base_volume > self.volume:
            raise MarketDataValidationError("Taker-buy base volume cannot exceed total volume")
        if self.taker_buy_quote_volume > self.quote_volume:
            raise MarketDataValidationError(
                "Taker-buy quote volume cannot exceed total quote volume"
            )

    def is_closed_at(self, as_of: datetime) -> bool:
        return self.close_time < utc_datetime(as_of, "as_of")


@dataclass(frozen=True, slots=True)
class CandleSeries:
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candles", tuple(self.candles))
        if not self.candles:
            raise MarketDataValidationError("A candle series cannot be empty")
        first = self.candles[0]
        seen: set[datetime] = set()
        previous: Candle | None = None
        for candle in self.candles:
            if (
                candle.symbol != first.symbol
                or candle.venue is not first.venue
                or candle.interval is not first.interval
            ):
                raise MarketDataValidationError(
                    "A candle series cannot mix symbols, venues, or intervals"
                )
            if candle.open_time in seen:
                raise MarketDataValidationError("Duplicate candle open time detected")
            seen.add(candle.open_time)
            if previous is not None:
                expected_open = previous.close_time + timedelta(milliseconds=1)
                if candle.open_time != expected_open:
                    raise MarketDataValidationError("Missing or unordered candle detected")
            previous = candle

    @property
    def symbol(self) -> str:
        return self.candles[0].symbol

    @property
    def venue(self) -> MarketVenue:
        return self.candles[0].venue

    @property
    def interval(self) -> MarketInterval:
        return self.candles[0].interval

    @property
    def latest(self) -> Candle:
        return self.candles[-1]

    def closed_at(self, as_of: datetime) -> CandleSeries:
        closed = tuple(candle for candle in self.candles if candle.is_closed_at(as_of))
        if not closed:
            raise MarketDataValidationError("The upstream series contains no closed candle")
        return CandleSeries(closed)


@dataclass(frozen=True, slots=True)
class FundingSnapshot:
    mark_price: Decimal
    index_price: Decimal
    last_funding_rate: Decimal
    next_funding_time: datetime
    observed_at: datetime
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        for name in ("mark_price", "index_price", "last_funding_rate"):
            object.__setattr__(self, name, api_decimal(getattr(self, name), name))
        object.__setattr__(
            self,
            "next_funding_time",
            utc_datetime(self.next_funding_time, "next_funding_time"),
        )
        object.__setattr__(self, "observed_at", utc_datetime(self.observed_at, "observed_at"))
        if self.mark_price <= 0 or self.index_price <= 0:
            raise MarketDataValidationError("Mark and index prices must be positive")
        if abs(self.last_funding_rate) > Decimal("0.10"):
            raise MarketDataValidationError("Funding rate is outside the accepted safety range")


@dataclass(frozen=True, slots=True)
class FundingRatePoint:
    funding_rate: Decimal
    funding_time: datetime
    mark_price: Decimal | None = None
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        object.__setattr__(self, "funding_rate", api_decimal(self.funding_rate, "funding_rate"))
        object.__setattr__(self, "funding_time", utc_datetime(self.funding_time, "funding_time"))
        if self.mark_price is not None:
            object.__setattr__(self, "mark_price", api_decimal(self.mark_price, "mark_price"))
            if self.mark_price <= 0:
                raise MarketDataValidationError("Funding mark price must be positive")
        if abs(self.funding_rate) > Decimal("0.10"):
            raise MarketDataValidationError("Funding rate is outside the accepted safety range")


@dataclass(frozen=True, slots=True)
class OpenInterestPoint:
    open_interest: Decimal
    observed_at: datetime
    open_interest_value: Decimal | None = None
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        object.__setattr__(
            self,
            "open_interest",
            api_decimal(self.open_interest, "open_interest"),
        )
        object.__setattr__(self, "observed_at", utc_datetime(self.observed_at, "observed_at"))
        if self.open_interest < 0:
            raise MarketDataValidationError("Open interest cannot be negative")
        if self.open_interest_value is not None:
            object.__setattr__(
                self,
                "open_interest_value",
                api_decimal(self.open_interest_value, "open_interest_value"),
            )
            if self.open_interest_value < 0:
                raise MarketDataValidationError("Open-interest value cannot be negative")


@dataclass(frozen=True, slots=True)
class TakerVolumePoint:
    period: DerivativesPeriod
    buy_volume: Decimal
    sell_volume: Decimal
    buy_sell_ratio: Decimal
    period_start: datetime
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        for name in ("buy_volume", "sell_volume", "buy_sell_ratio"):
            object.__setattr__(self, name, api_decimal(getattr(self, name), name))
        object.__setattr__(self, "period_start", utc_datetime(self.period_start, "period_start"))
        if min(self.buy_volume, self.sell_volume, self.buy_sell_ratio) < 0:
            raise MarketDataValidationError("Taker volume and ratio cannot be negative")
        if self.sell_volume > 0:
            expected = self.buy_volume / self.sell_volume
            tolerance = max(Decimal("0.0001"), expected * Decimal("0.002"))
            if abs(self.buy_sell_ratio - expected) > tolerance:
                raise MarketDataValidationError("Taker buy/sell ratio contradicts its volumes")


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", api_decimal(self.price, "order-book price"))
        object.__setattr__(self, "quantity", api_decimal(self.quantity, "order-book quantity"))
        if self.price <= 0 or self.quantity < 0:
            raise MarketDataValidationError(
                "Order-book price must be positive and quantity nonnegative"
            )


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    last_update_id: int
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    observed_at: datetime
    symbol: str = BTCUSDT

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", validate_btc_symbol(self.symbol))
        object.__setattr__(
            self,
            "last_update_id",
            api_integer(self.last_update_id, "last_update_id"),
        )
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        object.__setattr__(self, "observed_at", utc_datetime(self.observed_at, "observed_at"))
        if not self.bids or not self.asks:
            raise MarketDataValidationError("Order book must contain bids and asks")
        if self.bids != tuple(sorted(self.bids, key=lambda level: level.price, reverse=True)):
            raise MarketDataValidationError("Order-book bids are not sorted descending")
        if self.asks != tuple(sorted(self.asks, key=lambda level: level.price)):
            raise MarketDataValidationError("Order-book asks are not sorted ascending")
        if self.best_bid >= self.best_ask:
            raise MarketDataValidationError("Order book is locked or crossed")

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0].price

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal("2")

    @property
    def spread_basis_points(self) -> Decimal:
        return (self.best_ask - self.best_bid) / self.mid_price * Decimal("10000")

    @property
    def quantity_imbalance(self) -> Decimal:
        bid_quantity = sum((level.quantity for level in self.bids), Decimal("0"))
        ask_quantity = sum((level.quantity for level in self.asks), Decimal("0"))
        total = bid_quantity + ask_quantity
        return Decimal("0") if total == 0 else (bid_quantity - ask_quantity) / total


class CollectionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class DataIssue:
    code: str
    source: str
    detail: str
    required: bool

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.source.strip() or not self.detail.strip():
            raise MarketDataValidationError("Data issues require code, source, and detail")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    captured_at: datetime
    spot_series: tuple[CandleSeries, ...]
    futures_series: tuple[CandleSeries, ...]
    funding: FundingSnapshot
    open_interest: OpenInterestPoint
    funding_history: tuple[FundingRatePoint, ...] = ()
    open_interest_history: tuple[OpenInterestPoint, ...] = ()
    taker_volume: tuple[TakerVolumePoint, ...] = ()
    order_book: OrderBookSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, "captured_at"))
        for name in (
            "spot_series",
            "futures_series",
            "funding_history",
            "open_interest_history",
            "taker_volume",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.spot_series or not self.futures_series:
            raise MarketDataValidationError("A snapshot requires spot and futures candle series")
        if any(series.venue is not MarketVenue.SPOT for series in self.spot_series):
            raise MarketDataValidationError("Spot snapshot series must come from the spot venue")
        if any(series.venue is not MarketVenue.FUTURES for series in self.futures_series):
            raise MarketDataValidationError(
                "Futures snapshot series must come from the futures venue"
            )
        keys = [
            (series.venue, series.interval) for series in (*self.spot_series, *self.futures_series)
        ]
        if len(keys) != len(set(keys)):
            raise MarketDataValidationError("A snapshot contains duplicate venue/interval series")

    def series_for(self, venue: MarketVenue, interval: MarketInterval) -> CandleSeries:
        for series in (*self.spot_series, *self.futures_series):
            if series.venue is venue and series.interval is interval:
                return series
        raise MarketDataValidationError(f"Snapshot is missing {venue.value} {interval.value}")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    status: CollectionStatus
    snapshot: MarketSnapshot | None
    issues: tuple[DataIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.status is CollectionStatus.REJECTED:
            if self.snapshot is not None or not any(issue.required for issue in self.issues):
                raise MarketDataValidationError(
                    "Rejected collection requires a required issue and no snapshot"
                )
        elif self.snapshot is None:
            raise MarketDataValidationError("Accepted or degraded collection requires a snapshot")
        if self.status is CollectionStatus.ACCEPTED and self.issues:
            raise MarketDataValidationError("Accepted collection cannot contain data issues")
        if self.status is CollectionStatus.DEGRADED and not self.issues:
            raise MarketDataValidationError("Degraded collection requires at least one issue")

"""Phase 3 coherent market snapshot collector with explicit optional inputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataError, MarketDataValidationError
from btc_sentinel.market_data.models import (
    CandleSeries,
    CollectionResult,
    CollectionStatus,
    DataIssue,
    FundingRatePoint,
    FundingSnapshot,
    MarketSnapshot,
    OpenInterestPoint,
    OrderBookSnapshot,
    TakerVolumePoint,
)
from btc_sentinel.market_data.validation import (
    coherent_reference_time,
    ensure_observation_fresh,
    ensure_price_coherence,
    ensure_series_usable,
)


class BinanceMarketData(Protocol):
    def spot_server_time(self) -> datetime: ...

    def futures_server_time(self) -> datetime: ...

    def spot_candles(
        self,
        interval: MarketInterval,
        *,
        limit: int,
        as_of: datetime | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CandleSeries: ...

    def futures_candles(
        self,
        interval: MarketInterval,
        *,
        limit: int,
        as_of: datetime | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CandleSeries: ...

    def funding_snapshot(self) -> FundingSnapshot: ...

    def open_interest(self) -> OpenInterestPoint: ...

    def funding_history(
        self,
        *,
        limit: int = 30,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[FundingRatePoint, ...]: ...

    def open_interest_history(
        self,
        period: DerivativesPeriod,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[OpenInterestPoint, ...]: ...

    def taker_volume(
        self,
        period: DerivativesPeriod,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[TakerVolumePoint, ...]: ...

    def spot_order_book(
        self,
        *,
        observed_at: datetime,
        limit: int = 100,
    ) -> OrderBookSnapshot: ...


@dataclass(frozen=True, slots=True)
class SeriesRequirement:
    venue: MarketVenue
    interval: MarketInterval
    minimum_closed_candles: int
    request_limit: int

    def __post_init__(self) -> None:
        maximum = 1000 if self.venue is MarketVenue.SPOT else 1500
        if not 1 <= self.minimum_closed_candles < self.request_limit <= maximum:
            raise ValueError("Series limits must leave room for one incomplete upstream candle")


def _default_requirements() -> tuple[SeriesRequirement, ...]:
    return (
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_MONTH, 60, 120),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_WEEK, 210, 260),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_DAY, 250, 300),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.FOUR_HOURS, 250, 300),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_HOUR, 250, 300),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.FIFTEEN_MINUTES, 250, 300),
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_MINUTE, 300, 500),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.ONE_DAY, 100, 150),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.FOUR_HOURS, 100, 150),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.ONE_HOUR, 100, 150),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.FIFTEEN_MINUTES, 100, 150),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.ONE_MINUTE, 100, 150),
    )


@dataclass(frozen=True, slots=True)
class MarketDataPolicy:
    requirements: tuple[SeriesRequirement, ...] = ()
    optional_history_limit: int = 100
    order_book_limit: int = 100

    def __post_init__(self) -> None:
        if not self.requirements:
            object.__setattr__(self, "requirements", _default_requirements())
        else:
            object.__setattr__(self, "requirements", tuple(self.requirements))
        keys = [(item.venue, item.interval) for item in self.requirements]
        if len(keys) != len(set(keys)):
            raise ValueError("Market-data requirements must be unique")
        required_keys = set(keys)
        if (MarketVenue.SPOT, MarketInterval.ONE_MINUTE) not in required_keys:
            raise ValueError("Policy must include required SPOT 1m candles")
        if (MarketVenue.FUTURES, MarketInterval.ONE_MINUTE) not in required_keys:
            raise ValueError("Policy must include required FUTURES 1m candles")
        if not 1 <= self.optional_history_limit <= 500:
            raise ValueError("optional_history_limit must be between 1 and 500")
        if self.order_book_limit not in {5, 10, 20, 50, 100}:
            raise ValueError("Unsupported low-weight order-book limit")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketDataCollector:
    def __init__(
        self,
        client: BinanceMarketData,
        *,
        policy: MarketDataPolicy | None = None,
        local_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.client = client
        self.policy = policy or MarketDataPolicy()
        self.local_clock = local_clock

    def collect(self) -> CollectionResult:
        try:
            as_of = coherent_reference_time(
                spot_server_time=self.client.spot_server_time(),
                futures_server_time=self.client.futures_server_time(),
                local_time=self.local_clock(),
            )
            spot_series, futures_series = self._required_series(as_of)
            funding = self.client.funding_snapshot()
            open_interest = self.client.open_interest()
            ensure_observation_fresh(
                funding.observed_at,
                as_of=as_of,
                maximum_age=timedelta(minutes=5),
                name="funding snapshot",
            )
            ensure_observation_fresh(
                open_interest.observed_at,
                as_of=as_of,
                maximum_age=timedelta(minutes=5),
                name="open interest",
            )
            spot_one_minute = next(
                series for series in spot_series if series.interval is MarketInterval.ONE_MINUTE
            )
            futures_one_minute = next(
                series for series in futures_series if series.interval is MarketInterval.ONE_MINUTE
            )
            ensure_price_coherence(spot_one_minute.latest.close, funding.mark_price)
            ensure_price_coherence(spot_one_minute.latest.close, funding.index_price)
            ensure_price_coherence(futures_one_minute.latest.close, funding.mark_price)
        except MarketDataError as exc:
            return self._rejected("core", exc)

        issues: list[DataIssue] = []
        funding_history = self._optional(
            "funding_history",
            lambda: self.client.funding_history(
                limit=min(self.policy.optional_history_limit, 1000)
            ),
            issues,
        )
        open_interest_history = self._optional(
            "open_interest_history",
            lambda: self.client.open_interest_history(
                DerivativesPeriod.FIVE_MINUTES,
                limit=self.policy.optional_history_limit,
            ),
            issues,
        )
        taker_volume = self._optional(
            "taker_volume",
            lambda: self.client.taker_volume(
                DerivativesPeriod.FIVE_MINUTES,
                limit=self.policy.optional_history_limit,
            ),
            issues,
        )
        order_book = self._optional(
            "spot_order_book",
            lambda: self.client.spot_order_book(
                observed_at=as_of,
                limit=self.policy.order_book_limit,
            ),
            issues,
        )

        funding_points = self._fresh_optional_history(
            funding_history,
            source="funding_history",
            timestamp=lambda point: point.funding_time,
            maximum_age=timedelta(hours=36),
            as_of=as_of,
            issues=issues,
        )
        oi_points = self._fresh_optional_history(
            open_interest_history,
            source="open_interest_history",
            timestamp=lambda point: point.observed_at,
            maximum_age=timedelta(minutes=15),
            as_of=as_of,
            issues=issues,
        )
        taker_points = self._fresh_optional_history(
            taker_volume,
            source="taker_volume",
            timestamp=lambda point: point.period_start,
            maximum_age=timedelta(minutes=15),
            as_of=as_of,
            issues=issues,
        )
        if order_book is not None and not isinstance(order_book, OrderBookSnapshot):
            issues.append(
                self._issue(
                    "spot_order_book",
                    MarketDataValidationError("spot_order_book returned an invalid record"),
                    required=False,
                )
            )
            order_book = None
        try:
            ensure_observation_fresh(
                as_of,
                as_of=self.local_clock(),
                maximum_age=timedelta(minutes=2),
                name="market-data collection",
            )
        except MarketDataError as exc:
            return self._rejected("collection_duration", exc)

        snapshot = MarketSnapshot(
            captured_at=as_of,
            spot_series=spot_series,
            futures_series=futures_series,
            funding=funding,
            open_interest=open_interest,
            funding_history=funding_points,
            open_interest_history=oi_points,
            taker_volume=taker_points,
            order_book=order_book,
        )
        return CollectionResult(
            status=CollectionStatus.DEGRADED if issues else CollectionStatus.ACCEPTED,
            snapshot=snapshot,
            issues=tuple(issues),
        )

    def _required_series(
        self,
        as_of: datetime,
    ) -> tuple[tuple[CandleSeries, ...], tuple[CandleSeries, ...]]:
        spot: list[CandleSeries] = []
        futures: list[CandleSeries] = []
        for requirement in self.policy.requirements:
            if requirement.venue is MarketVenue.SPOT:
                series = self.client.spot_candles(
                    requirement.interval,
                    limit=requirement.request_limit,
                    as_of=as_of,
                )
                spot.append(series)
            else:
                series = self.client.futures_candles(
                    requirement.interval,
                    limit=requirement.request_limit,
                    as_of=as_of,
                )
                futures.append(series)
            if series.venue is not requirement.venue or series.interval is not requirement.interval:
                raise MarketDataValidationError(
                    "Market-data client returned a series for the wrong venue or interval"
                )
            ensure_series_usable(
                series,
                as_of=as_of,
                minimum_closed_candles=requirement.minimum_closed_candles,
            )
        if not spot or not futures:
            raise MarketDataValidationError("Policy must require both spot and futures candles")
        return tuple(spot), tuple(futures)

    def _optional[T](
        self,
        source: str,
        operation: Callable[[], T],
        issues: list[DataIssue],
    ) -> T | None:
        try:
            value = operation()
            if isinstance(value, tuple) and not value:
                raise MarketDataValidationError(f"{source} returned no records")
            return value
        except MarketDataError as exc:
            issues.append(self._issue(source, exc, required=False))
            return None

    def _fresh_optional_history[T](
        self,
        value: tuple[T, ...] | None,
        *,
        source: str,
        timestamp: Callable[[T], datetime],
        maximum_age: timedelta,
        as_of: datetime,
        issues: list[DataIssue],
    ) -> tuple[T, ...]:
        if value is None:
            return ()
        if not isinstance(value, tuple):  # defensive boundary for runtime Protocol implementations
            issues.append(
                self._issue(
                    source,
                    MarketDataValidationError(f"{source} has an invalid internal type"),
                    required=False,
                )
            )
            return ()
        try:
            ensure_observation_fresh(
                timestamp(value[-1]),
                as_of=as_of,
                maximum_age=maximum_age,
                name=source,
            )
        except MarketDataError as exc:
            issues.append(self._issue(source, exc, required=False))
            return ()
        return value

    @classmethod
    def _rejected(cls, source: str, error: MarketDataError) -> CollectionResult:
        return CollectionResult(
            status=CollectionStatus.REJECTED,
            snapshot=None,
            issues=(cls._issue(source, error, required=True),),
        )

    @staticmethod
    def _issue(source: str, error: MarketDataError, *, required: bool) -> DataIssue:
        return DataIssue(
            code=error.__class__.__name__,
            source=source,
            detail=str(error),
            required=required,
        )

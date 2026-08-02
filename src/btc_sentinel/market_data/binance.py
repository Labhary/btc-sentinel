"""Strict parsers for unauthenticated Binance BTC/USDT market-data endpoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import (
    BTCUSDT,
    Candle,
    CandleSeries,
    FundingRatePoint,
    FundingSnapshot,
    OpenInterestPoint,
    OrderBookLevel,
    OrderBookSnapshot,
    TakerVolumePoint,
    api_decimal,
    api_integer,
    utc_datetime,
    utc_from_milliseconds,
    validate_btc_symbol,
)
from btc_sentinel.market_data.transport import (
    FUTURES_ORIGIN,
    SPOT_ORIGIN,
    JsonTransport,
)


class BinancePublicClient:
    """BTC-only client that sends no API key, signature, or order request."""

    def __init__(self, transport: JsonTransport) -> None:
        self.transport = transport

    def spot_server_time(self) -> datetime:
        return self._server_time(SPOT_ORIGIN, "/api/v3/time")

    def futures_server_time(self) -> datetime:
        return self._server_time(FUTURES_ORIGIN, "/fapi/v1/time")

    def spot_candles(
        self,
        interval: MarketInterval,
        *,
        limit: int,
        as_of: datetime | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CandleSeries:
        self._validate_limit(limit, maximum=1000)
        params = self._candle_params(interval, limit, start_at, end_at)
        payload = self.transport.get_json(SPOT_ORIGIN, "/api/v3/klines", params)
        reference_time = self.spot_server_time() if as_of is None else utc_datetime(as_of, "as_of")
        return self._parse_candles(payload, MarketVenue.SPOT, interval).closed_at(reference_time)

    def futures_candles(
        self,
        interval: MarketInterval,
        *,
        limit: int,
        as_of: datetime | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CandleSeries:
        self._validate_limit(limit, maximum=1500)
        params = self._candle_params(interval, limit, start_at, end_at)
        payload = self.transport.get_json(FUTURES_ORIGIN, "/fapi/v1/klines", params)
        reference_time = (
            self.futures_server_time()
            if as_of is None
            else utc_datetime(as_of, "as_of")
        )
        return self._parse_candles(payload, MarketVenue.FUTURES, interval).closed_at(reference_time)

    def funding_snapshot(self) -> FundingSnapshot:
        payload = _object(
            self.transport.get_json(
                FUTURES_ORIGIN,
                "/fapi/v1/premiumIndex",
                {"symbol": BTCUSDT},
            ),
            "funding snapshot",
        )
        validate_btc_symbol(payload.get("symbol"))
        return FundingSnapshot(
            symbol=BTCUSDT,
            mark_price=api_decimal(payload.get("markPrice"), "markPrice"),
            index_price=api_decimal(payload.get("indexPrice"), "indexPrice"),
            last_funding_rate=api_decimal(payload.get("lastFundingRate"), "lastFundingRate"),
            next_funding_time=utc_from_milliseconds(
                payload.get("nextFundingTime"),
                "nextFundingTime",
            ),
            observed_at=utc_from_milliseconds(payload.get("time"), "funding snapshot time"),
        )

    def funding_history(
        self,
        *,
        limit: int = 30,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[FundingRatePoint, ...]:
        self._validate_limit(limit, maximum=1000)
        params: dict[str, str | int] = {"symbol": BTCUSDT, "limit": limit}
        params.update(_time_range_params(start_at, end_at))
        rows = _array(
            self.transport.get_json(FUTURES_ORIGIN, "/fapi/v1/fundingRate", params),
            "funding history",
        )
        points: list[FundingRatePoint] = []
        for raw in rows:
            row = _object(raw, "funding history row")
            validate_btc_symbol(row.get("symbol"))
            mark_price_raw = row.get("markPrice")
            points.append(
                FundingRatePoint(
                    funding_rate=api_decimal(row.get("fundingRate"), "fundingRate"),
                    funding_time=utc_from_milliseconds(row.get("fundingTime"), "fundingTime"),
                    mark_price=(
                        None
                        if mark_price_raw is None or mark_price_raw == ""
                        else api_decimal(mark_price_raw, "funding markPrice")
                    ),
                )
            )
        return _ordered_unique(points, lambda point: point.funding_time, "funding history")

    def open_interest(self) -> OpenInterestPoint:
        payload = _object(
            self.transport.get_json(
                FUTURES_ORIGIN,
                "/fapi/v1/openInterest",
                {"symbol": BTCUSDT},
            ),
            "open interest",
        )
        validate_btc_symbol(payload.get("symbol"))
        return OpenInterestPoint(
            open_interest=api_decimal(payload.get("openInterest"), "openInterest"),
            observed_at=utc_from_milliseconds(payload.get("time"), "open-interest time"),
        )

    def open_interest_history(
        self,
        period: DerivativesPeriod,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[OpenInterestPoint, ...]:
        self._validate_limit(limit, maximum=500)
        params: dict[str, str | int] = {
            "symbol": BTCUSDT,
            "period": period.value,
            "limit": limit,
        }
        params.update(_time_range_params(start_at, end_at))
        rows = _array(
            self.transport.get_json(
                FUTURES_ORIGIN,
                "/futures/data/openInterestHist",
                params,
            ),
            "open-interest history",
        )
        points: list[OpenInterestPoint] = []
        for raw in rows:
            row = _object(raw, "open-interest history row")
            validate_btc_symbol(row.get("symbol"))
            points.append(
                OpenInterestPoint(
                    open_interest=api_decimal(row.get("sumOpenInterest"), "sumOpenInterest"),
                    open_interest_value=api_decimal(
                        row.get("sumOpenInterestValue"),
                        "sumOpenInterestValue",
                    ),
                    observed_at=utc_from_milliseconds(row.get("timestamp"), "OI timestamp"),
                )
            )
        return _ordered_unique(points, lambda point: point.observed_at, "open-interest history")

    def taker_volume(
        self,
        period: DerivativesPeriod,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[TakerVolumePoint, ...]:
        self._validate_limit(limit, maximum=500)
        params: dict[str, str | int] = {
            "symbol": BTCUSDT,
            "period": period.value,
            "limit": limit,
        }
        params.update(_time_range_params(start_at, end_at))
        rows = _array(
            self.transport.get_json(
                FUTURES_ORIGIN,
                "/futures/data/takerlongshortRatio",
                params,
            ),
            "taker volume",
        )
        points: list[TakerVolumePoint] = []
        for raw in rows:
            row = _object(raw, "taker-volume row")
            symbol = row.get("symbol", BTCUSDT)
            validate_btc_symbol(symbol)
            points.append(
                TakerVolumePoint(
                    period=period,
                    buy_volume=api_decimal(row.get("buyVol"), "buyVol"),
                    sell_volume=api_decimal(row.get("sellVol"), "sellVol"),
                    buy_sell_ratio=api_decimal(row.get("buySellRatio"), "buySellRatio"),
                    period_start=utc_from_milliseconds(row.get("timestamp"), "taker timestamp"),
                )
            )
        return _ordered_unique(points, lambda point: point.period_start, "taker-volume history")

    def spot_order_book(
        self,
        *,
        observed_at: datetime,
        limit: int = 100,
    ) -> OrderBookSnapshot:
        if limit not in {5, 10, 20, 50, 100}:
            raise MarketDataValidationError("Order-book limit must be 5, 10, 20, 50, or 100")
        payload = _object(
            self.transport.get_json(
                SPOT_ORIGIN,
                "/api/v3/depth",
                {"symbol": BTCUSDT, "limit": limit},
            ),
            "spot order book",
        )
        bids = _book_levels(payload.get("bids"), "bids")
        asks = _book_levels(payload.get("asks"), "asks")
        return OrderBookSnapshot(
            last_update_id=api_integer(payload.get("lastUpdateId"), "lastUpdateId"),
            bids=bids,
            asks=asks,
            observed_at=utc_datetime(observed_at, "observed_at"),
        )

    def _server_time(self, origin: str, path: str) -> datetime:
        payload = _object(self.transport.get_json(origin, path, {}), "server time")
        return utc_from_milliseconds(payload.get("serverTime"), "serverTime")

    @staticmethod
    def _candle_params(
        interval: MarketInterval,
        limit: int,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "symbol": BTCUSDT,
            "interval": interval.value,
            "limit": limit,
        }
        params.update(_time_range_params(start_at, end_at))
        return params

    @staticmethod
    def _parse_candles(
        payload: object,
        venue: MarketVenue,
        interval: MarketInterval,
    ) -> CandleSeries:
        rows = _array(payload, "candle response")
        candles: list[Candle] = []
        for raw in rows:
            if not isinstance(raw, list) or len(raw) != 12:
                raise MarketDataValidationError("Candle row must contain exactly 12 fields")
            candles.append(
                Candle(
                    venue=venue,
                    interval=interval,
                    open_time=utc_from_milliseconds(raw[0], "candle open time"),
                    open=api_decimal(raw[1], "candle open"),
                    high=api_decimal(raw[2], "candle high"),
                    low=api_decimal(raw[3], "candle low"),
                    close=api_decimal(raw[4], "candle close"),
                    volume=api_decimal(raw[5], "candle volume"),
                    close_time=utc_from_milliseconds(raw[6], "candle close time"),
                    quote_volume=api_decimal(raw[7], "candle quote volume"),
                    trade_count=api_integer(raw[8], "candle trade count"),
                    taker_buy_base_volume=api_decimal(raw[9], "taker buy base volume"),
                    taker_buy_quote_volume=api_decimal(raw[10], "taker buy quote volume"),
                )
            )
        return CandleSeries(tuple(candles))

    @staticmethod
    def _validate_limit(limit: int, *, maximum: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise MarketDataValidationError(f"Request limit must be between 1 and {maximum}")


def _time_range_params(
    start_at: datetime | None,
    end_at: datetime | None,
) -> dict[str, int]:
    start = None if start_at is None else utc_datetime(start_at, "start_at")
    end = None if end_at is None else utc_datetime(end_at, "end_at")
    if start is not None and end is not None and start > end:
        raise MarketDataValidationError("start_at cannot be later than end_at")
    result: dict[str, int] = {}
    if start is not None:
        result["startTime"] = _milliseconds(start)
    if end is not None:
        result["endTime"] = _milliseconds(end)
    return result


def _milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    difference = value - epoch
    return (
        difference.days * 86_400_000
        + difference.seconds * 1000
        + difference.microseconds // 1000
    )


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MarketDataValidationError(f"{name} must be a JSON object")
    return value


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise MarketDataValidationError(f"{name} must be a JSON array")
    return value


def _book_levels(value: object, name: str) -> tuple[OrderBookLevel, ...]:
    rows = _array(value, f"order-book {name}")
    levels: list[OrderBookLevel] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise MarketDataValidationError(
                f"Order-book {name} row must contain price and quantity"
            )
        levels.append(
            OrderBookLevel(
                price=api_decimal(row[0], f"{name} price"),
                quantity=api_decimal(row[1], f"{name} quantity"),
            )
        )
    return tuple(levels)


def _ordered_unique[T](
    values: list[T],
    key: Callable[[T], datetime],
    name: str,
) -> tuple[T, ...]:
    keys = [key(value) for value in values]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise MarketDataValidationError(f"{name} must be ordered and unique")
    return tuple(values)

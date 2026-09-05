"""Disk-backed, point-in-time historical candle resampling without look-ahead."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from btc_sentinel.backtesting.dataset import HistoricalDatasetLoader
from btc_sentinel.backtesting.monthly_dataset import NativeMonthlyLoader, NativeMonthlySummary
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import (
    Candle,
    CandleSeries,
    OpenInterestPoint,
    OrderBookSnapshot,
    TakerVolumePoint,
)
from btc_sentinel.time_utils import ensure_utc

_RESAMPLED_INTERVALS = (
    MarketInterval.FIFTEEN_MINUTES,
    MarketInterval.ONE_HOUR,
    MarketInterval.FOUR_HOURS,
    MarketInterval.ONE_DAY,
    MarketInterval.ONE_WEEK,
    MarketInterval.ONE_MONTH,
)


def _epoch_milliseconds(value: datetime) -> int:
    utc = ensure_utc(value)
    return int(utc.timestamp()) * 1_000 + utc.microsecond // 1_000


def _from_epoch_milliseconds(value: int) -> datetime:
    seconds, milliseconds = divmod(value, 1_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=milliseconds * 1_000)


def _bucket_open(value: datetime, interval: MarketInterval) -> datetime:
    utc = ensure_utc(value).replace(second=0, microsecond=0)
    if interval is MarketInterval.FIFTEEN_MINUTES:
        return utc.replace(minute=(utc.minute // 15) * 15)
    if interval is MarketInterval.ONE_HOUR:
        return utc.replace(minute=0)
    if interval is MarketInterval.FOUR_HOURS:
        return utc.replace(hour=(utc.hour // 4) * 4, minute=0)
    if interval is MarketInterval.ONE_DAY:
        return utc.replace(hour=0, minute=0)
    if interval is MarketInterval.ONE_WEEK:
        return (utc - timedelta(days=utc.weekday())).replace(hour=0, minute=0)
    if interval is MarketInterval.ONE_MONTH:
        return utc.replace(day=1, hour=0, minute=0)
    if interval is MarketInterval.ONE_MINUTE:
        return utc
    raise DomainValidationError(f"Historical resampling does not support {interval.value}")


@dataclass(slots=True)
class _Bucket:
    interval: MarketInterval
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal
    first_one_minute_open: datetime
    last_one_minute_close: datetime
    one_minute_count: int

    @classmethod
    def start(cls, candle: Candle, interval: MarketInterval) -> _Bucket:
        return cls(
            interval,
            _bucket_open(candle.open_time, interval),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.quote_volume,
            candle.trade_count,
            candle.taker_buy_base_volume,
            candle.taker_buy_quote_volume,
            candle.open_time,
            candle.close_time,
            1,
        )

    def add(self, candle: Candle) -> None:
        self.high = max(self.high, candle.high)
        self.low = min(self.low, candle.low)
        self.close = candle.close
        self.volume += candle.volume
        self.quote_volume += candle.quote_volume
        self.trade_count += candle.trade_count
        self.taker_buy_base_volume += candle.taker_buy_base_volume
        self.taker_buy_quote_volume += candle.taker_buy_quote_volume
        self.last_one_minute_close = candle.close_time
        self.one_minute_count += 1

    def complete(self) -> bool:
        expected_minutes = int(
            (
                self.interval.expected_close_time(self.open_time)
                + timedelta(milliseconds=1)
                - self.open_time
            ).total_seconds()
            // 60
        )
        return (
            self.first_one_minute_open == self.open_time
            and self.last_one_minute_close == self.interval.expected_close_time(self.open_time)
            and self.one_minute_count == expected_minutes
        )

    def candle(self) -> Candle:
        return Candle(
            venue=MarketVenue.SPOT,
            interval=self.interval,
            open_time=self.open_time,
            close_time=self.interval.expected_close_time(self.open_time),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            trade_count=self.trade_count,
            taker_buy_base_volume=self.taker_buy_base_volume,
            taker_buy_quote_volume=self.taker_buy_quote_volume,
        )


@dataclass(frozen=True, slots=True)
class HistoricalImportSummary:
    dataset_id: str
    manifest_sha256: str
    one_minute_candles: int
    resampled_counts: tuple[tuple[MarketInterval, int], ...]


@dataclass(frozen=True, slots=True)
class HistoricalMarketView:
    captured_at: datetime
    spot_series: tuple[CandleSeries, ...]
    open_interest_history: tuple[OpenInterestPoint, ...] = ()
    taker_volume: tuple[TakerVolumePoint, ...] = ()
    order_book: OrderBookSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "captured_at", ensure_utc(self.captured_at))
        object.__setattr__(self, "spot_series", tuple(self.spot_series))

    def series_for(self, venue: MarketVenue, interval: MarketInterval) -> CandleSeries:
        if venue is MarketVenue.SPOT:
            for series in self.spot_series:
                if series.interval is interval:
                    return series
        raise DomainValidationError(f"Historical view is missing {venue.value} {interval.value}")


class HistoricalReplayStore:
    """Persist validated history and expose only data closed before each decision."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self._series_cache: dict[tuple[MarketInterval, int], CandleSeries] = {}
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS replay_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_candles (
                interval TEXT NOT NULL,
                open_time_ms INTEGER NOT NULL,
                close_time_ms INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                quote_volume TEXT NOT NULL,
                trade_count INTEGER NOT NULL,
                taker_buy_base_volume TEXT NOT NULL,
                taker_buy_quote_volume TEXT NOT NULL,
                PRIMARY KEY (interval, open_time_ms)
            );
            CREATE INDEX IF NOT EXISTS replay_candles_closed
            ON replay_candles(interval, close_time_ms);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> HistoricalReplayStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def import_manifest(
        self,
        manifest_path: Path,
        loader: HistoricalDatasetLoader | None = None,
    ) -> HistoricalImportSummary:
        if self.connection.execute("SELECT 1 FROM replay_metadata LIMIT 1").fetchone():
            raise DomainValidationError("Historical replay store has already been initialized")
        source = loader or HistoricalDatasetLoader()
        buckets: dict[MarketInterval, _Bucket] = {}
        counts = {interval: 0 for interval in _RESAMPLED_INTERVALS}
        one_minute_count = 0

        def consume(candle: Candle) -> None:
            nonlocal one_minute_count
            self._insert(candle)
            one_minute_count += 1
            for interval in _RESAMPLED_INTERVALS:
                bucket_open = _bucket_open(candle.open_time, interval)
                current = buckets.get(interval)
                if current is None or current.open_time != bucket_open:
                    if current is not None and current.complete():
                        self._insert(current.candle())
                        counts[interval] += 1
                    buckets[interval] = _Bucket.start(candle, interval)
                else:
                    current.add(candle)

        try:
            self.connection.execute("BEGIN")
            dataset = source.visit(manifest_path, consume)
            for interval, bucket in buckets.items():
                if bucket.complete():
                    self._insert(bucket.candle())
                    counts[interval] += 1
            self.connection.executemany(
                "INSERT INTO replay_metadata(key, value) VALUES (?, ?)",
                (
                    ("dataset_id", dataset.manifest.dataset_id),
                    ("manifest_sha256", dataset.manifest_sha256),
                    ("coverage_start", dataset.manifest.coverage_start.isoformat()),
                    ("coverage_end", dataset.manifest.coverage_end.isoformat()),
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return HistoricalImportSummary(
            dataset.manifest.dataset_id,
            dataset.manifest_sha256,
            one_minute_count,
            tuple((interval, counts[interval]) for interval in _RESAMPLED_INTERVALS),
        )

    def import_native_monthly_manifest(
        self,
        manifest_path: Path,
        loader: NativeMonthlyLoader | None = None,
    ) -> NativeMonthlySummary:
        if not self.connection.execute("SELECT 1 FROM replay_metadata LIMIT 1").fetchone():
            raise DomainValidationError("One-minute replay history must be imported first")
        if self.connection.execute(
            "SELECT 1 FROM replay_metadata WHERE key = 'native_monthly_dataset_id'"
        ).fetchone():
            raise DomainValidationError("Native monthly replay history is already initialized")
        source = loader or NativeMonthlyLoader()
        base_start, _base_end = self.coverage()
        try:
            self.connection.execute("BEGIN")
            native_candles: list[Candle] = []
            summary = source.visit(manifest_path, native_candles.append)
            if summary.coverage_start > base_start or summary.coverage_end <= base_start:
                raise DomainValidationError(
                    "Native monthly coverage must warm up and overlap the one-minute replay"
                )
            self.connection.execute(
                """
                DELETE FROM replay_candles
                WHERE interval = ? AND open_time_ms >= ? AND open_time_ms < ?
                """,
                (
                    MarketInterval.ONE_MONTH.value,
                    _epoch_milliseconds(summary.coverage_start),
                    _epoch_milliseconds(summary.coverage_end),
                ),
            )
            for candle in native_candles:
                self._insert(candle)
            self.connection.executemany(
                "INSERT INTO replay_metadata(key, value) VALUES (?, ?)",
                (
                    ("native_monthly_dataset_id", summary.dataset_id),
                    ("native_monthly_manifest_sha256", summary.manifest_sha256),
                    ("native_monthly_coverage_start", summary.coverage_start.isoformat()),
                    ("native_monthly_coverage_end", summary.coverage_end.isoformat()),
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self._series_cache.clear()
        return summary

    def _insert(self, candle: Candle) -> None:
        self.connection.execute(
            """
            INSERT INTO replay_candles(
                interval, open_time_ms, close_time_ms, open, high, low, close,
                volume, quote_volume, trade_count, taker_buy_base_volume,
                taker_buy_quote_volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candle.interval.value,
                _epoch_milliseconds(candle.open_time),
                _epoch_milliseconds(candle.close_time),
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                str(candle.volume),
                str(candle.quote_volume),
                candle.trade_count,
                str(candle.taker_buy_base_volume),
                str(candle.taker_buy_quote_volume),
            ),
        )

    def metadata(self, key: str) -> str:
        if key not in {
            "dataset_id",
            "manifest_sha256",
            "coverage_start",
            "coverage_end",
            "native_monthly_dataset_id",
            "native_monthly_manifest_sha256",
            "native_monthly_coverage_start",
            "native_monthly_coverage_end",
        }:
            raise DomainValidationError("Unknown historical replay metadata key")
        row = self.connection.execute(
            "SELECT value FROM replay_metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise DomainValidationError("Historical replay store is not initialized")
        return str(row[0])

    def coverage(self) -> tuple[datetime, datetime]:
        return (
            ensure_utc(datetime.fromisoformat(self.metadata("coverage_start"))),
            ensure_utc(datetime.fromisoformat(self.metadata("coverage_end"))),
        )

    def iter_candles(
        self,
        interval: MarketInterval,
        start: datetime,
        end: datetime,
    ) -> Iterator[Candle]:
        if interval is not MarketInterval.ONE_MINUTE:
            raise DomainValidationError("Streaming replay currently supports one-minute candles")
        first = ensure_utc(start)
        stop = ensure_utc(end)
        if stop <= first or not interval.is_open_time_aligned(first):
            raise DomainValidationError("Historical streaming range is invalid")
        cursor = self.connection.execute(
            """
            SELECT open_time_ms, close_time_ms, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_base_volume,
                   taker_buy_quote_volume
            FROM replay_candles
            WHERE interval = ? AND open_time_ms >= ? AND open_time_ms < ?
            ORDER BY open_time_ms
            """,
            (interval.value, _epoch_milliseconds(first), _epoch_milliseconds(stop)),
        )
        expected = first
        seen = False
        for row in cursor:
            candle = self._row(interval, row)
            if candle.open_time != expected:
                raise DomainValidationError("Historical streaming range contains a candle gap")
            seen = True
            expected = candle.open_time + timedelta(minutes=1)
            yield candle
        if not seen:
            raise DomainValidationError("Historical streaming range contains no candles")

    def series_at(
        self,
        interval: MarketInterval,
        as_of: datetime,
        *,
        limit: int = 200,
    ) -> CandleSeries:
        if interval not in (MarketInterval.ONE_MINUTE, *_RESAMPLED_INTERVALS):
            raise DomainValidationError("Unsupported historical replay interval")
        if not 1 <= limit <= 1_000:
            raise DomainValidationError("Historical replay limit must be between 1 and 1000")
        latest_row = self.connection.execute(
            """
            SELECT open_time_ms, close_time_ms, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_base_volume,
                   taker_buy_quote_volume
            FROM replay_candles
            WHERE interval = ? AND close_time_ms < ?
            ORDER BY close_time_ms DESC
            LIMIT 1
            """,
            (interval.value, _epoch_milliseconds(as_of)),
        ).fetchone()
        if latest_row is None:
            raise DomainValidationError(f"No completed historical {interval.value} candles")
        cache_key = (interval, limit)
        cached = self._series_cache.get(cache_key)
        latest_open = _from_epoch_milliseconds(int(latest_row[0]))
        if cached is not None:
            if latest_open == cached.latest.open_time:
                return cached
            expected_open = cached.latest.close_time + timedelta(milliseconds=1)
            if latest_open == expected_open:
                latest = self._row(interval, latest_row)
                values = (*cached.candles, latest)
                if len(values) > limit:
                    values = values[-limit:]
                result = CandleSeries(values)
                self._series_cache[cache_key] = result
                return result
        rows = self.connection.execute(
            """
            SELECT open_time_ms, close_time_ms, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_base_volume,
                   taker_buy_quote_volume
            FROM replay_candles
            WHERE interval = ? AND close_time_ms < ?
            ORDER BY close_time_ms DESC
            LIMIT ?
            """,
            (interval.value, _epoch_milliseconds(as_of), limit),
        ).fetchall()
        descending = tuple(self._row(interval, row) for row in rows)
        contiguous = [descending[0]]
        newer = descending[0]
        for older in descending[1:]:
            if older.close_time + timedelta(milliseconds=1) != newer.open_time:
                break
            contiguous.append(older)
            newer = older
        result = CandleSeries(tuple(reversed(contiguous)))
        self._series_cache[cache_key] = result
        return result

    def market_view(self, as_of: datetime, *, lookback: int = 200) -> HistoricalMarketView:
        now = ensure_utc(as_of)
        return HistoricalMarketView(
            now,
            tuple(
                self.series_at(interval, now, limit=lookback) for interval in _RESAMPLED_INTERVALS
            ),
        )

    def candidate_times(self, start: datetime, end: datetime) -> tuple[datetime, ...]:
        lower = _epoch_milliseconds(ensure_utc(start))
        upper = _epoch_milliseconds(ensure_utc(end))
        if upper <= lower:
            raise DomainValidationError("Historical candidate range is invalid")
        rows = self.connection.execute(
            """
            SELECT close_time_ms + 1
            FROM replay_candles
            WHERE interval = ? AND close_time_ms + 1 >= ? AND close_time_ms + 1 < ?
            ORDER BY open_time_ms
            """,
            (MarketInterval.FIFTEEN_MINUTES.value, lower, upper),
        ).fetchall()
        return tuple(_from_epoch_milliseconds(row[0]) for row in rows)

    @staticmethod
    def _row(interval: MarketInterval, row: tuple[object, ...]) -> Candle:
        return Candle(
            venue=MarketVenue.SPOT,
            interval=interval,
            open_time=_from_epoch_milliseconds(int(row[0])),
            close_time=_from_epoch_milliseconds(int(row[1])),
            open=Decimal(str(row[2])),
            high=Decimal(str(row[3])),
            low=Decimal(str(row[4])),
            close=Decimal(str(row[5])),
            volume=Decimal(str(row[6])),
            quote_volume=Decimal(str(row[7])),
            trade_count=int(row[8]),
            taker_buy_base_volume=Decimal(str(row[9])),
            taker_buy_quote_volume=Decimal(str(row[10])),
        )

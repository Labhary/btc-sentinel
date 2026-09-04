import hashlib
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting import (
    HistoricalDataError,
    HistoricalDatasetLoader,
    HistoricalMarketView,
    HistoricalReplayStore,
)
from btc_sentinel.backtesting.replay import _Bucket, _bucket_open
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle


def _kline(open_time: datetime, offset: int) -> str:
    opened = int(open_time.timestamp()) * 1_000
    price = Decimal(100 + offset)
    return ",".join(
        (
            str(opened),
            str(price),
            str(price + 2),
            str(price - 1),
            str(price + 1),
            str(10 + offset),
            str(opened + 59_999),
            str(1_000 + offset),
            str(offset + 1),
            "5",
            "500",
            "0",
        )
    )


def _archive(member: str, rows: list[str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, "\n".join(rows) + "\n")
    return output.getvalue()


class HistoricalReplayTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "archives").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, start: datetime, offsets: list[int]) -> Path:
        rows = [
            _kline(start + timedelta(minutes=index), offset) for index, offset in enumerate(offsets)
        ]
        content = _archive("BTCUSDT-1m-test.csv", rows)
        relative = "archives/BTCUSDT-1m-test.zip"
        (self.root / relative).write_bytes(content)
        end = start + timedelta(minutes=len(rows))

        def timestamp(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        record = {
            "path": relative,
            "source_url": (
                "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-test.zip"
            ),
            "sha256": hashlib.sha256(content).hexdigest(),
            "timestamp_unit": "milliseconds",
            "coverage_start": timestamp(start),
            "coverage_end": timestamp(end),
            "row_count": len(rows),
        }
        payload = {
            "schema_version": 1,
            "dataset_id": "binance-vision-btcusdt-replay-test-v1",
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1m",
            "coverage_start": timestamp(start),
            "coverage_end": timestamp(end),
            "source_origin": "https://data.binance.vision",
            "exhaustive_candidate_scan": True,
            "excluded_features": ["historical_order_book", "historical_liquidations"],
            "archives": [record],
        }
        path = self.root / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_import_resamples_only_complete_utc_buckets(self) -> None:
        start = datetime(2024, 1, 1, 0, 7, tzinfo=UTC)
        path = self._manifest(start, list(range(30)))
        loader = HistoricalDatasetLoader(maximum_loaded_candles=1)

        with HistoricalReplayStore(self.root / "replay.sqlite3") as store:
            summary = store.import_manifest(path, loader)
            series = store.series_at(
                MarketInterval.FIFTEEN_MINUTES,
                datetime(2024, 1, 1, 0, 30, tzinfo=UTC),
            )

        self.assertEqual(summary.one_minute_candles, 30)
        self.assertEqual(dict(summary.resampled_counts)[MarketInterval.FIFTEEN_MINUTES], 1)
        self.assertEqual(len(series.candles), 1)
        candle = series.latest
        self.assertEqual(candle.open_time, datetime(2024, 1, 1, 0, 15, tzinfo=UTC))
        self.assertEqual(candle.open, Decimal(108))
        self.assertEqual(candle.high, Decimal(124))
        self.assertEqual(candle.low, Decimal(107))
        self.assertEqual(candle.close, Decimal(123))
        self.assertEqual(candle.volume, sum(Decimal(10 + index) for index in range(8, 23)))
        self.assertEqual(candle.trade_count, sum(index + 1 for index in range(8, 23)))

    def test_point_in_time_queries_never_expose_a_not_yet_closed_candle(self) -> None:
        start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        path = self._manifest(start, list(range(30)))

        with HistoricalReplayStore(self.root / "replay.sqlite3") as store:
            store.import_manifest(path)
            first_close = datetime(2024, 1, 1, 0, 14, 59, 999_000, tzinfo=UTC)
            with self.assertRaisesRegex(DomainValidationError, "No completed"):
                store.series_at(MarketInterval.FIFTEEN_MINUTES, first_close)
            visible = store.series_at(
                MarketInterval.FIFTEEN_MINUTES,
                first_close + timedelta(milliseconds=1),
            )
            candidates = store.candidate_times(
                start,
                datetime(2024, 1, 1, 0, 31, tzinfo=UTC),
            )

        self.assertEqual(visible.latest.open_time, start)
        self.assertEqual(
            candidates,
            (
                datetime(2024, 1, 1, 0, 15, tzinfo=UTC),
                datetime(2024, 1, 1, 0, 30, tzinfo=UTC),
            ),
        )

    def test_calendar_buckets_use_monday_and_month_start_utc(self) -> None:
        value = datetime(2024, 2, 29, 23, 59, tzinfo=UTC)
        self.assertEqual(
            _bucket_open(value, MarketInterval.ONE_WEEK),
            datetime(2024, 2, 26, tzinfo=UTC),
        )
        self.assertEqual(
            _bucket_open(value, MarketInterval.ONE_MONTH),
            datetime(2024, 2, 1, tzinfo=UTC),
        )

        candle = Candle(
            venue=MarketVenue.SPOT,
            interval=MarketInterval.ONE_MINUTE,
            open_time=value,
            close_time=value + timedelta(minutes=1) - timedelta(milliseconds=1),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
            quote_volume=Decimal(100),
            trade_count=1,
            taker_buy_base_volume=Decimal("0.5"),
            taker_buy_quote_volume=Decimal(50),
        )
        self.assertFalse(_Bucket.start(candle, MarketInterval.ONE_MONTH).complete())

    def test_full_calendar_month_builds_every_required_analysis_interval(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        minutes = 31 * 24 * 60
        path = self._manifest(start, list(range(minutes)))

        with HistoricalReplayStore(self.root / "replay.sqlite3") as store:
            summary = store.import_manifest(path)
            view = store.market_view(datetime(2024, 2, 1, tzinfo=UTC), lookback=2)

        counts = dict(summary.resampled_counts)
        self.assertEqual(counts[MarketInterval.FIFTEEN_MINUTES], 2_976)
        self.assertEqual(counts[MarketInterval.ONE_HOUR], 744)
        self.assertEqual(counts[MarketInterval.FOUR_HOURS], 186)
        self.assertEqual(counts[MarketInterval.ONE_DAY], 31)
        self.assertEqual(counts[MarketInterval.ONE_WEEK], 4)
        self.assertEqual(counts[MarketInterval.ONE_MONTH], 1)
        self.assertEqual(
            tuple(series.interval for series in view.spot_series),
            (
                MarketInterval.FIFTEEN_MINUTES,
                MarketInterval.ONE_HOUR,
                MarketInterval.FOUR_HOURS,
                MarketInterval.ONE_DAY,
                MarketInterval.ONE_WEEK,
                MarketInterval.ONE_MONTH,
            ),
        )
        self.assertTrue(
            all(series.latest.close_time < view.captured_at for series in view.spot_series)
        )

    def test_import_is_atomic_and_store_cannot_be_reinitialized(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        valid_path = self._manifest(start, list(range(15)))
        database = self.root / "replay.sqlite3"
        with HistoricalReplayStore(database) as store:
            store.import_manifest(valid_path)
            with self.assertRaisesRegex(DomainValidationError, "already been initialized"):
                store.import_manifest(valid_path)

        broken_path = self._manifest(start, [0, 1])
        archive_path = self.root / "archives/BTCUSDT-1m-test.zip"
        broken = _archive(
            "BTCUSDT-1m-test.csv",
            [_kline(start, 0), _kline(start + timedelta(minutes=2), 1)],
        )
        archive_path.write_bytes(broken)
        payload = json.loads(broken_path.read_text(encoding="utf-8"))
        payload["archives"][0]["sha256"] = hashlib.sha256(broken).hexdigest()
        broken_path.write_text(json.dumps(payload), encoding="utf-8")

        rollback_database = self.root / "rollback.sqlite3"
        with HistoricalReplayStore(rollback_database) as store:
            with self.assertRaisesRegex(HistoricalDataError, "gapped or unordered"):
                store.import_manifest(broken_path)
            rows = store.connection.execute("SELECT COUNT(*) FROM replay_candles").fetchone()
            metadata = store.connection.execute("SELECT COUNT(*) FROM replay_metadata").fetchone()
        self.assertEqual(rows, (0,))
        self.assertEqual(metadata, (0,))

    def test_historical_view_rejects_an_unavailable_venue(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        path = self._manifest(start, list(range(15)))
        with HistoricalReplayStore(self.root / "replay.sqlite3") as store:
            store.import_manifest(path)
            series = store.series_at(
                MarketInterval.FIFTEEN_MINUTES,
                start + timedelta(minutes=15),
            )
        view = HistoricalMarketView(start + timedelta(minutes=15), (series,))
        self.assertIs(view.series_for(MarketVenue.SPOT, MarketInterval.FIFTEEN_MINUTES), series)
        with self.assertRaisesRegex(DomainValidationError, "FUTURES"):
            view.series_for(MarketVenue.FUTURES, MarketInterval.FIFTEEN_MINUTES)

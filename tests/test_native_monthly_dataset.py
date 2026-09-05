import hashlib
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting.archive_fetch import ArchiveDownload
from btc_sentinel.backtesting.dataset import HistoricalDataError, TimestampUnit
from btc_sentinel.backtesting.monthly_dataset import (
    BinanceVisionMonthlyBuilder,
    NativeMonthlyLoader,
)
from btc_sentinel.market_data.enums import MarketInterval


def _raw(value: datetime, unit: TimestampUnit) -> int:
    return int(value.timestamp()) * unit.scale + value.microsecond * unit.scale // 1_000_000


def _archive(start: datetime, unit: TimestampUnit, *, close_offset: int = 0) -> bytes:
    close = MarketInterval.ONE_MONTH.expected_close_time(start)
    row = ",".join(
        (
            str(_raw(start, unit)),
            "100",
            "120",
            "90",
            "110",
            "10",
            str(_raw(close, unit) + close_offset),
            "1000",
            "5",
            "6",
            "600",
            "0",
        )
    )
    output = io.BytesIO()
    member = f"BTCUSDT-1mo-{start:%Y-%m}.csv"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, row + "\n")
    return output.getvalue()


class FakeMonthlyDownloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def download(self, url: str, destination: Path) -> ArchiveDownload:
        self.urls.append(url)
        year_month = destination.name.removeprefix("BTCUSDT-1mo-")[:7]
        start = datetime.strptime(year_month, "%Y-%m").replace(tzinfo=UTC)
        unit = (
            TimestampUnit.MILLISECONDS
            if start < datetime(2025, 1, 1, tzinfo=UTC)
            else TimestampUnit.MICROSECONDS
        )
        content = _archive(start, unit)
        destination.write_bytes(content)
        return ArchiveDownload(hashlib.sha256(content).hexdigest(), len(content))


class NativeMonthlyDatasetTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builder_and_loader_validate_native_1mo_archives_across_unit_cutoff(self) -> None:
        downloader = FakeMonthlyDownloader()
        result = BinanceVisionMonthlyBuilder(downloader).build(
            datetime(2024, 12, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            self.root / "native",
            "btc-native-monthly-test-v1",
        )
        candles = []
        summary = NativeMonthlyLoader().visit(result.manifest_path, candles.append)

        self.assertEqual(result.archive_count, 2)
        self.assertEqual(summary.candle_count, 2)
        self.assertEqual([item.open_time.month for item in candles], [12, 1])
        self.assertTrue(all(item.interval is MarketInterval.ONE_MONTH for item in candles))
        self.assertEqual(
            downloader.urls[0],
            "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1mo/"
            "BTCUSDT-1mo-2024-12.zip",
        )
        payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["archives"][0]["timestamp_unit"], "milliseconds")
        self.assertEqual(payload["archives"][1]["timestamp_unit"], "microseconds")

    def test_loader_rejects_a_monthly_candle_with_an_inexact_close(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = _archive(start, TimestampUnit.MILLISECONDS, close_offset=-1)
        root = self.root / "bad"
        archives = root / "archives"
        archives.mkdir(parents=True)
        filename = "BTCUSDT-1mo-2024-01.zip"
        (archives / filename).write_bytes(content)
        payload = {
            "schema_version": 1,
            "dataset_id": "bad-native-month-v1",
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1mo",
            "coverage_start": "2024-01-01T00:00:00Z",
            "coverage_end": "2024-02-01T00:00:00Z",
            "source_origin": "https://data.binance.vision",
            "archives": [
                {
                    "path": f"archives/{filename}",
                    "source_url": (
                        "https://data.binance.vision/data/spot/monthly/klines/"
                        f"BTCUSDT/1mo/{filename}"
                    ),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "timestamp_unit": "milliseconds",
                    "coverage_start": "2024-01-01T00:00:00Z",
                    "coverage_end": "2024-02-01T00:00:00Z",
                }
            ],
        }
        manifest = root / "monthly-manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(HistoricalDataError, "timestamps contradict"):
            NativeMonthlyLoader().validate(manifest)

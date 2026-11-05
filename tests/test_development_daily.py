import hashlib
import io
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting.archive_fetch import ArchiveDownload, _validate_url
from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.backtesting.development_daily import DevelopmentDailyBuilder


class FakeDailyDownloader:
    def __init__(self, bad_checksum=False) -> None:
        self.urls = []
        self.payloads = {}
        self.bad_checksum = bad_checksum

    def download(self, url, destination):
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".CHECKSUM"):
            name = name.removesuffix(".CHECKSUM")
            digest = (
                "0" * 64 if self.bad_checksum else hashlib.sha256(self.payloads[name]).hexdigest()
            )
            content = f"{digest}  {name}\n".encode()
        else:
            day = datetime.strptime(name.removeprefix("BTCUSDT-1m-")[:10], "%Y-%m-%d").replace(
                tzinfo=UTC
            )
            opened = int(day.timestamp()) * 1000
            row = f"{opened},100,120,90,110,10,{opened + 59999},1000,5,6,600,0\n"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(name.removesuffix(".zip") + ".csv", row)
            content = buffer.getvalue()
            self.payloads[name] = content
        destination.write_bytes(content)
        return ArchiveDownload(hashlib.sha256(content).hexdigest(), len(content))


class DevelopmentDailyTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_full_year_uses_all_365_days_and_retains_gaps_without_filling(self):
        downloader = FakeDailyDownloader()
        built = DevelopmentDailyBuilder(downloader, workers=1).build(
            self.root / "daily", "daily-test"
        )
        manifest = json.loads(built.manifest_path.read_text())
        self.assertEqual(built.archive_count, 365)
        self.assertEqual(built.candle_count, 365)
        self.assertEqual(len(downloader.urls), 730)
        self.assertTrue(all("/daily/" in url and "2021-" in url for url in downloader.urls))
        self.assertEqual(manifest["coverage_end"], "2022-01-01T00:00:00Z")
        self.assertEqual(manifest["archives"][-1]["path"], "archives/BTCUSDT-1m-2021-12-31.zip")
        self.assertTrue(all(len(row["missing_intervals"]) == 1 for row in manifest["archives"]))
        self.assertTrue(
            (built.manifest_path.parent / "archives/BTCUSDT-1m-2021-01-01.zip.CHECKSUM").is_file()
        )
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            DevelopmentDailyBuilder(downloader).build(built.manifest_path.parent, "daily-test")

    def test_publisher_checksum_failure_prevents_final_manifest(self):
        output = self.root / "bad"
        with self.assertRaisesRegex(HistoricalDataError, "publisher checksum"):
            DevelopmentDailyBuilder(FakeDailyDownloader(True), workers=1).build(
                output, "bad-checksum"
            )
        self.assertFalse((output / "manifest.json").exists())

    def test_outside_development_days_are_rejected_before_network(self):
        downloader = FakeDailyDownloader()
        builder = DevelopmentDailyBuilder(downloader)
        for day in (
            datetime(2020, 12, 31, tzinfo=UTC),
            datetime(2022, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        ):
            with self.subTest(day=day), self.assertRaisesRegex(HistoricalDataError, "frozen 2021"):
                builder._acquire_day(day, self.root)
        self.assertEqual(downloader.urls, [])

    def test_daily_url_policy_cannot_expand_other_intervals_or_hosts(self):
        url = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2021-01-11.zip.CHECKSUM"
        _validate_url(url, "1m", "daily")
        for other in (url.replace("data.binance.vision", "example.com"), url + "?x=1"):
            with self.assertRaises(HistoricalDataError):
                _validate_url(other, "1m", "daily")
        with self.assertRaises(HistoricalDataError):
            _validate_url(url, "1m")
        with self.assertRaises(HistoricalDataError):
            _validate_url(url, "1w", "daily")
        for workers in (0, 5):
            with self.assertRaises(ValueError):
                DevelopmentDailyBuilder(workers=workers)

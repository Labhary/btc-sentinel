import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from btc_sentinel.backtesting.archive_fetch import (
    ArchiveDownload,
    BinanceVisionArchiveBuilder,
    HistoricalDataError,
    UrllibArchiveDownloader,
)
from btc_sentinel.backtesting.archive_job import _month


class FakeDownloader:
    def __init__(self, fail: bool = False) -> None:
        self.urls: list[str] = []
        self.fail = fail

    def download(self, url: str, destination: Path) -> ArchiveDownload:
        self.urls.append(url)
        if self.fail:
            raise HistoricalDataError("synthetic download failure")
        content = f"archive:{url}".encode()
        destination.write_bytes(content)
        return ArchiveDownload(hashlib.sha256(content).hexdigest(), len(content))


class FakeLoader:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def validate(self, path: Path):
        self.payload = json.loads(path.read_text(encoding="utf-8"))
        return SimpleNamespace(
            candle_count=sum(record["row_count"] for record in self.payload["archives"])
        )


class FakeResponse:
    status = 200

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def read(self, _size: int) -> bytes:
        self.read_count += 1
        return self.content if self.read_count == 1 else b""


class FakeOpener:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def open(self, request, timeout):
        return FakeResponse(self.content)


class HistoricalArchiveFetchTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_monthly_urls_and_manifest_across_timestamp_cutoff(self) -> None:
        downloader = FakeDownloader()
        loader = FakeLoader()
        output = self.root / "dataset"
        result = BinanceVisionArchiveBuilder(downloader, loader).build(
            datetime(2024, 12, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            output,
            "btc-history-test-v1",
        )

        self.assertEqual(result.archive_count, 2)
        self.assertTrue(result.manifest_path.is_file())
        self.assertFalse((output / "manifest.json.part").exists())
        self.assertEqual(
            downloader.urls,
            [
                "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/"
                "BTCUSDT-1m-2024-12.zip",
                "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/"
                "BTCUSDT-1m-2025-01.zip",
            ],
        )
        records = loader.payload["archives"]
        self.assertEqual(records[0]["timestamp_unit"], "milliseconds")
        self.assertEqual(records[1]["timestamp_unit"], "microseconds")
        self.assertEqual(records[0]["row_count"], 31 * 24 * 60)
        self.assertEqual(records[1]["row_count"], 31 * 24 * 60)

    def test_refuses_unaligned_empty_large_or_existing_ranges(self) -> None:
        builder = BinanceVisionArchiveBuilder(FakeDownloader(), FakeLoader(), maximum_months=2)
        cases = (
            (
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                "month boundary",
            ),
            (
                datetime(2024, 2, 1, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                "empty",
            ),
            (
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 4, 1, tzinfo=UTC),
                "month limit",
            ),
        )
        for index, (start, end, message) in enumerate(cases):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(HistoricalDataError, message),
            ):
                builder.build(start, end, self.root / f"output-{index}", "test-v1")

        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            builder.build(
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                existing,
                "test-v1",
            )

    def test_failed_download_never_publishes_a_manifest(self) -> None:
        output = self.root / "failed"
        with self.assertRaisesRegex(HistoricalDataError, "synthetic"):
            BinanceVisionArchiveBuilder(FakeDownloader(True), FakeLoader()).build(
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                output,
                "test-v1",
            )
        self.assertFalse((output / "manifest.json").exists())

    def test_streaming_downloader_rejects_host_and_removes_oversized_partial(self) -> None:
        downloader = UrllibArchiveDownloader(maximum_bytes=3, maximum_attempts=1)
        destination = self.root / "archive.part"
        with self.assertRaisesRegex(HistoricalDataError, "fixed Binance"):
            downloader.download("https://example.com/archive.zip", destination)

        downloader.opener = FakeOpener(b"four")
        with self.assertRaisesRegex(HistoricalDataError, "size limit"):
            downloader.download(
                "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/"
                "BTCUSDT-1m-2024-01.zip",
                destination,
            )
        self.assertFalse(destination.exists())

    def test_cli_month_parser_is_exact(self) -> None:
        self.assertEqual(_month("2024-01"), datetime(2024, 1, 1, tzinfo=UTC))
        with self.assertRaises(argparse.ArgumentTypeError):
            _month("2024-1")

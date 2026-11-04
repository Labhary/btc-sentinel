import hashlib
import io
import json
import tempfile
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from btc_sentinel.backtesting.archive_fetch import ArchiveDownload, _validate_url
from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.backtesting.replay import HistoricalReplayStore
from btc_sentinel.backtesting.weekly_dataset import (
    BinanceVisionWeeklyBuilder,
    NativeWeeklyLoader,
    _weekly_opens,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval


def _row(opened: datetime) -> list[str]:
    return [
        str(int(opened.timestamp()) * 1000),
        "100",
        "120",
        "90",
        "110",
        "10",
        str(int((opened + timedelta(days=7)).timestamp()) * 1000 - 1),
        "1000",
        "5",
        "6",
        "600",
        "0",
    ]


def _zip(name: str, rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            name.removesuffix(".zip") + ".csv", "\n".join(",".join(row) for row in rows) + "\n"
        )
    return buffer.getvalue()


class FakeWeeklyDownloader:
    def __init__(self) -> None:
        self.urls = []
        self.payloads = {}

    def download(self, url: str, destination: Path) -> ArchiveDownload:
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".CHECKSUM"):
            archive_name = name.removesuffix(".CHECKSUM")
            digest = hashlib.sha256(self.payloads[archive_name]).hexdigest()
            content = f"{digest}  {archive_name}\n".encode()
        else:
            month = datetime.strptime(name.removeprefix("BTCUSDT-1w-")[:7], "%Y-%m").replace(
                tzinfo=UTC
            )
            content = _zip(name, [_row(opened) for opened in _weekly_opens(month)])
            self.payloads[name] = content
        destination.write_bytes(content)
        return ArchiveDownload(hashlib.sha256(content).hexdigest(), len(content))


class FakeMinuteLoader:
    def visit(self, _path, visitor):
        from btc_sentinel.backtesting.weekly_dataset import _candle

        opened = datetime(2021, 1, 4, tzinfo=UTC)
        template = _candle(_row(opened), opened)
        for minute in range(15):
            start = opened + timedelta(minutes=minute)
            visitor(
                replace(
                    template,
                    interval=MarketInterval.ONE_MINUTE,
                    open_time=start,
                    close_time=start + timedelta(minutes=1, milliseconds=-1),
                )
            )
        return SimpleNamespace(
            manifest=SimpleNamespace(
                dataset_id="minute-fixture",
                coverage_start=opened,
                coverage_end=opened + timedelta(minutes=15),
            ),
            manifest_sha256="a" * 64,
        )


class NativeWeeklyDatasetTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.downloader = FakeWeeklyDownloader()
        self.path = BinanceVisionWeeklyBuilder(self.downloader).build(
            datetime(2020, 12, 1, tzinfo=UTC),
            datetime(2021, 2, 1, tzinfo=UTC),
            self.root / "weekly",
            "native-weekly-fixture",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _rewrite_archive(self, transform) -> None:
        payload = json.loads(self.path.read_text())
        spec = payload["archives"][0]
        archive = self.path.parent / spec["path"]
        name = archive.name
        month = datetime(2020, 12, 1, tzinfo=UTC)
        rows = [_row(opened) for opened in _weekly_opens(month)]
        content = _zip(name, transform(rows))
        archive.write_bytes(content)
        spec["sha256"] = hashlib.sha256(content).hexdigest()
        Path(f"{archive}.CHECKSUM").write_text(f"{spec['sha256']}  {name}\n")
        self.path.write_text(json.dumps(payload))

    def test_native_weekly_builder_retains_official_checksums_and_exact_weeks(self) -> None:
        candles = []
        summary = NativeWeeklyLoader().visit(self.path, candles.append)
        self.assertEqual(summary.candle_count, 8)
        self.assertEqual(summary.coverage_start, datetime(2020, 12, 7, tzinfo=UTC))
        self.assertEqual(summary.coverage_end, datetime(2021, 2, 1, tzinfo=UTC))
        self.assertTrue(all(item.interval is MarketInterval.ONE_WEEK for item in candles))
        self.assertEqual(len(self.downloader.urls), 4)
        self.assertTrue(self.downloader.urls[1].endswith(".zip.CHECKSUM"))

    def test_refuses_sealed_period_and_crossing_december_archive_before_download(self) -> None:
        for start, end in (("2021-12", "2022-01"), ("2022-01", "2022-02"), ("2026-01", "2026-02")):
            with (
                self.subTest(start=start),
                self.assertRaisesRegex(HistoricalDataError, "frozen development"),
            ):
                BinanceVisionWeeklyBuilder(self.downloader).build(
                    datetime.strptime(start, "%Y-%m").replace(tzinfo=UTC),
                    datetime.strptime(end, "%Y-%m").replace(tzinfo=UTC),
                    self.root / "forbidden",
                    "forbidden",
                )
        self.assertEqual(len(self.downloader.urls), 4)

    def test_rejects_missing_week_even_when_all_checksums_match(self) -> None:
        self._rewrite_archive(lambda rows: rows[:-1])
        with self.assertRaisesRegex(HistoricalDataError, "every expected week"):
            NativeWeeklyLoader().validate(self.path)

    def test_rejects_duplicate_or_unordered_week_even_when_checksums_match(self) -> None:
        self._rewrite_archive(lambda rows: [rows[1], rows[0], *rows[2:]])
        with self.assertRaisesRegex(HistoricalDataError, "timestamps contradict"):
            NativeWeeklyLoader().validate(self.path)

    def test_rejects_inexact_close_even_when_checksums_match(self) -> None:
        def change(rows):
            rows[0][6] = str(int(rows[0][6]) - 1)
            return rows

        self._rewrite_archive(change)
        with self.assertRaisesRegex(HistoricalDataError, "timestamps contradict"):
            NativeWeeklyLoader().validate(self.path)

    def test_rejects_corrupted_archive(self) -> None:
        archive = self.path.parent / "archives/BTCUSDT-1w-2020-12.zip"
        archive.write_bytes(b"corrupted")
        with self.assertRaisesRegex(HistoricalDataError, "checksum"):
            NativeWeeklyLoader().validate(self.path)

    def test_rejects_wrong_publisher_checksum(self) -> None:
        checksum = self.path.parent / "archives/BTCUSDT-1w-2020-12.zip.CHECKSUM"
        checksum.write_text("0" * 64 + "  BTCUSDT-1w-2020-12.zip\n")
        with self.assertRaisesRegex(HistoricalDataError, "publisher checksum"):
            NativeWeeklyLoader().validate(self.path)

    def test_rejects_external_or_traversal_manifest_paths(self) -> None:
        original = self.path.read_text()
        for key, value in (
            ("path", "../BTCUSDT-1w-2020-12.zip"),
            ("source_url", "https://example.com/BTCUSDT-1w-2020-12.zip"),
        ):
            payload = json.loads(original)
            payload["archives"][0][key] = value
            self.path.write_text(json.dumps(payload))
            with self.subTest(key=key), self.assertRaisesRegex(HistoricalDataError, "catalog"):
                NativeWeeklyLoader().validate(self.path)

    def test_download_url_allowlist_remains_interval_specific(self) -> None:
        url = self.downloader.urls[1]
        _validate_url(url, "1w")
        with self.assertRaises(HistoricalDataError):
            _validate_url(url, "1m")
        with self.assertRaises(HistoricalDataError):
            _validate_url(url.replace("data.binance.vision", "example.com"), "1w")

    def test_does_not_overwrite_dataset(self) -> None:
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            BinanceVisionWeeklyBuilder(self.downloader).build(
                datetime(2020, 12, 1, tzinfo=UTC),
                datetime(2021, 2, 1, tzinfo=UTC),
                self.path.parent,
                "native-weekly-fixture",
            )

    def test_import_preserves_minutes_candidates_and_future_visibility(self) -> None:
        with HistoricalReplayStore(self.root / "replay.sqlite3") as store:
            store.import_manifest(Path("unused"), FakeMinuteLoader())
            start, end = store.coverage()
            before = store.connection.execute(
                "SELECT * FROM replay_candles WHERE interval = '1m'"
            ).fetchall()
            candidates = store.candidate_times(start, end)
            summary = store.import_native_weekly_manifest(self.path)
            after = store.connection.execute(
                "SELECT * FROM replay_candles WHERE interval = '1m'"
            ).fetchall()
            self.assertEqual(before, after)
            self.assertEqual(candidates, store.candidate_times(start, end))
            self.assertEqual(store.coverage(), (start, end))
            self.assertEqual(summary.overlap_checked_count, 0)
            visible = store.series_at(MarketInterval.ONE_WEEK, start)
            self.assertEqual(visible.latest.open_time, datetime(2020, 12, 28, tzinfo=UTC))
            later = store.series_at(MarketInterval.ONE_WEEK, datetime(2021, 1, 11, tzinfo=UTC))
            self.assertEqual(later.latest.open_time, start)
            with self.assertRaisesRegex(DomainValidationError, "already initialized"):
                store.import_native_weekly_manifest(self.path)

    def test_import_checks_overlaps_and_rolls_back_conflicting_source(self) -> None:
        candles = []
        NativeWeeklyLoader().visit(self.path, candles.append)
        overlapping = next(c for c in candles if c.open_time == datetime(2021, 1, 4, tzinfo=UTC))
        for conflict in (False, True):
            with (
                self.subTest(conflict=conflict),
                HistoricalReplayStore(self.root / f"{conflict}.sqlite3") as store,
            ):
                store.import_manifest(Path("unused"), FakeMinuteLoader())
                store._insert(replace(overlapping, trade_count=99) if conflict else overlapping)
                store.connection.commit()
                before = store.connection.execute(
                    "SELECT * FROM replay_candles ORDER BY interval, open_time_ms"
                ).fetchall()
                if conflict:
                    with self.assertRaisesRegex(DomainValidationError, "disagrees"):
                        store.import_native_weekly_manifest(self.path)
                    self.assertEqual(
                        before,
                        store.connection.execute(
                            "SELECT * FROM replay_candles ORDER BY interval, open_time_ms"
                        ).fetchall(),
                    )
                    self.assertIsNone(
                        store.connection.execute(
                            "SELECT value FROM replay_metadata WHERE key='native_weekly_dataset_id'"
                        ).fetchone()
                    )
                else:
                    summary = store.import_native_weekly_manifest(self.path)
                    self.assertEqual(summary.overlap_checked_count, 1)

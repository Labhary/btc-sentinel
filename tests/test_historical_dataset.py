import hashlib
import io
import json
import tempfile
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting import HistoricalDataError, HistoricalDatasetLoader
from btc_sentinel.backtesting.job import main


def raw_timestamp(value: datetime, unit: str) -> int:
    scale = 1_000 if unit == "milliseconds" else 1_000_000
    return int(value.timestamp()) * scale


def kline(open_time: datetime, unit: str) -> str:
    scale = 1_000 if unit == "milliseconds" else 1_000_000
    opened = raw_timestamp(open_time, unit)
    closed = opened + 60 * scale - 1
    return ",".join(
        (
            str(opened),
            "42000.10",
            "42010.20",
            "41990.00",
            "42005.50",
            "12.5",
            str(closed),
            "525000.00",
            "42",
            "7.5",
            "315000.00",
            "0",
        )
    )


def archive_bytes(member: str, rows: list[str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, "\n".join(rows) + "\n")
    return output.getvalue()


def archive_record(path: str, content: bytes, unit: str, start: datetime, rows: int) -> dict:
    filename = Path(path).name
    return {
        "path": path,
        "source_url": ("https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/" + filename),
        "sha256": hashlib.sha256(content).hexdigest(),
        "timestamp_unit": unit,
        "coverage_start": start.isoformat().replace("+00:00", "Z"),
        "coverage_end": (start + timedelta(minutes=rows)).isoformat().replace("+00:00", "Z"),
        "row_count": rows,
    }


def manifest(records: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "dataset_id": "binance-vision-btcusdt-test-v1",
        "symbol": "BTCUSDT",
        "venue": "SPOT",
        "interval": "1m",
        "coverage_start": records[0]["coverage_start"],
        "coverage_end": records[-1]["coverage_end"],
        "source_origin": "https://data.binance.vision",
        "exhaustive_candidate_scan": True,
        "excluded_features": ["historical_order_book", "historical_liquidations"],
        "archives": records,
    }


class HistoricalDatasetTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "archives").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_archive(self, name: str, content: bytes) -> str:
        relative = f"archives/{name}.zip"
        (self.root / relative).write_bytes(content)
        return relative

    def write_manifest(self, payload: dict) -> Path:
        path = self.root / "manifest.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def test_loads_continuous_millisecond_and_microsecond_archives(self) -> None:
        first = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
        second = first + timedelta(minutes=1)
        old = archive_bytes("BTCUSDT-1m-old.csv", [kline(first, "milliseconds")])
        new = archive_bytes("BTCUSDT-1m-new.csv", [kline(second, "microseconds")])
        records = [
            archive_record(
                self.write_archive("BTCUSDT-1m-old", old),
                old,
                "milliseconds",
                first,
                1,
            ),
            archive_record(
                self.write_archive("BTCUSDT-1m-new", new),
                new,
                "microseconds",
                second,
                1,
            ),
        ]

        dataset = HistoricalDatasetLoader().load(self.write_manifest(manifest(records)))

        self.assertEqual(dataset.candle_count, 2)
        self.assertEqual(dataset.candles.candles[0].open_time, first)
        self.assertEqual(dataset.candles.candles[1].open_time, second)
        self.assertEqual(dataset.candles.candles[1].close_time.microsecond, 999000)
        self.assertEqual(len(dataset.manifest_sha256), 64)

    def test_checksum_is_verified_before_archive_is_trusted(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = archive_bytes("BTCUSDT-1m-day.csv", [kline(start, "milliseconds")])
        record = archive_record(
            self.write_archive("BTCUSDT-1m-day", content), content, "milliseconds", start, 1
        )
        record["sha256"] = "0" * 64
        with self.assertRaisesRegex(HistoricalDataError, "checksum mismatch"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

    def test_manifest_rejects_unknown_duplicate_and_unsafe_fields(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = archive_bytes("BTCUSDT-1m-day.csv", [kline(start, "milliseconds")])
        record = archive_record("../escape.zip", content, "milliseconds", start, 1)
        with self.assertRaisesRegex(HistoricalDataError, "safe relative"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

        record = archive_record("archives/BTCUSDT-1m-day.zip", content, "milliseconds", start, 1)
        record["source_url"] = "https://example.com/BTCUSDT-1m-day.zip"
        with self.assertRaisesRegex(HistoricalDataError, "fixed Binance path"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

        record = archive_record("archives/BTCUSDT-1m-day.zip", content, "microseconds", start, 1)
        with self.assertRaisesRegex(HistoricalDataError, "timestamp unit contradicts"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

        record = archive_record("archives/BTCUSDT-1m-day.zip", content, "milliseconds", start, 1)
        payload = manifest([record])
        payload["unexpected"] = True
        with self.assertRaisesRegex(HistoricalDataError, r"unknown=\['unexpected'\]"):
            HistoricalDatasetLoader().load(self.write_manifest(payload))

        duplicate = b'{"schema_version":1,"schema_version":1}'
        (self.root / "manifest.json").write_bytes(duplicate)
        with self.assertRaisesRegex(HistoricalDataError, "Duplicate JSON field"):
            HistoricalDatasetLoader().load(self.root / "manifest.json")

    def test_zip_member_and_row_shape_fail_closed(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        wrong_member = archive_bytes("other.csv", [kline(start, "milliseconds")])
        record = archive_record(
            self.write_archive("BTCUSDT-1m-day", wrong_member),
            wrong_member,
            "milliseconds",
            start,
            1,
        )
        with self.assertRaisesRegex(HistoricalDataError, "one expected CSV"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

        malformed = archive_bytes("BTCUSDT-1m-day.csv", ["1,2,3"])
        (self.root / record["path"]).write_bytes(malformed)
        record["sha256"] = hashlib.sha256(malformed).hexdigest()
        with self.assertRaisesRegex(HistoricalDataError, "exactly 12"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

    def test_actual_candle_gap_is_rejected_even_when_manifest_claims_continuity(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = archive_bytes(
            "BTCUSDT-1m-two.csv",
            [kline(start, "milliseconds"), kline(start + timedelta(minutes=2), "milliseconds")],
        )
        record = archive_record(
            self.write_archive("BTCUSDT-1m-two", content), content, "milliseconds", start, 2
        )
        with self.assertRaisesRegex(HistoricalDataError, "gapped or unordered"):
            HistoricalDatasetLoader().load(self.write_manifest(manifest([record])))

    def test_cli_reports_validation_without_claiming_performance(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = archive_bytes("BTCUSDT-1m-day.csv", [kline(start, "milliseconds")])
        record = archive_record(
            self.write_archive("BTCUSDT-1m-day", content),
            content,
            "milliseconds",
            start,
            1,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([str(self.write_manifest(manifest([record])))])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["performance_verdict"], "NOT_RUN")
        self.assertEqual(payload["candle_count"], 1)

        error = io.StringIO()
        with redirect_stderr(error):
            result = main([str(self.root / "missing.json")])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(error.getvalue())["error_name"], "FileNotFoundError")

    def test_streaming_validation_does_not_require_loading_every_candle(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        content = archive_bytes(
            "BTCUSDT-1m-two.csv",
            [kline(start, "milliseconds"), kline(start + timedelta(minutes=1), "milliseconds")],
        )
        record = archive_record(
            self.write_archive("BTCUSDT-1m-two", content), content, "milliseconds", start, 2
        )
        path = self.write_manifest(manifest([record]))
        loader = HistoricalDatasetLoader(maximum_loaded_candles=1)

        self.assertEqual(loader.validate(path).candle_count, 2)
        with self.assertRaisesRegex(HistoricalDataError, "in-memory load limit"):
            loader.load(path)

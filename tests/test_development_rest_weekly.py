import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.backtesting.development_rest_weekly import (
    END,
    SOURCE_URL,
    START,
    DevelopmentRestWeeklyLoader,
    build,
    parse_rows,
)


def rows():
    result = []
    for index in range(100):
        opened = int((START + timedelta(days=7 * index)).timestamp()) * 1000
        result.append(
            [
                opened,
                "100",
                "120",
                "90",
                "110",
                "10",
                opened + 7 * 24 * 60 * 60 * 1000 - 1,
                "1000",
                5,
                "6",
                "600",
                "0",
            ]
        )
    return result


class DevelopmentRestWeeklyTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_retains_two_identical_bounded_responses_and_exact_weeks(self):
        raw = json.dumps(rows()).encode()
        calls = []

        def fetch():
            calls.append(True)
            return raw

        manifest = build(self.root / "weekly", "rest-fixture", fetch)
        candles = []
        summary = DevelopmentRestWeeklyLoader().visit(manifest, candles.append)
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary.candle_count, 100)
        self.assertEqual(summary.coverage_start, START)
        self.assertEqual(summary.coverage_end, END)
        self.assertTrue(all(c.close_time < END for c in candles))
        self.assertEqual(json.loads(manifest.read_text())["source_url"], SOURCE_URL)
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            build(manifest.parent, "rest-fixture", fetch)
        self.assertEqual(len(calls), 2)

    def test_different_repeat_is_rejected_before_publishing(self):
        first = rows()
        second = rows()
        second[0][4] = "111"
        responses = iter([json.dumps(first).encode(), json.dumps(second).encode()])
        with self.assertRaisesRegex(HistoricalDataError, "changed between reads"):
            build(self.root / "changed", "changed", lambda: next(responses))
        self.assertFalse((self.root / "changed").exists())

    def test_rejects_missing_duplicate_shifted_and_future_rows(self):
        for kind in ("missing", "duplicate", "shifted", "future", "bool", "float", "nan"):
            value = rows()
            if kind == "missing":
                value.pop()
            elif kind == "duplicate":
                value[1] = value[0]
            elif kind == "shifted":
                value[0][0] += 60_000
            elif kind == "future":
                value[-1][0] = 1641168000000
            elif kind == "bool":
                value[0][8] = True
            elif kind == "float":
                value[0][1] = 100.0
            else:
                value[0][1] = "NaN"
            with self.subTest(kind=kind), self.assertRaises(HistoricalDataError):
                parse_rows(json.dumps(value).encode())

    def test_loader_rejects_unbounded_origin_or_future_manifest(self):
        manifest = build(self.root / "weekly", "rest-fixture", lambda: json.dumps(rows()).encode())
        original = manifest.read_text()
        for field, value in (
            (
                "source_url",
                "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1w",
            ),
            ("coverage_end", "2026-01-01T00:00:00Z"),
            ("raw_path", "../weekly.json"),
        ):
            payload = json.loads(original)
            payload[field] = value
            manifest.write_text(json.dumps(payload))
            with self.subTest(field=field), self.assertRaises(HistoricalDataError):
                DevelopmentRestWeeklyLoader().visit(manifest, lambda _: None)

    def test_loader_rejects_tampered_response(self):
        manifest = build(self.root / "weekly", "rest-fixture", lambda: json.dumps(rows()).encode())
        (manifest.parent / "weekly.json").write_bytes(b"[]")
        with self.assertRaisesRegex(HistoricalDataError, "checksum"):
            DevelopmentRestWeeklyLoader().visit(manifest, lambda _: None)

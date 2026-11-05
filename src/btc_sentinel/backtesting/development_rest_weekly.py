"""Retained public REST weekly snapshot for a fixed pre-2022 development experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, build_opener

from btc_sentinel.backtesting.archive_fetch import _NoRedirect
from btc_sentinel.backtesting.dataset import HistoricalDataError, _fields, _strict_object, _utc
from btc_sentinel.backtesting.weekly_dataset import NativeWeeklySummary, _candle
from btc_sentinel.market_data.models import Candle

START = datetime(2020, 1, 6, tzinfo=UTC)
END = datetime(2021, 12, 6, tzinfo=UTC)
SOURCE_URL = (
    "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1w"
    "&startTime=1578268800000&endTime=1638748799999&limit=100"
)
_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_id",
    "source_url",
    "coverage_start",
    "coverage_end",
    "raw_path",
    "raw_sha256",
    "repeat_path",
    "repeat_sha256",
    "retrieved_at",
}


def parse_rows(raw: bytes) -> tuple[Candle, ...]:
    if not 0 < len(raw) <= 1_000_000:
        raise HistoricalDataError("Development REST weekly response size is invalid")
    try:
        rows = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalDataError("Development REST weekly response is not UTF-8 JSON") from exc
    if not isinstance(rows, list) or len(rows) != 100:
        raise HistoricalDataError("Development REST weekly response must contain exactly 100 rows")
    candles = []
    for index, row in enumerate(rows):
        if (
            not isinstance(row, list)
            or len(row) != 12
            or any(type(row[column]) is not int for column in (0, 6, 8))
            or any(type(row[column]) is not str for column in (1, 2, 3, 4, 5, 7, 9, 10, 11))
        ):
            raise HistoricalDataError("Development REST weekly row types are invalid")
        opened = START + timedelta(days=7 * index)
        candles.append(_candle([str(value) for value in row], opened))
    if candles[-1].close_time + timedelta(milliseconds=1) != END:
        raise HistoricalDataError("Development REST weekly coverage is invalid")
    return tuple(candles)


def _fetch() -> bytes:
    opener = build_opener(_NoRedirect())
    request = Request(SOURCE_URL, method="GET", headers={"Accept": "application/json"})
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != SOURCE_URL:
            raise HistoricalDataError("Development REST weekly response origin/status is invalid")
        if response.headers.get_content_type() != "application/json":
            raise HistoricalDataError("Development REST weekly response must be JSON")
        raw = response.read(1_000_001)
    parse_rows(raw)
    return raw


class DevelopmentRestWeeklyLoader:
    def visit(self, path: Path, visitor: Callable[[Candle], None]) -> NativeWeeklySummary:
        if path.stat().st_size > 10_000:
            raise HistoricalDataError("Development REST weekly manifest is too large")
        raw_manifest = path.read_bytes()
        try:
            manifest = json.loads(raw_manifest, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalDataError("Development REST weekly manifest is invalid JSON") from exc
        if not isinstance(manifest, dict):
            raise HistoricalDataError("Development REST weekly manifest must be an object")
        _fields(manifest, _MANIFEST_FIELDS, "development REST weekly manifest")
        identity = manifest["dataset_id"]
        if (
            type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1
            or not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identity)
            or manifest["source_url"] != SOURCE_URL
            or _utc(manifest["coverage_start"], "coverage_start") != START
            or _utc(manifest["coverage_end"], "coverage_end") != END
            or manifest["raw_path"] != "weekly.json"
            or manifest["repeat_path"] != "weekly-repeat.json"
        ):
            raise HistoricalDataError("Development REST weekly manifest identity/range is invalid")
        retrieved = _utc(manifest["retrieved_at"], "retrieved_at")
        if retrieved < END:
            raise HistoricalDataError("Development REST weekly retrieval predates completed input")
        root = path.resolve().parent
        responses = []
        for field, hash_field in (("raw_path", "raw_sha256"), ("repeat_path", "repeat_sha256")):
            source = (root / manifest[field]).resolve()
            if (
                not source.is_relative_to(root)
                or not source.is_file()
                or source.stat().st_size > 1_000_000
            ):
                raise HistoricalDataError("Development REST weekly response path/size is invalid")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != manifest[hash_field]:
                raise HistoricalDataError("Development REST weekly response checksum is invalid")
            responses.append(content)
        if responses[0] != responses[1]:
            raise HistoricalDataError("Development REST weekly responses changed between reads")
        candles = parse_rows(responses[0])
        for candle in candles:
            visitor(candle)
        return NativeWeeklySummary(
            identity, hashlib.sha256(raw_manifest).hexdigest(), START, END, len(candles)
        )


def build(output: Path, dataset_id: str, fetcher: Callable[[], bytes] = _fetch) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", dataset_id):
        raise HistoricalDataError("Development REST weekly dataset identity is invalid")
    if output.exists():
        raise HistoricalDataError("Development REST weekly output already exists")
    first = fetcher()
    second = fetcher()
    parse_rows(first)
    parse_rows(second)
    if first != second:
        raise HistoricalDataError("Development REST weekly responses changed between reads")
    output.mkdir(parents=True)
    (output / "weekly.json").write_bytes(first)
    (output / "weekly-repeat.json").write_bytes(second)
    manifest = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "source_url": SOURCE_URL,
        "coverage_start": START.isoformat(),
        "coverage_end": END.isoformat(),
        "raw_path": "weekly.json",
        "raw_sha256": hashlib.sha256(first).hexdigest(),
        "repeat_path": "weekly-repeat.json",
        "repeat_sha256": hashlib.sha256(second).hexdigest(),
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    partial = output / "weekly-rest-manifest.json.part"
    partial.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    DevelopmentRestWeeklyLoader().visit(partial, lambda _candle: None)
    final = output / "weekly-rest-manifest.json"
    partial.rename(final)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retain exactly 100 pre-2022 public REST weeks")
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build(args.output, args.dataset_id)
    except Exception as exc:
        print(
            json.dumps({"event": "development_rest_weekly_failed", "error": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "event": "development_rest_weekly_retained",
                "manifest": str(manifest),
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

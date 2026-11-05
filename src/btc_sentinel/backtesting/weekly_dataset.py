"""Strict native weekly inputs for the frozen pre-2022 development audit only."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Protocol

from btc_sentinel.backtesting.archive_fetch import (
    ArchiveDownloader,
    UrllibArchiveDownloader,
    _month_bound,
    _next_month,
    _validate_url,
)
from btc_sentinel.backtesting.dataset import (
    HistoricalDataError,
    TimestampUnit,
    _decimal,
    _epoch,
    _fields,
    _file_sha256,
    _strict_object,
    _utc,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import Candle

_ORIGIN = "https://data.binance.vision"
_PREFIX = "/data/spot/monthly/klines/BTCUSDT/1w/"
_FIRST_MONTH = datetime(2020, 1, 1, tzinfo=UTC)
_LAST_MONTH_END = datetime(2021, 12, 1, tzinfo=UTC)
_TOP_FIELDS = {
    "schema_version",
    "dataset_id",
    "symbol",
    "venue",
    "interval",
    "source_origin",
    "archive_start",
    "archive_end",
    "archives",
}
_ARCHIVE_FIELDS = {"path", "source_url", "sha256", "month"}


def _range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    first = _month_bound(start, "archive_start")
    stop = _month_bound(end, "archive_end")
    if not _FIRST_MONTH <= first < stop <= _LAST_MONTH_END:
        raise HistoricalDataError("Native weekly range is outside the frozen development bounds")
    return first, stop


def _weekly_opens(month: datetime) -> tuple[datetime, ...]:
    first = month + timedelta(days=(-month.weekday()) % 7)
    result = []
    while first < _next_month(month):
        result.append(first)
        first += timedelta(days=7)
    return tuple(result)


def _candle(row: list[str], expected_open: datetime) -> Candle:
    if len(row) != 12:
        raise HistoricalDataError("Native weekly kline row must contain 12 fields")
    try:
        raw_open, raw_close, trades = int(row[0]), int(row[6]), int(row[8])
    except ValueError as exc:
        raise HistoricalDataError("Native weekly timestamps and trades must be integers") from exc
    close = MarketInterval.ONE_WEEK.expected_close_time(expected_open)
    if raw_open != int(expected_open.timestamp()) * 1000 or raw_close != (
        int(close.timestamp()) * 1000 + 999
    ):
        raise HistoricalDataError("Native weekly candle timestamps contradict its expected week")
    if trades < 0:
        raise HistoricalDataError("Native weekly trades cannot be negative")
    _decimal(row[11], "ignore", nonnegative=True)
    try:
        return Candle(
            venue=MarketVenue.SPOT,
            interval=MarketInterval.ONE_WEEK,
            open_time=_epoch(raw_open, TimestampUnit.MILLISECONDS),
            close_time=close,
            open=_decimal(row[1], "open"),
            high=_decimal(row[2], "high"),
            low=_decimal(row[3], "low"),
            close=_decimal(row[4], "close"),
            volume=_decimal(row[5], "volume", nonnegative=True),
            quote_volume=_decimal(row[7], "quote volume", nonnegative=True),
            trade_count=trades,
            taker_buy_base_volume=_decimal(row[9], "taker buy base", nonnegative=True),
            taker_buy_quote_volume=_decimal(row[10], "taker buy quote", nonnegative=True),
        )
    except MarketDataValidationError as exc:
        raise HistoricalDataError("Native weekly candle violates the market contract") from exc


@dataclass(frozen=True, slots=True)
class NativeWeeklySummary:
    dataset_id: str
    manifest_sha256: str
    coverage_start: datetime
    coverage_end: datetime
    candle_count: int


class NativeWeeklySource(Protocol):
    def visit(self, path: Path, visitor: Callable[[Candle], None]) -> NativeWeeklySummary: ...


@dataclass(frozen=True, slots=True)
class NativeWeeklyImportSummary(NativeWeeklySummary):
    overlap_checked_count: int


class NativeWeeklyOverlapError(DomainValidationError):
    """Retain the exact public-data discrepancy without relaxing source agreement."""

    def __init__(self, native: Candle, derived: Candle, manifest_sha256: str) -> None:
        self.week_open = native.open_time
        self.manifest_sha256 = manifest_sha256
        self.differences = {
            field.name: {
                "native": str(getattr(native, field.name)),
                "derived": str(getattr(derived, field.name)),
            }
            for field in fields(native)
            if getattr(native, field.name) != getattr(derived, field.name)
        }
        super().__init__(
            "Native weekly candle disagrees with complete one-minute-derived week "
            f"at {native.open_time.isoformat()}: {','.join(self.differences)}"
        )

    def diagnostic_payload(self) -> dict[str, object]:
        return {
            "stop_reason": "NATIVE_WEEKLY_OVERLAP_CONFLICT",
            "performance_verdict": "NOT_RUN",
            "week_open": self.week_open.isoformat(),
            "native_weekly_manifest_sha256": self.manifest_sha256,
            "differences": self.differences,
        }


class NativeWeeklyLoader:
    """Require all native rows plus the publisher's checksum; never repair gaps."""

    def visit(self, manifest_path: Path, visitor: Callable[[Candle], None]) -> NativeWeeklySummary:
        if manifest_path.stat().st_size > 1_000_000:
            raise HistoricalDataError("Native weekly manifest is too large")
        raw = manifest_path.read_bytes()
        try:
            payload = json.loads(raw, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalDataError("Native weekly manifest is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise HistoricalDataError("Native weekly manifest must be an object")
        _fields(payload, _TOP_FIELDS, "native weekly manifest")
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["symbol"] != "BTCUSDT"
            or payload["venue"] != "SPOT"
            or payload["interval"] != "1w"
            or payload["source_origin"] != _ORIGIN
        ):
            raise HistoricalDataError("Native weekly manifest identity is unsupported")
        identity = payload["dataset_id"]
        if not isinstance(identity, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identity
        ):
            raise HistoricalDataError("Native weekly dataset identity is invalid")
        first, stop = _range(
            _utc(payload["archive_start"], "archive_start"),
            _utc(payload["archive_end"], "archive_end"),
        )
        specs = payload["archives"]
        if not isinstance(specs, list):
            raise HistoricalDataError("Native weekly archives must be an array")
        root = manifest_path.resolve().parent
        expected_month = first
        candles: list[Candle] = []
        for spec in specs:
            if not isinstance(spec, dict):
                raise HistoricalDataError("Native weekly archive specification must be an object")
            _fields(spec, _ARCHIVE_FIELDS, "native weekly archive")
            if not all(isinstance(spec[key], str) for key in _ARCHIVE_FIELDS):
                raise HistoricalDataError("Native weekly archive fields must be strings")
            if expected_month >= stop or spec["month"] != expected_month.strftime("%Y-%m"):
                raise HistoricalDataError(
                    "Native weekly archive months must be exact and continuous"
                )
            name = f"BTCUSDT-1w-{expected_month:%Y-%m}.zip"
            if (
                spec["path"] != f"archives/{name}"
                or spec["source_url"] != f"{_ORIGIN}{_PREFIX}{name}"
            ):
                raise HistoricalDataError("Native weekly archive path is outside the fixed catalog")
            _validate_url(spec["source_url"], "1w")
            digest = spec["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise HistoricalDataError("Native weekly archive SHA-256 is invalid")
            path = (root / spec["path"]).resolve()
            checksum_path = (root / f"{spec['path']}.CHECKSUM").resolve()
            for item in (path, checksum_path):
                if not item.is_relative_to(root) or not item.is_file():
                    raise HistoricalDataError(
                        "Native weekly archive or checksum escapes its root or is missing"
                    )
            if _file_sha256(path, 2_000_000) != digest or checksum_path.stat().st_size > 1024:
                raise HistoricalDataError("Native weekly archive checksum or size is invalid")
            try:
                declared = checksum_path.read_text(encoding="utf-8").split()
            except UnicodeDecodeError as exc:
                raise HistoricalDataError("Native weekly publisher checksum is not UTF-8") from exc
            if declared != [digest, name]:
                raise HistoricalDataError("Native weekly publisher checksum does not match archive")
            try:
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) != 1:
                        raise HistoricalDataError("Native weekly ZIP must have exactly one member")
                    member = members[0]
                    if (
                        member.filename != PurePosixPath(name).with_suffix(".csv").name
                        or member.is_dir()
                        or member.flag_bits & 1
                        or member.file_size > 100_000
                    ):
                        raise HistoricalDataError("Native weekly ZIP member is unsafe or oversized")
                    with archive.open(member) as binary:
                        content = binary.read(100_001)
                    if len(content) > 100_000:
                        raise HistoricalDataError("Native weekly CSV is too large")
                    rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
            except (OSError, UnicodeDecodeError, zipfile.BadZipFile, csv.Error) as exc:
                raise HistoricalDataError("Native weekly archive is not a valid UTF-8 ZIP") from exc
            expected_opens = _weekly_opens(expected_month)
            if len(rows) != len(expected_opens):
                raise HistoricalDataError("Native weekly archive must contain every expected week")
            candles.extend(
                _candle(row, opened) for row, opened in zip(rows, expected_opens, strict=True)
            )
            expected_month = _next_month(expected_month)
        if expected_month != stop or not candles:
            raise HistoricalDataError("Native weekly archives do not cover the declared range")
        for older, newer in pairwise(candles):
            if older.close_time + timedelta(milliseconds=1) != newer.open_time:
                raise HistoricalDataError("Native weekly candles are not continuous")
        for candle in candles:
            visitor(candle)
        return NativeWeeklySummary(
            identity,
            hashlib.sha256(raw).hexdigest(),
            candles[0].open_time,
            candles[-1].close_time + timedelta(milliseconds=1),
            len(candles),
        )

    def validate(self, path: Path) -> NativeWeeklySummary:
        return self.visit(path, lambda _candle: None)


class BinanceVisionWeeklyBuilder:
    def __init__(self, downloader: ArchiveDownloader | None = None) -> None:
        self.downloader = downloader or UrllibArchiveDownloader(
            archive_interval="1w", maximum_bytes=2_000_000
        )

    def build(self, start: datetime, end: datetime, output: Path, dataset_id: str) -> Path:
        first, stop = _range(start, end)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", dataset_id):
            raise HistoricalDataError("Native weekly dataset identity is invalid")
        if output.exists():
            raise HistoricalDataError("Native weekly output directory already exists")
        archives = output / "archives"
        archives.mkdir(parents=True)
        specs = []
        month = first
        while month < stop:
            name = f"BTCUSDT-1w-{month:%Y-%m}.zip"
            url = f"{_ORIGIN}{_PREFIX}{name}"
            temporary = archives / f"{name}.part"
            result = self.downloader.download(url, temporary)
            temporary.rename(archives / name)
            self.downloader.download(f"{url}.CHECKSUM", archives / f"{name}.CHECKSUM")
            specs.append(
                {
                    "path": f"archives/{name}",
                    "source_url": url,
                    "sha256": result.sha256,
                    "month": f"{month:%Y-%m}",
                }
            )
            month = _next_month(month)
        payload = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1w",
            "source_origin": _ORIGIN,
            "archive_start": first.isoformat(),
            "archive_end": stop.isoformat(),
            "archives": specs,
        }
        temporary = output / "weekly-manifest.json.part"
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        NativeWeeklyLoader().validate(temporary)
        final = output / "weekly-manifest.json"
        temporary.rename(final)
        return final

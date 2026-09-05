"""Strict loader for immutable Binance Vision BTCUSDT one-minute archives."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from btc_sentinel.errors import BtcSentinelError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import BTCUSDT, Candle, CandleSeries

_ORIGIN = "https://data.binance.vision"
_MICROSECOND_CUTOFF = datetime(2025, 1, 1, tzinfo=UTC)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "dataset_id",
    "symbol",
    "venue",
    "interval",
    "coverage_start",
    "coverage_end",
    "source_origin",
    "exhaustive_candidate_scan",
    "excluded_features",
    "archives",
}
_ARCHIVE_FIELDS = {
    "path",
    "source_url",
    "sha256",
    "timestamp_unit",
    "coverage_start",
    "coverage_end",
    "row_count",
}
_ARCHIVE_FIELDS_V2 = _ARCHIVE_FIELDS | {"close_time_anomalies", "missing_intervals"}
_ANOMALY_FIELDS = {"row_number", "open_timestamp", "raw_close_timestamp"}
_MISSING_FIELDS = {"start", "end"}


class HistoricalDataError(BtcSentinelError):
    """A historical archive or manifest violates the replay contract."""


class TimestampUnit(StrEnum):
    MILLISECONDS = "milliseconds"
    MICROSECONDS = "microseconds"

    @property
    def scale(self) -> int:
        return 1_000 if self is TimestampUnit.MILLISECONDS else 1_000_000


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDataError(f"{name} must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataError(f"{name} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalDataError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalDataError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _fields(value: dict[str, object], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing or unknown:
        raise HistoricalDataError(
            f"{name} fields are invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


@dataclass(frozen=True, slots=True)
class CloseTimeAnomaly:
    row_number: int
    open_timestamp: int
    raw_close_timestamp: int


@dataclass(frozen=True, slots=True)
class MissingInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
        ):
            raise HistoricalDataError("Archive missing interval must be timezone-aware")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    path: str
    source_url: str
    sha256: str
    timestamp_unit: TimestampUnit
    coverage_start: datetime
    coverage_end: datetime
    row_count: int
    close_time_anomalies: tuple[CloseTimeAnomaly, ...] = ()
    missing_intervals: tuple[MissingInterval, ...] = ()

    def __post_init__(self) -> None:
        if self.coverage_start.tzinfo is None or self.coverage_end.tzinfo is None:
            raise HistoricalDataError("Archive coverage must be timezone-aware")
        object.__setattr__(self, "coverage_start", self.coverage_start.astimezone(UTC))
        object.__setattr__(self, "coverage_end", self.coverage_end.astimezone(UTC))
        relative = PurePosixPath(self.path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in self.path
            or relative.suffix != ".zip"
        ):
            raise HistoricalDataError("Archive path must be a safe relative ZIP path")
        parsed = urlsplit(self.source_url)
        valid_prefixes = (
            "/data/spot/daily/klines/BTCUSDT/1m/",
            "/data/spot/monthly/klines/BTCUSDT/1m/",
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname != "data.binance.vision"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(valid_prefixes)
            or PurePosixPath(parsed.path).name != relative.name
        ):
            raise HistoricalDataError("Archive source URL is outside the fixed Binance path")
        if not _SHA256.fullmatch(self.sha256):
            raise HistoricalDataError("Archive SHA-256 must be 64 lowercase hexadecimal characters")
        if self.coverage_start.second or self.coverage_start.microsecond:
            raise HistoricalDataError("Archive coverage_start must be minute-aligned")
        if self.coverage_end.second or self.coverage_end.microsecond:
            raise HistoricalDataError("Archive coverage_end must be minute-aligned")
        expected_unit = (
            TimestampUnit.MILLISECONDS
            if self.coverage_end <= _MICROSECOND_CUTOFF
            else TimestampUnit.MICROSECONDS
            if self.coverage_start >= _MICROSECOND_CUTOFF
            else None
        )
        if expected_unit is None or self.timestamp_unit is not expected_unit:
            raise HistoricalDataError("Archive timestamp unit contradicts its Binance date range")
        expected_rows = int((self.coverage_end - self.coverage_start) / timedelta(minutes=1))
        if self.coverage_end <= self.coverage_start:
            raise HistoricalDataError("Archive coverage is empty")
        object.__setattr__(self, "close_time_anomalies", tuple(self.close_time_anomalies))
        object.__setattr__(self, "missing_intervals", tuple(self.missing_intervals))
        previous_end = self.coverage_start
        missing_minutes = 0
        for interval in self.missing_intervals:
            start = interval.start.astimezone(UTC)
            end = interval.end.astimezone(UTC)
            if (
                start < self.coverage_start
                or end > self.coverage_end
                or end <= start
                or start.second
                or start.microsecond
                or end.second
                or end.microsecond
                or start < previous_end
            ):
                raise HistoricalDataError("Archive missing intervals are invalid")
            previous_end = end
            missing_minutes += int((end - start) / timedelta(minutes=1))
        rows = [item.row_number for item in self.close_time_anomalies]
        if len(rows) != len(set(rows)) or any(not 1 <= row <= self.row_count for row in rows):
            raise HistoricalDataError("Archive close-time anomaly rows are invalid")
        minute = 60 * self.timestamp_unit.scale
        for item in self.close_time_anomalies:
            if (
                item.open_timestamp % minute
                or not item.open_timestamp
                <= item.raw_close_timestamp
                < item.open_timestamp + minute - 1
            ):
                raise HistoricalDataError("Archive close-time anomaly values are invalid")
            opened = _epoch(item.open_timestamp, self.timestamp_unit)
            if not any(
                interval.start <= opened < interval.end for interval in self.missing_intervals
            ):
                raise HistoricalDataError(
                    "Archive close-time anomaly is not inside a missing interval"
                )
        expected_raw_rows = expected_rows - missing_minutes + len(self.close_time_anomalies)
        if self.row_count != expected_raw_rows:
            raise HistoricalDataError("Archive row_count contradicts its missing intervals")


@dataclass(frozen=True, slots=True)
class HistoricalDatasetManifest:
    dataset_id: str
    coverage_start: datetime
    coverage_end: datetime
    excluded_features: tuple[str, ...]
    archives: tuple[ArchiveSpec, ...]

    def __post_init__(self) -> None:
        if self.coverage_start.tzinfo is None or self.coverage_end.tzinfo is None:
            raise HistoricalDataError("Dataset coverage must be timezone-aware")
        object.__setattr__(self, "coverage_start", self.coverage_start.astimezone(UTC))
        object.__setattr__(self, "coverage_end", self.coverage_end.astimezone(UTC))
        object.__setattr__(self, "excluded_features", tuple(self.excluded_features))
        object.__setattr__(self, "archives", tuple(self.archives))
        if not _DATASET_ID.fullmatch(self.dataset_id):
            raise HistoricalDataError("Dataset identifier has an invalid format")
        if not self.archives:
            raise HistoricalDataError("Dataset manifest requires at least one archive")
        if self.archives[0].coverage_start != self.coverage_start:
            raise HistoricalDataError("Dataset coverage_start does not match the first archive")
        if self.archives[-1].coverage_end != self.coverage_end:
            raise HistoricalDataError("Dataset coverage_end does not match the last archive")
        for previous, current in zip(self.archives, self.archives[1:], strict=False):
            if previous.coverage_end != current.coverage_start:
                raise HistoricalDataError("Manifest archives must have continuous UTC coverage")
        if len({item.path for item in self.archives}) != len(self.archives):
            raise HistoricalDataError("Manifest archive paths must be unique")
        if len(set(self.excluded_features)) != len(self.excluded_features):
            raise HistoricalDataError("Excluded historical features must be unique")


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    manifest: HistoricalDatasetManifest
    manifest_sha256: str
    candles: CandleSeries

    @property
    def candle_count(self) -> int:
        return len(self.candles.candles)


@dataclass(frozen=True, slots=True)
class HistoricalDatasetSummary:
    manifest: HistoricalDatasetManifest
    manifest_sha256: str
    candle_count: int
    first_open_time: datetime
    last_close_time: datetime


def _anomalies(value: object, index: int, version: int) -> tuple[CloseTimeAnomaly, ...]:
    if version == 1:
        return ()
    if not isinstance(value, list):
        raise HistoricalDataError(f"archives[{index}].close_time_anomalies must be an array")
    result = []
    for anomaly_index, item in enumerate(value):
        if not isinstance(item, dict):
            raise HistoricalDataError("Archive close-time anomaly must be an object")
        _fields(item, _ANOMALY_FIELDS, f"archives[{index}].close_time_anomalies[{anomaly_index}]")
        values = (item["row_number"], item["open_timestamp"], item["raw_close_timestamp"])
        if any(isinstance(part, bool) or not isinstance(part, int) for part in values):
            raise HistoricalDataError("Archive close-time anomaly values must be integers")
        result.append(CloseTimeAnomaly(*values))
    return tuple(result)


def _missing(value: object, index: int, version: int) -> tuple[MissingInterval, ...]:
    if version == 1:
        return ()
    if not isinstance(value, list):
        raise HistoricalDataError(f"archives[{index}].missing_intervals must be an array")
    result = []
    for missing_index, item in enumerate(value):
        if not isinstance(item, dict):
            raise HistoricalDataError("Archive missing interval must be an object")
        _fields(item, _MISSING_FIELDS, f"archives[{index}].missing_intervals[{missing_index}]")
        result.append(
            MissingInterval(
                _utc(item["start"], "missing interval start"),
                _utc(item["end"], "missing interval end"),
            )
        )
    return tuple(result)


def _archive(value: object, index: int, version: int) -> ArchiveSpec:
    if not isinstance(value, dict):
        raise HistoricalDataError(f"archives[{index}] must be an object")
    _fields(value, _ARCHIVE_FIELDS if version == 1 else _ARCHIVE_FIELDS_V2, f"archives[{index}]")
    try:
        unit = TimestampUnit(value["timestamp_unit"])
    except (TypeError, ValueError) as exc:
        raise HistoricalDataError(f"archives[{index}].timestamp_unit is unsupported") from exc
    row_count = value["row_count"]
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
        raise HistoricalDataError(f"archives[{index}].row_count must be a positive integer")
    path = value["path"]
    source_url = value["source_url"]
    digest = value["sha256"]
    if not all(isinstance(item, str) for item in (path, source_url, digest)):
        raise HistoricalDataError(f"archives[{index}] paths and sha256 must be strings")
    return ArchiveSpec(
        path=path,
        source_url=source_url,
        sha256=digest,
        timestamp_unit=unit,
        coverage_start=_utc(value["coverage_start"], f"archives[{index}].coverage_start"),
        coverage_end=_utc(value["coverage_end"], f"archives[{index}].coverage_end"),
        row_count=row_count,
        close_time_anomalies=_anomalies(value.get("close_time_anomalies"), index, version),
        missing_intervals=_missing(value.get("missing_intervals"), index, version),
    )


def parse_manifest(raw: bytes) -> tuple[HistoricalDatasetManifest, str]:
    """Parse a canonical manifest without accepting duplicate or unknown fields."""

    if len(raw) > 1_000_000:
        raise HistoricalDataError("Historical manifest is too large")
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalDataError("Historical manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HistoricalDataError("Historical manifest must be a JSON object")
    _fields(payload, _TOP_LEVEL_FIELDS, "manifest")
    version = payload["schema_version"]
    if isinstance(version, bool) or version not in {1, 2}:
        raise HistoricalDataError("Historical manifest schema_version must be 1 or 2")
    if (
        payload["symbol"] != BTCUSDT
        or payload["venue"] != MarketVenue.SPOT.value
        or payload["interval"] != MarketInterval.ONE_MINUTE.value
    ):
        raise HistoricalDataError("Historical manifest supports Spot BTCUSDT 1m only")
    if payload["source_origin"] != _ORIGIN:
        raise HistoricalDataError("Historical manifest source origin is not allowlisted")
    if payload["exhaustive_candidate_scan"] is not True:
        raise HistoricalDataError("Historical manifest must require an exhaustive candidate scan")
    dataset_id = payload["dataset_id"]
    excluded = payload["excluded_features"]
    archives = payload["archives"]
    if not isinstance(dataset_id, str):
        raise HistoricalDataError("dataset_id must be a string")
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        raise HistoricalDataError("excluded_features must be a string array")
    if not isinstance(archives, list):
        raise HistoricalDataError("archives must be an array")
    manifest = HistoricalDatasetManifest(
        dataset_id=dataset_id,
        coverage_start=_utc(payload["coverage_start"], "coverage_start"),
        coverage_end=_utc(payload["coverage_end"], "coverage_end"),
        excluded_features=tuple(excluded),
        archives=tuple(_archive(item, index, version) for index, item in enumerate(archives)),
    )
    return manifest, hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path, maximum_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            total += len(block)
            if total > maximum_bytes:
                raise HistoricalDataError("Historical archive exceeds the configured size limit")
            digest.update(block)
    return digest.hexdigest()


def _epoch(value: int, unit: TimestampUnit) -> datetime:
    seconds, remainder = divmod(value, unit.scale)
    microseconds = remainder * (1_000 if unit is TimestampUnit.MILLISECONDS else 1)
    try:
        return datetime.fromtimestamp(seconds, UTC).replace(microsecond=microseconds)
    except (OverflowError, OSError, ValueError) as exc:
        raise HistoricalDataError("Archive timestamp is outside the supported range") from exc


def _decimal(value: str, name: str, *, nonnegative: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise HistoricalDataError(f"Archive {name} is not a decimal") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise HistoricalDataError(f"Archive {name} is outside the accepted range")
    return parsed


def _candle(row: list[str], unit: TimestampUnit) -> Candle:
    if len(row) != 12:
        raise HistoricalDataError("Every Binance kline row must contain exactly 12 fields")
    try:
        raw_open, raw_close, trade_count = int(row[0]), int(row[6]), int(row[8])
    except ValueError as exc:
        raise HistoricalDataError("Archive timestamps and trade count must be integers") from exc
    minute = 60 * unit.scale
    if raw_open % minute or raw_close != raw_open + minute - 1:
        raise HistoricalDataError("Archive candle timestamps are not an exact completed minute")
    open_time = _epoch(raw_open, unit)
    if trade_count < 0:
        raise HistoricalDataError("Archive trade count cannot be negative")
    _decimal(row[11], "ignore field", nonnegative=True)
    return Candle(
        venue=MarketVenue.SPOT,
        interval=MarketInterval.ONE_MINUTE,
        open_time=open_time,
        close_time=MarketInterval.ONE_MINUTE.expected_close_time(open_time),
        open=_decimal(row[1], "open"),
        high=_decimal(row[2], "high"),
        low=_decimal(row[3], "low"),
        close=_decimal(row[4], "close"),
        volume=_decimal(row[5], "volume", nonnegative=True),
        quote_volume=_decimal(row[7], "quote volume", nonnegative=True),
        trade_count=trade_count,
        taker_buy_base_volume=_decimal(row[9], "taker-buy base volume", nonnegative=True),
        taker_buy_quote_volume=_decimal(row[10], "taker-buy quote volume", nonnegative=True),
    )


class HistoricalDatasetLoader:
    """Verify hashes, ZIP structure, rows, timestamps, and full continuity."""

    def __init__(
        self,
        *,
        maximum_archive_bytes: int = 512_000_000,
        maximum_uncompressed_bytes: int = 2_000_000_000,
        maximum_candles: int = 10_000_000,
        maximum_loaded_candles: int = 500_000,
    ) -> None:
        if (
            min(
                maximum_archive_bytes,
                maximum_uncompressed_bytes,
                maximum_candles,
                maximum_loaded_candles,
            )
            < 1
        ):
            raise ValueError("Historical dataset limits must be positive")
        self.maximum_archive_bytes = maximum_archive_bytes
        self.maximum_uncompressed_bytes = maximum_uncompressed_bytes
        self.maximum_candles = maximum_candles
        self.maximum_loaded_candles = maximum_loaded_candles

    def validate(self, manifest_path: Path) -> HistoricalDatasetSummary:
        summary, _ = self._read(manifest_path, collect=False)
        return summary

    def visit(
        self,
        manifest_path: Path,
        visitor: Callable[[Candle], None],
    ) -> HistoricalDatasetSummary:
        """Validate a dataset while streaming each candle to a bounded consumer."""

        summary, _ = self._read(manifest_path, collect=False, visitor=visitor)
        return summary

    def load(self, manifest_path: Path) -> HistoricalDataset:
        summary, candles = self._read(manifest_path, collect=True)
        assert candles is not None
        return HistoricalDataset(summary.manifest, summary.manifest_sha256, CandleSeries(candles))

    def _read(
        self,
        manifest_path: Path,
        *,
        collect: bool,
        visitor: Callable[[Candle], None] | None = None,
    ) -> tuple[HistoricalDatasetSummary, tuple[Candle, ...] | None]:
        root = manifest_path.resolve().parent
        manifest, manifest_digest = parse_manifest(manifest_path.read_bytes())
        candles: list[Candle] = []
        candle_count = 0
        first_open: datetime | None = None
        last_close: datetime | None = None
        previous_open: datetime | None = None
        for spec in manifest.archives:
            archive_path = (root / spec.path).resolve()
            try:
                archive_path.relative_to(root)
            except ValueError as exc:
                raise HistoricalDataError("Archive path escapes the manifest directory") from exc
            if not archive_path.is_file():
                raise HistoricalDataError(f"Historical archive is missing: {spec.path}")
            if _file_sha256(archive_path, self.maximum_archive_bytes) != spec.sha256:
                raise HistoricalDataError(f"Historical archive checksum mismatch: {spec.path}")
            for candle in self._iter_archive(archive_path, spec):
                if first_open is None:
                    first_open = candle.open_time
                previous_open = candle.open_time
                last_close = candle.close_time
                candle_count += 1
                if candle_count > self.maximum_candles:
                    raise HistoricalDataError("Historical dataset exceeds the candle limit")
                if collect:
                    if candle_count > self.maximum_loaded_candles:
                        raise HistoricalDataError(
                            "Historical dataset exceeds the bounded in-memory load limit"
                        )
                    candles.append(candle)
                if visitor is not None:
                    visitor(candle)
        assert first_open is not None and previous_open is not None and last_close is not None
        summary = HistoricalDatasetSummary(
            manifest,
            manifest_digest,
            candle_count,
            first_open,
            last_close,
        )
        return summary, tuple(candles) if collect else None

    def _iter_archive(self, path: Path, spec: ArchiveSpec) -> Iterator[Candle]:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                expected_member = PurePosixPath(spec.path).with_suffix(".csv").name
                if len(members) != 1 or members[0].filename != expected_member:
                    raise HistoricalDataError("Historical ZIP must contain its one expected CSV")
                member = members[0]
                if member.is_dir() or member.flag_bits & 0x1:
                    raise HistoricalDataError(
                        "Historical ZIP member cannot be a directory or encrypted"
                    )
                if member.file_size > self.maximum_uncompressed_bytes:
                    raise HistoricalDataError("Historical CSV exceeds the uncompressed size limit")
                if member.compress_size and member.file_size > member.compress_size * 100:
                    raise HistoricalDataError("Historical ZIP compression ratio is unsafe")
                with archive.open(member) as binary:
                    text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
                    row_count = 0
                    declared = {
                        item.row_number: (item.open_timestamp, item.raw_close_timestamp)
                        for item in spec.close_time_anomalies
                    }
                    used: set[int] = set()
                    intervals = iter(spec.missing_intervals)
                    interval = next(intervals, None)
                    expected_open = spec.coverage_start
                    for row_number, row in enumerate(csv.reader(text), start=1):
                        if len(row) != 12:
                            raise HistoricalDataError(
                                "Every Binance kline row must contain exactly 12 fields"
                            )
                        try:
                            raw_open, raw_close = int(row[0]), int(row[6])
                        except ValueError as exc:
                            raise HistoricalDataError(
                                "Archive timestamps must be integers"
                            ) from exc
                        opened = _epoch(raw_open, spec.timestamp_unit)
                        while interval is not None and interval.end <= opened:
                            if expected_open != interval.start:
                                raise HistoricalDataError(
                                    "Archive data does not reach its declared missing interval"
                                )
                            expected_open = interval.end
                            interval = next(intervals, None)
                        if interval is not None and interval.start <= opened < interval.end:
                            if declared.get(row_number) != (raw_open, raw_close):
                                raise HistoricalDataError(
                                    "Archive contains data inside a declared missing interval"
                                )
                            used.add(row_number)
                            row_count += 1
                            continue
                        if opened != expected_open:
                            raise HistoricalDataError(
                                "Historical candles are gapped or unordered without declaration"
                            )
                        candle = _candle(row, spec.timestamp_unit)
                        expected_open += timedelta(minutes=1)
                        row_count += 1
                        yield candle
                    while interval is not None:
                        if expected_open != interval.start:
                            raise HistoricalDataError(
                                "Archive data does not reach its declared missing interval"
                            )
                        expected_open = interval.end
                        interval = next(intervals, None)
                    if expected_open != spec.coverage_end:
                        raise HistoricalDataError(
                            "Archive data does not match its declared coverage and gaps"
                        )
                    if used != set(declared):
                        raise HistoricalDataError(
                            "Declared archive close-time anomaly was not used"
                        )
        except MarketDataValidationError as exc:
            raise HistoricalDataError(
                "Historical candle values violate the market contract"
            ) from exc
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile, csv.Error) as exc:
            raise HistoricalDataError("Historical archive is not a valid UTF-8 ZIP/CSV") from exc
        if row_count != spec.row_count:
            raise HistoricalDataError("Historical archive row count does not match the manifest")

"""Strict loader for immutable Binance Vision BTCUSDT one-minute archives."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator
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
class ArchiveSpec:
    path: str
    source_url: str
    sha256: str
    timestamp_unit: TimestampUnit
    coverage_start: datetime
    coverage_end: datetime
    row_count: int

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
        if self.coverage_end <= self.coverage_start or self.row_count != expected_rows:
            raise HistoricalDataError("Archive row_count does not match its exclusive coverage")


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


def _archive(value: object, index: int) -> ArchiveSpec:
    if not isinstance(value, dict):
        raise HistoricalDataError(f"archives[{index}] must be an object")
    _fields(value, _ARCHIVE_FIELDS, f"archives[{index}]")
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
    if payload["schema_version"] != 1:
        raise HistoricalDataError("Historical manifest schema_version must be 1")
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
        archives=tuple(_archive(item, index) for index, item in enumerate(archives)),
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

    def load(self, manifest_path: Path) -> HistoricalDataset:
        summary, candles = self._read(manifest_path, collect=True)
        assert candles is not None
        return HistoricalDataset(summary.manifest, summary.manifest_sha256, CandleSeries(candles))

    def _read(
        self, manifest_path: Path, *, collect: bool
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
                if previous_open is not None and candle.open_time != previous_open + timedelta(
                    minutes=1
                ):
                    raise HistoricalDataError("Historical candles are gapped or unordered")
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
        assert first_open is not None and previous_open is not None and last_close is not None
        if first_open != manifest.coverage_start:
            raise HistoricalDataError("First candle does not match manifest coverage_start")
        if previous_open + timedelta(minutes=1) != manifest.coverage_end:
            raise HistoricalDataError("Last candle does not match manifest coverage_end")
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
                    first_open: datetime | None = None
                    previous_open: datetime | None = None
                    for row in csv.reader(text):
                        candle = _candle(row, spec.timestamp_unit)
                        if (
                            previous_open is not None
                            and candle.open_time != previous_open + timedelta(minutes=1)
                        ):
                            raise HistoricalDataError("Historical candles are gapped or unordered")
                        if first_open is None:
                            first_open = candle.open_time
                        previous_open = candle.open_time
                        row_count += 1
                        yield candle
        except MarketDataValidationError as exc:
            raise HistoricalDataError(
                "Historical candle values violate the market contract"
            ) from exc
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile, csv.Error) as exc:
            raise HistoricalDataError("Historical archive is not a valid UTF-8 ZIP/CSV") from exc
        if row_count != spec.row_count:
            raise HistoricalDataError("Historical archive row count does not match the manifest")
        assert first_open is not None and previous_open is not None
        if first_open != spec.coverage_start:
            raise HistoricalDataError("Archive first candle does not match declared coverage")
        if previous_open + timedelta(minutes=1) != spec.coverage_end:
            raise HistoricalDataError("Archive last candle does not match declared coverage")

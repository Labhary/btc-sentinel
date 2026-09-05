"""Official native Binance BTCUSDT monthly candles for long-horizon warm-up."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from btc_sentinel.backtesting.archive_fetch import (
    ArchiveDownloader,
    UrllibArchiveDownloader,
    _month_bound,
    _next_month,
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
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import Candle

_ORIGIN = "https://data.binance.vision"
_PREFIX = "/data/spot/monthly/klines/BTCUSDT/1mo/"
_NAME = re.compile(r"BTCUSDT-1mo-\d{4}-\d{2}\.zip")
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MICROSECOND_CUTOFF = datetime(2025, 1, 1, tzinfo=UTC)
_TOP_FIELDS = {
    "schema_version",
    "dataset_id",
    "symbol",
    "venue",
    "interval",
    "coverage_start",
    "coverage_end",
    "source_origin",
    "archives",
}
_ARCHIVE_FIELDS = {
    "path",
    "source_url",
    "sha256",
    "timestamp_unit",
    "coverage_start",
    "coverage_end",
}


@dataclass(frozen=True, slots=True)
class NativeMonthlyArchiveSpec:
    path: str
    source_url: str
    sha256: str
    timestamp_unit: TimestampUnit
    coverage_start: datetime
    coverage_end: datetime

    def __post_init__(self) -> None:
        start = _month_bound(self.coverage_start, "archive coverage_start")
        end = _month_bound(self.coverage_end, "archive coverage_end")
        if end != _next_month(start):
            raise HistoricalDataError("Native monthly archive must cover exactly one UTC month")
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        relative = PurePosixPath(self.path)
        parsed = urlsplit(self.source_url)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in self.path
            or not _NAME.fullmatch(relative.name)
            or parsed.scheme != "https"
            or parsed.hostname != "data.binance.vision"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(_PREFIX)
            or PurePosixPath(parsed.path).name != relative.name
        ):
            raise HistoricalDataError("Native monthly archive path is outside Binance 1mo")
        if not _SHA256.fullmatch(self.sha256):
            raise HistoricalDataError("Native monthly archive SHA-256 is invalid")
        expected_unit = (
            TimestampUnit.MILLISECONDS if end <= _MICROSECOND_CUTOFF else TimestampUnit.MICROSECONDS
        )
        if self.timestamp_unit is not expected_unit:
            raise HistoricalDataError("Native monthly timestamp unit contradicts its date")


@dataclass(frozen=True, slots=True)
class NativeMonthlyManifest:
    dataset_id: str
    coverage_start: datetime
    coverage_end: datetime
    archives: tuple[NativeMonthlyArchiveSpec, ...]

    def __post_init__(self) -> None:
        start = _month_bound(self.coverage_start, "coverage_start")
        end = _month_bound(self.coverage_end, "coverage_end")
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(self, "archives", tuple(self.archives))
        if not _DATASET_ID.fullmatch(self.dataset_id) or not self.archives:
            raise HistoricalDataError("Native monthly manifest identity or archives are invalid")
        if self.archives[0].coverage_start != start or self.archives[-1].coverage_end != end:
            raise HistoricalDataError("Native monthly manifest boundaries contradict archives")
        for previous, current in zip(self.archives, self.archives[1:], strict=False):
            if previous.coverage_end != current.coverage_start:
                raise HistoricalDataError("Native monthly archives must be continuous")
        if len({item.path for item in self.archives}) != len(self.archives):
            raise HistoricalDataError("Native monthly archive paths must be unique")


@dataclass(frozen=True, slots=True)
class NativeMonthlySummary:
    dataset_id: str
    manifest_sha256: str
    coverage_start: datetime
    coverage_end: datetime
    candle_count: int


def parse_monthly_manifest(raw: bytes) -> tuple[NativeMonthlyManifest, str]:
    if len(raw) > 1_000_000:
        raise HistoricalDataError("Native monthly manifest is too large")
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalDataError("Native monthly manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HistoricalDataError("Native monthly manifest must be an object")
    _fields(payload, _TOP_FIELDS, "native monthly manifest")
    if (
        payload["schema_version"] != 1
        or payload["symbol"] != "BTCUSDT"
        or payload["venue"] != MarketVenue.SPOT.value
        or payload["interval"] != "1mo"
        or payload["source_origin"] != _ORIGIN
    ):
        raise HistoricalDataError("Native monthly manifest identity is unsupported")
    dataset_id = payload["dataset_id"]
    archives = payload["archives"]
    if not isinstance(dataset_id, str) or not isinstance(archives, list):
        raise HistoricalDataError("Native monthly dataset_id or archives are invalid")
    specs: list[NativeMonthlyArchiveSpec] = []
    for index, value in enumerate(archives):
        if not isinstance(value, dict):
            raise HistoricalDataError(f"native monthly archives[{index}] must be an object")
        _fields(value, _ARCHIVE_FIELDS, f"native monthly archives[{index}]")
        try:
            unit = TimestampUnit(value["timestamp_unit"])
        except (TypeError, ValueError) as exc:
            raise HistoricalDataError("Native monthly timestamp unit is unsupported") from exc
        if not all(isinstance(value[name], str) for name in _ARCHIVE_FIELDS):
            raise HistoricalDataError("Native monthly archive fields must be strings")
        specs.append(
            NativeMonthlyArchiveSpec(
                value["path"],
                value["source_url"],
                value["sha256"],
                unit,
                _utc(value["coverage_start"], "archive coverage_start"),
                _utc(value["coverage_end"], "archive coverage_end"),
            )
        )
    manifest = NativeMonthlyManifest(
        dataset_id,
        _utc(payload["coverage_start"], "coverage_start"),
        _utc(payload["coverage_end"], "coverage_end"),
        tuple(specs),
    )
    return manifest, hashlib.sha256(raw).hexdigest()


def _monthly_candle(row: list[str], spec: NativeMonthlyArchiveSpec) -> Candle:
    if len(row) != 12:
        raise HistoricalDataError("Native monthly kline row must contain 12 fields")
    try:
        raw_open, raw_close, trade_count = int(row[0]), int(row[6]), int(row[8])
    except ValueError as exc:
        raise HistoricalDataError("Native monthly timestamps and trades must be integers") from exc
    scale = spec.timestamp_unit.scale
    close_time = MarketInterval.ONE_MONTH.expected_close_time(spec.coverage_start)
    expected_open = int(spec.coverage_start.timestamp()) * scale
    expected_close = (
        int(close_time.timestamp()) * scale + close_time.microsecond * scale // 1_000_000
    )
    if raw_open != expected_open or raw_close != expected_close:
        raise HistoricalDataError("Native monthly candle timestamps contradict its month")
    if trade_count < 0:
        raise HistoricalDataError("Native monthly trade count cannot be negative")
    _decimal(row[11], "ignore field", nonnegative=True)
    return Candle(
        venue=MarketVenue.SPOT,
        interval=MarketInterval.ONE_MONTH,
        open_time=_epoch(raw_open, spec.timestamp_unit),
        close_time=close_time,
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


class NativeMonthlyLoader:
    def __init__(
        self,
        *,
        maximum_archive_bytes: int = 2_000_000,
        maximum_uncompressed_bytes: int = 4_000_000,
    ) -> None:
        if min(maximum_archive_bytes, maximum_uncompressed_bytes) < 1:
            raise ValueError("Native monthly loader limits must be positive")
        self.maximum_archive_bytes = maximum_archive_bytes
        self.maximum_uncompressed_bytes = maximum_uncompressed_bytes

    def visit(self, manifest_path: Path, visitor: Callable[[Candle], None]) -> NativeMonthlySummary:
        manifest, digest = parse_monthly_manifest(manifest_path.read_bytes())
        root = manifest_path.resolve().parent
        count = 0
        for spec in manifest.archives:
            path = (root / spec.path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise HistoricalDataError("Native monthly archive escapes its root") from exc
            if not path.is_file() or _file_sha256(path, self.maximum_archive_bytes) != spec.sha256:
                raise HistoricalDataError("Native monthly archive is missing or checksum-invalid")
            try:
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    expected = PurePosixPath(spec.path).with_suffix(".csv").name
                    if len(members) != 1 or members[0].filename != expected:
                        raise HistoricalDataError("Native monthly ZIP member is invalid")
                    member = members[0]
                    if member.is_dir() or member.flag_bits & 0x1:
                        raise HistoricalDataError("Native monthly ZIP member is unsafe")
                    if member.file_size > self.maximum_uncompressed_bytes:
                        raise HistoricalDataError("Native monthly CSV exceeds its size limit")
                    with archive.open(member) as binary:
                        rows = list(csv.reader(io.TextIOWrapper(binary, encoding="utf-8")))
            except (OSError, UnicodeDecodeError, zipfile.BadZipFile, csv.Error) as exc:
                raise HistoricalDataError(
                    "Native monthly archive is not a valid UTF-8 ZIP"
                ) from exc
            if len(rows) != 1:
                raise HistoricalDataError("Native monthly archive must contain exactly one candle")
            try:
                visitor(_monthly_candle(rows[0], spec))
            except MarketDataValidationError as exc:
                raise HistoricalDataError(
                    "Native monthly candle violates the market contract"
                ) from exc
            count += 1
        return NativeMonthlySummary(
            manifest.dataset_id,
            digest,
            manifest.coverage_start,
            manifest.coverage_end,
            count,
        )

    def validate(self, manifest_path: Path) -> NativeMonthlySummary:
        return self.visit(manifest_path, lambda _candle: None)


@dataclass(frozen=True, slots=True)
class NativeMonthlyBuild:
    manifest_path: Path
    dataset_id: str
    archive_count: int


class BinanceVisionMonthlyBuilder:
    def __init__(
        self,
        downloader: ArchiveDownloader | None = None,
        loader: NativeMonthlyLoader | None = None,
        *,
        maximum_months: int = 120,
    ) -> None:
        if not 1 <= maximum_months <= 240:
            raise ValueError("maximum_months must be between 1 and 240")
        self.downloader = downloader or UrllibArchiveDownloader(archive_interval="1mo")
        self.loader = loader or NativeMonthlyLoader()
        self.maximum_months = maximum_months

    def build(
        self,
        start: datetime,
        end: datetime,
        output_directory: Path,
        dataset_id: str,
    ) -> NativeMonthlyBuild:
        first = _month_bound(start, "start")
        stop = _month_bound(end, "end")
        if stop <= first or not _DATASET_ID.fullmatch(dataset_id):
            raise HistoricalDataError("Native monthly range or dataset identifier is invalid")
        months: list[tuple[datetime, datetime]] = []
        current = first
        while current < stop:
            following = _next_month(current)
            months.append((current, following))
            current = following
        if len(months) > self.maximum_months:
            raise HistoricalDataError("Native monthly range exceeds the month limit")
        if output_directory.exists():
            raise HistoricalDataError("Native monthly output directory already exists")
        archives_directory = output_directory / "archives"
        archives_directory.mkdir(parents=True)
        records: list[dict[str, str]] = []
        for month_start, month_end in months:
            filename = f"BTCUSDT-1mo-{month_start:%Y-%m}.zip"
            relative = f"archives/{filename}"
            url = f"{_ORIGIN}{_PREFIX}{filename}"
            partial = archives_directory / f"{filename}.part"
            final = archives_directory / filename
            downloaded = self.downloader.download(url, partial)
            partial.rename(final)
            unit = (
                TimestampUnit.MILLISECONDS
                if month_end <= _MICROSECOND_CUTOFF
                else TimestampUnit.MICROSECONDS
            )
            records.append(
                {
                    "path": relative,
                    "source_url": url,
                    "sha256": downloaded.sha256,
                    "timestamp_unit": unit.value,
                    "coverage_start": month_start.isoformat().replace("+00:00", "Z"),
                    "coverage_end": month_end.isoformat().replace("+00:00", "Z"),
                }
            )
        payload = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1mo",
            "coverage_start": first.isoformat().replace("+00:00", "Z"),
            "coverage_end": stop.isoformat().replace("+00:00", "Z"),
            "source_origin": _ORIGIN,
            "archives": records,
        }
        temporary = output_directory / "monthly-manifest.json.part"
        final = output_directory / "monthly-manifest.json"
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        self.loader.validate(temporary)
        temporary.rename(final)
        return NativeMonthlyBuild(final, dataset_id, len(records))

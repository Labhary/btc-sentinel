"""Bounded acquisition of official monthly Binance Vision replay archives."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from btc_sentinel.backtesting.dataset import HistoricalDataError, HistoricalDatasetLoader

_ORIGIN = "https://data.binance.vision"
_ARCHIVE_PREFIX = "/data/spot/monthly/klines/BTCUSDT/1m/"
_MICROSECOND_CUTOFF = datetime(2025, 1, 1, tzinfo=UTC)
_ARCHIVE_NAME = re.compile(r"BTCUSDT-1m-\d{4}-\d{2}\.zip")
_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


@dataclass(frozen=True, slots=True)
class ArchiveDownload:
    sha256: str
    byte_count: int


class ArchiveDownloader(Protocol):
    def download(self, url: str, destination: Path) -> ArchiveDownload: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "data.binance.vision"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(_ARCHIVE_PREFIX)
        or not _ARCHIVE_NAME.fullmatch(parsed.path.removeprefix(_ARCHIVE_PREFIX))
    ):
        raise HistoricalDataError("Archive download URL is outside the fixed Binance path")


class UrllibArchiveDownloader:
    """Stream one fixed-host archive to a new file with bounded retries."""

    def __init__(
        self,
        *,
        maximum_bytes: int = 512_000_000,
        timeout_seconds: float = 30,
        maximum_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if maximum_bytes < 1 or not 0 < timeout_seconds <= 60:
            raise ValueError("Archive download size and timeout limits are invalid")
        if not 1 <= maximum_attempts <= 5:
            raise ValueError("Archive download attempts must be between 1 and 5")
        self.maximum_bytes = maximum_bytes
        self.timeout_seconds = timeout_seconds
        self.maximum_attempts = maximum_attempts
        self.sleeper = sleeper
        self.opener = build_opener(_NoRedirect())

    def download(self, url: str, destination: Path) -> ArchiveDownload:
        _validate_url(url)
        if destination.exists():
            raise HistoricalDataError("Archive download destination already exists")
        final_error: Exception | None = None
        for attempt in range(1, self.maximum_attempts + 1):
            try:
                return self._once(url, destination)
            except HistoricalDataError:
                destination.unlink(missing_ok=True)
                raise
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                final_error = exc
                destination.unlink(missing_ok=True)
                status = exc.code if isinstance(exc, HTTPError) else None
                retryable = status in {408, 429, 500, 502, 503, 504} or status is None
                if not retryable or attempt == self.maximum_attempts:
                    break
                self.sleeper(min(2 ** (attempt - 1), 5))
        raise HistoricalDataError("Official Binance archive download failed") from final_error

    def _once(self, url: str, destination: Path) -> ArchiveDownload:
        request = Request(
            url,
            method="GET",
            headers={"Accept": "application/zip", "User-Agent": "btc-sentinel/history-builder"},
        )
        digest = hashlib.sha256()
        total = 0
        with self.opener.open(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise HistoricalDataError("Official Binance archive returned a non-success status")
            with destination.open("xb") as target:
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > self.maximum_bytes:
                        raise HistoricalDataError("Official Binance archive exceeds size limit")
                    digest.update(block)
                    target.write(block)
        if total == 0:
            destination.unlink(missing_ok=True)
            raise HistoricalDataError("Official Binance archive is empty")
        return ArchiveDownload(digest.hexdigest(), total)


def _next_month(value: datetime) -> datetime:
    return (
        value.replace(year=value.year + 1, month=1)
        if value.month == 12
        else value.replace(month=value.month + 1)
    )


def _month_bound(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalDataError(f"{name} must be timezone-aware")
    result = value.astimezone(UTC)
    if result != result.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
        raise HistoricalDataError(f"{name} must be a UTC calendar-month boundary")
    return result


@dataclass(frozen=True, slots=True)
class HistoricalArchiveBuild:
    manifest_path: Path
    dataset_id: str
    archive_count: int
    candle_count: int


class BinanceVisionArchiveBuilder:
    """Create and fully validate a new monthly Spot BTCUSDT dataset directory."""

    def __init__(
        self,
        downloader: ArchiveDownloader | None = None,
        loader: HistoricalDatasetLoader | None = None,
        *,
        maximum_months: int = 120,
    ) -> None:
        if not 1 <= maximum_months <= 240:
            raise ValueError("maximum_months must be between 1 and 240")
        self.downloader = downloader or UrllibArchiveDownloader()
        self.loader = loader or HistoricalDatasetLoader()
        self.maximum_months = maximum_months

    def build(
        self,
        start: datetime,
        end: datetime,
        output_directory: Path,
        dataset_id: str,
    ) -> HistoricalArchiveBuild:
        first = _month_bound(start, "start")
        stop = _month_bound(end, "end")
        if not _DATASET_ID.fullmatch(dataset_id):
            raise HistoricalDataError("Historical dataset identifier is invalid")
        if stop <= first:
            raise HistoricalDataError("Historical archive range is empty")
        months: list[tuple[datetime, datetime]] = []
        current = first
        while current < stop:
            following = _next_month(current)
            months.append((current, following))
            current = following
        if len(months) > self.maximum_months:
            raise HistoricalDataError("Historical archive range exceeds the month limit")
        if output_directory.exists():
            raise HistoricalDataError("Historical dataset output directory already exists")

        archives_directory = output_directory / "archives"
        archives_directory.mkdir(parents=True)
        records: list[dict[str, object]] = []
        for month_start, month_end in months:
            filename = f"BTCUSDT-1m-{month_start:%Y-%m}.zip"
            relative = f"archives/{filename}"
            url = f"{_ORIGIN}{_ARCHIVE_PREFIX}{filename}"
            partial = archives_directory / f"{filename}.part"
            final = archives_directory / filename
            downloaded = self.downloader.download(url, partial)
            partial.rename(final)
            records.append(
                {
                    "path": relative,
                    "source_url": url,
                    "sha256": downloaded.sha256,
                    "timestamp_unit": (
                        "milliseconds" if month_end <= _MICROSECOND_CUTOFF else "microseconds"
                    ),
                    "coverage_start": month_start.isoformat().replace("+00:00", "Z"),
                    "coverage_end": month_end.isoformat().replace("+00:00", "Z"),
                    "row_count": int((month_end - month_start).total_seconds() // 60),
                }
            )

        payload = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1m",
            "coverage_start": first.isoformat().replace("+00:00", "Z"),
            "coverage_end": stop.isoformat().replace("+00:00", "Z"),
            "source_origin": _ORIGIN,
            "exhaustive_candidate_scan": True,
            "excluded_features": [
                "historical_futures_candles",
                "historical_funding",
                "historical_open_interest",
                "historical_taker_volume",
                "historical_order_book",
                "historical_liquidations",
            ],
            "archives": records,
        }
        temporary_manifest = output_directory / "manifest.json.part"
        final_manifest = output_directory / "manifest.json"
        temporary_manifest.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = self.loader.validate(temporary_manifest)
        temporary_manifest.rename(final_manifest)
        return HistoricalArchiveBuild(
            final_manifest,
            dataset_id,
            archive_count=len(records),
            candle_count=summary.candle_count,
        )

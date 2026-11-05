"""Uniform daily-archive acquisition, strictly limited to the frozen 2021 experiment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

from btc_sentinel.backtesting.archive_fetch import (
    ArchiveDownloader,
    HistoricalArchiveBuild,
    UrllibArchiveDownloader,
    _inspect_archive,
)
from btc_sentinel.backtesting.dataset import (
    HistoricalDataError,
    HistoricalDatasetLoader,
    TimestampUnit,
)

START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2022, 1, 1, tzinfo=UTC)


class DevelopmentDailyBuilder:
    def __init__(self, downloader: ArchiveDownloader | None = None, *, workers: int = 4) -> None:
        if not 1 <= workers <= 4:
            raise ValueError("Daily development acquisition allows one to four workers")
        self.downloader = downloader
        self.workers = workers

    def _acquire_day(self, day: datetime, directory: Path) -> dict[str, object]:
        if not START <= day < END:
            raise HistoricalDataError("Daily archive is outside the frozen 2021 development year")
        name = f"BTCUSDT-1m-{day:%Y-%m-%d}.zip"
        url = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{name}"
        downloader = self.downloader or UrllibArchiveDownloader(
            archive_frequency="daily", maximum_bytes=5_000_000
        )
        partial = directory / f"{name}.part"
        downloaded = downloader.download(url, partial)
        final = directory / name
        partial.rename(final)
        checksum_path = directory / f"{name}.CHECKSUM"
        downloader.download(f"{url}.CHECKSUM", checksum_path)
        if checksum_path.stat().st_size > 1024:
            raise HistoricalDataError("Daily publisher checksum exceeds its size limit")
        try:
            declared = checksum_path.read_text(encoding="utf-8").split()
        except UnicodeDecodeError as exc:
            raise HistoricalDataError("Daily publisher checksum is not UTF-8") from exc
        if declared != [downloaded.sha256, name]:
            raise HistoricalDataError("Daily publisher checksum disagrees with the archive")
        inspection = _inspect_archive(
            final,
            name.removesuffix(".zip") + ".csv",
            TimestampUnit.MILLISECONDS,
            day,
            day + timedelta(days=1),
        )
        return {
            "path": f"archives/{name}",
            "source_url": url,
            "sha256": downloaded.sha256,
            "timestamp_unit": "milliseconds",
            "coverage_start": day.isoformat().replace("+00:00", "Z"),
            "coverage_end": (day + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "row_count": inspection.row_count,
            "close_time_anomalies": list(inspection.close_time_anomalies),
            "missing_intervals": list(inspection.missing_intervals),
        }

    def build(self, output: Path, dataset_id: str) -> HistoricalArchiveBuild:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", dataset_id):
            raise HistoricalDataError("Daily development dataset identity is invalid")
        if output.exists():
            raise HistoricalDataError("Daily development output directory already exists")
        archives = output / "archives"
        archives.mkdir(parents=True)
        days = [START + timedelta(days=index) for index in range((END - START).days)]
        records = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(self._acquire_day, day, archives) for day in days]
            try:
                for future in as_completed(futures):
                    records.append(future.result())
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        records.sort(key=lambda row: str(row["coverage_start"]))
        payload = {
            "schema_version": 2,
            "dataset_id": dataset_id,
            "symbol": "BTCUSDT",
            "venue": "SPOT",
            "interval": "1m",
            "source_origin": "https://data.binance.vision",
            "coverage_start": "2021-01-01T00:00:00Z",
            "coverage_end": "2022-01-01T00:00:00Z",
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
        partial = output / "manifest.json.part"
        partial.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        summary = HistoricalDatasetLoader().validate(partial)
        final = output / "manifest.json"
        partial.rename(final)
        return HistoricalArchiveBuild(final, dataset_id, len(records), summary.candle_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire the fixed full 2021 daily-minute dataset")
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = DevelopmentDailyBuilder().build(args.output, args.dataset_id)
    except Exception as exc:
        print(
            json.dumps({"event": "development_daily_acquisition_failed", "error": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "event": "development_daily_acquisition_completed",
                "manifest": str(result.manifest_path),
                "archive_count": result.archive_count,
                "candle_count": result.candle_count,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

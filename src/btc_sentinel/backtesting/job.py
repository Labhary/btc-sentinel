"""Command-line validation boundary for a local historical dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from btc_sentinel.backtesting.dataset import HistoricalDatasetLoader
from btc_sentinel.time_utils import iso_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate immutable BTCUSDT history")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        dataset = HistoricalDatasetLoader().validate(args.manifest)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "historical_dataset_rejected",
                    "error_name": type(exc).__name__,
                    "reason": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "event": "historical_dataset_validated",
                "dataset_id": dataset.manifest.dataset_id,
                "manifest_sha256": dataset.manifest_sha256,
                "coverage_start": iso_utc(dataset.manifest.coverage_start),
                "coverage_end": iso_utc(dataset.manifest.coverage_end),
                "archive_count": len(dataset.manifest.archives),
                "candle_count": dataset.candle_count,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

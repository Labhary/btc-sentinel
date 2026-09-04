"""CLI for reproducible official Binance Vision history acquisition."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.archive_fetch import BinanceVisionArchiveBuilder


def _month(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise argparse.ArgumentTypeError("month must use YYYY-MM")
    try:
        return datetime.strptime(value, "%Y-%m").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch official monthly BTCUSDT history")
    parser.add_argument("start_month", type=_month)
    parser.add_argument("end_month", type=_month, help="exclusive month")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = BinanceVisionArchiveBuilder().build(
            args.start_month,
            args.end_month,
            args.output_directory,
            args.dataset_id,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "historical_archives_failed",
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
                "event": "historical_archives_built",
                "dataset_id": result.dataset_id,
                "manifest": str(result.manifest_path),
                "archive_count": result.archive_count,
                "candle_count": result.candle_count,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

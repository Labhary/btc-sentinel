"""CLI for official native BTCUSDT monthly-candle acquisition."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from btc_sentinel.backtesting.archive_job import _month
from btc_sentinel.backtesting.monthly_dataset import BinanceVisionMonthlyBuilder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch native Binance BTCUSDT 1mo history")
    parser.add_argument("start_month", type=_month)
    parser.add_argument("end_month", type=_month, help="exclusive month")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = BinanceVisionMonthlyBuilder().build(
            args.start_month,
            args.end_month,
            args.output_directory,
            args.dataset_id,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "native_monthly_archives_failed",
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
                "event": "native_monthly_archives_built",
                "dataset_id": result.dataset_id,
                "manifest": str(result.manifest_path),
                "archive_count": result.archive_count,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

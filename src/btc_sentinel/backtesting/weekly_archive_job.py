"""Acquire native weekly warm-up only inside the frozen development bounds."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.weekly_dataset import BinanceVisionWeeklyBuilder


def _month(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch frozen pre-2022 native weekly warm-up")
    parser.add_argument("start", type=_month)
    parser.add_argument("end", type=_month)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        path = BinanceVisionWeeklyBuilder().build(
            args.start, args.end, args.output, args.dataset_id
        )
    except Exception as exc:
        print(
            json.dumps({"event": "native_weekly_archives_failed", "error": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "event": "native_weekly_archives_built",
                "manifest": str(path),
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Diagnose pre-2022 weekly source disagreement without importing or evaluating signals."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.replay import HistoricalReplayStore
from btc_sentinel.backtesting.weekly_dataset import NativeWeeklyLoader, NativeWeeklyOverlapError
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval


def diagnose(market_manifest: Path, weekly_manifest: Path) -> dict[str, object]:
    with (
        tempfile.TemporaryDirectory(prefix="btc-weekly-overlap-") as directory,
        HistoricalReplayStore(Path(directory) / "market.sqlite3") as store,
    ):
        market = store.import_manifest(market_manifest)
        if store.coverage() != (
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2022, 1, 1, tzinfo=UTC),
        ):
            raise DomainValidationError(
                "Weekly overlap diagnostic requires the fixed 2021 market range"
            )
        candles = []
        weekly = NativeWeeklyLoader().visit(weekly_manifest, candles.append)
        overlap_count = 0
        conflicts = []
        for native in candles:
            try:
                derived = store.series_at(
                    MarketInterval.ONE_WEEK,
                    MarketInterval.ONE_WEEK.next_open_time(native.open_time),
                    limit=1,
                ).latest
            except DomainValidationError:
                continue
            if derived.open_time != native.open_time:
                continue
            overlap_count += 1
            if derived != native:
                conflict = NativeWeeklyOverlapError(native, derived, weekly.manifest_sha256)
                conflicts.append(
                    {
                        "week_open": native.open_time.isoformat(),
                        "differences": conflict.differences,
                    }
                )
        return {
            "event": "weekly_overlap_diagnostic",
            "market_manifest_sha256": market.manifest_sha256,
            "weekly_manifest_sha256": weekly.manifest_sha256,
            "overlap_count": overlap_count,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "performance_verdict": "NOT_RUN",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose frozen development weekly overlap only")
    parser.add_argument("market_manifest", type=Path)
    parser.add_argument("weekly_manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        report = diagnose(args.market_manifest, args.weekly_manifest)
    except Exception as exc:
        print(
            json.dumps({"event": "weekly_overlap_diagnostic_failed", "error": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

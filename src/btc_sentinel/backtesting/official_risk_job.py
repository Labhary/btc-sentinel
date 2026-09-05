"""CLI boundary for conservative official historical-risk reconstruction."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.official_risk_fetch import OfficialRiskArchiveBuilder


def _year(value: str) -> datetime:
    try:
        year = int(value)
        return datetime(year, 1, 1, tzinfo=UTC)
    except (ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("year must be a four-digit calendar year") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build immutable historical risk evidence from official archives"
    )
    parser.add_argument("--start-year", required=True, type=_year)
    parser.add_argument("--end-year", required=True, type=_year)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dataset-id", required=True)
    arguments = parser.parse_args()
    try:
        result = OfficialRiskArchiveBuilder().build(
            arguments.start_year,
            arguments.end_year,
            arguments.output,
            arguments.dataset_id,
        )
    except Exception as exc:  # CLI must fail closed without a traceback or secret-bearing body.
        print(json.dumps({"event": "official_risk_archive_failed", "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "event": "official_risk_archive_built",
                "dataset_id": result.dataset_id,
                "manifest": str(result.manifest_path),
                "artifact_count": result.artifact_count,
                "record_count": result.record_count,
                "coverage_gap_count": result.coverage_gap_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

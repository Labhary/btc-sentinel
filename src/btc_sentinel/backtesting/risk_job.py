"""Command-line validation boundary for point-in-time historical risk."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from btc_sentinel.backtesting.risk_history import HistoricalRiskStore
from btc_sentinel.time_utils import iso_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate immutable BTC risk history")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="btc-sentinel-risk-") as directory,
            HistoricalRiskStore(Path(directory) / "risk.sqlite3") as store,
        ):
            summary = store.import_manifest(args.manifest)
            source_coverage = store.source_coverage
            excluded_features = store.excluded_features
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "historical_risk_rejected",
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
                "event": "historical_risk_validated",
                "dataset_id": summary.dataset_id,
                "manifest_sha256": summary.manifest_sha256,
                "coverage_start": iso_utc(summary.coverage_start),
                "coverage_end": iso_utc(summary.coverage_end),
                "point_count": summary.point_count,
                "source_coverage": source_coverage,
                "excluded_features": excluded_features,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

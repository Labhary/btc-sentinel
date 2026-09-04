"""CLI for deterministic historical risk-timeline derivation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from btc_sentinel.backtesting.risk_derivation import HistoricalRiskTimelineBuilder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive immutable BTC risk history")
    parser.add_argument("evidence_manifest", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = HistoricalRiskTimelineBuilder().build(
            args.evidence_manifest,
            args.output_directory,
            args.dataset_id,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "historical_risk_derivation_failed",
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
                "event": "historical_risk_derived",
                "dataset_id": result.dataset_id,
                "manifest": str(result.manifest_path),
                "manifest_sha256": result.manifest_sha256,
                "point_count": result.point_count,
                "performance_verdict": "NOT_RUN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Executable development-only audit of complete analysis input readiness."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.historical_runner import HistoricalReplayRunner
from btc_sentinel.backtesting.replay import HistoricalReplayStore
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.time_utils import iso_utc


def _utc_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _run(args: argparse.Namespace) -> dict[str, object]:
    with ExitStack() as stack:
        if args.work_directory is None:
            directory = Path(
                stack.enter_context(tempfile.TemporaryDirectory(prefix="btc-sentinel-readiness-"))
            )
        else:
            directory = args.work_directory.resolve()
            directory.mkdir(parents=True, exist_ok=True)
        database = directory / "market-readiness.sqlite3"
        if database.exists():
            raise DomainValidationError("Historical readiness database path already exists")
        store = stack.enter_context(HistoricalReplayStore(database))
        market = store.import_manifest(args.market_manifest)
        monthly = (
            None
            if args.monthly_manifest is None
            else store.import_native_monthly_manifest(args.monthly_manifest)
        )
        weekly = (
            None
            if args.weekly_manifest is None
            else store.import_native_weekly_manifest(args.weekly_manifest)
        )
        audit = HistoricalReplayRunner().audit_analysis_readiness(
            store,
            args.start,
            args.end,
        )
        ready = audit.analysis_ready_candidate_count
        return {
            "event": "historical_analysis_readiness_completed",
            "market_dataset_id": market.dataset_id,
            "market_manifest_sha256": market.manifest_sha256,
            "native_monthly_dataset_id": None if monthly is None else monthly.dataset_id,
            "native_monthly_manifest_sha256": (
                None if monthly is None else monthly.manifest_sha256
            ),
            "native_weekly_dataset_id": None if weekly is None else weekly.dataset_id,
            "native_weekly_manifest_sha256": None if weekly is None else weekly.manifest_sha256,
            "native_weekly_overlap_checked_count": (
                None if weekly is None else weekly.overlap_checked_count
            ),
            "coverage_start": iso_utc(audit.coverage_start),
            "coverage_end": iso_utc(audit.coverage_end),
            "evaluated_boundaries": audit.candidate_count,
            "analysis_ready_boundaries": ready,
            "analysis_ready_first_boundary": (
                None
                if audit.analysis_ready_first_boundary is None
                else iso_utc(audit.analysis_ready_first_boundary)
            ),
            "analysis_ready_last_boundary": (
                None
                if audit.analysis_ready_last_boundary is None
                else iso_utc(audit.analysis_ready_last_boundary)
            ),
            "rejection_counts": dict(audit.rejection_counts),
            "performance_verdict": "NOT_RUN",
            "stop_reason": (
                "NO_ANALYSIS_READY_BOUNDARIES"
                if ready == 0
                else "ANALYSIS_INPUTS_READY_FOR_SEPARATE_RISK_VALIDATION"
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit historical analysis readiness without signal evaluation"
    )
    parser.add_argument("market_manifest", type=Path)
    parser.add_argument("start", type=_utc_argument)
    parser.add_argument("end", type=_utc_argument)
    parser.add_argument("--monthly-manifest", type=Path)
    parser.add_argument("--weekly-manifest", type=Path)
    parser.add_argument("--work-directory", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(
            json.dumps(
                {"event": "historical_analysis_readiness_failed", "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

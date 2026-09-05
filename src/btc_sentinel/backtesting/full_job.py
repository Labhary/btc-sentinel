"""Executable exhaustive historical replay over immutable market and risk inputs."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from btc_sentinel.backtesting.historical_runner import HistoricalReplayRunner
from btc_sentinel.backtesting.models import BacktestReport
from btc_sentinel.backtesting.replay import HistoricalReplayStore
from btc_sentinel.backtesting.risk_history import HistoricalRiskStore
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


def _report_payload(report: BacktestReport) -> dict[str, object]:
    return {
        "variant": report.variant.value,
        "verdict": report.verdict.value,
        "statistics": report.statistics.as_payload(),
        "walk_forward_folds": len(report.folds),
        "candidate_signals": report.candidate_count,
        "no_fill": report.no_fill_count,
        "unresolved": report.unresolved_count,
        "reasons": report.reasons,
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    with ExitStack() as stack:
        if args.work_directory is None:
            directory = Path(
                stack.enter_context(tempfile.TemporaryDirectory(prefix="btc-sentinel-replay-"))
            )
        else:
            directory = args.work_directory.resolve()
            directory.mkdir(parents=True, exist_ok=True)
        market_database = directory / "market-replay.sqlite3"
        risk_database = directory / "risk-replay.sqlite3"
        if market_database.exists() or risk_database.exists():
            raise DomainValidationError("Historical replay database path already exists")

        market_store = stack.enter_context(HistoricalReplayStore(market_database))
        risk_store = stack.enter_context(HistoricalRiskStore(risk_database))
        market_summary = market_store.import_manifest(args.market_manifest)
        monthly_summary = (
            None
            if args.monthly_manifest is None
            else market_store.import_native_monthly_manifest(args.monthly_manifest)
        )
        risk_summary = risk_store.import_manifest(args.risk_manifest)
        risk_start, risk_end = risk_store.coverage()
        if risk_start > args.start or risk_end < args.end:
            raise DomainValidationError("Historical risk coverage does not contain replay range")

        run = HistoricalReplayRunner().run(market_store, args.start, args.end, risk_store)
        comparison = run.evaluate(args.end)
        return {
            "event": "historical_replay_completed",
            "market_dataset_id": market_summary.dataset_id,
            "market_manifest_sha256": market_summary.manifest_sha256,
            "native_monthly_dataset_id": (
                None if monthly_summary is None else monthly_summary.dataset_id
            ),
            "native_monthly_manifest_sha256": (
                None if monthly_summary is None else monthly_summary.manifest_sha256
            ),
            "risk_dataset_id": risk_summary.dataset_id,
            "risk_manifest_sha256": risk_summary.manifest_sha256,
            "coverage_start": iso_utc(args.start),
            "coverage_end": iso_utc(args.end),
            "evaluated_boundaries": run.candidate_count,
            "created_signals": run.created_signal_count,
            "rejection_counts": dict(run.rejection_counts),
            "fixed": _report_payload(comparison.fixed),
            "managed": _report_payload(comparison.managed),
            "completed_pairs": comparison.completed_pairs,
            "average_managed_delta_r": (
                None
                if comparison.average_managed_delta_r is None
                else str(comparison.average_managed_delta_r)
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run exhaustive BTCUSDT historical replay")
    parser.add_argument("market_manifest", type=Path)
    parser.add_argument("risk_manifest", type=Path)
    parser.add_argument("start", type=_utc_argument)
    parser.add_argument("end", type=_utc_argument)
    parser.add_argument("--monthly-manifest", type=Path)
    parser.add_argument("--work-directory", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "historical_replay_failed",
                    "error_name": type(exc).__name__,
                    "reason": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

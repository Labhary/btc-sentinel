import argparse
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from btc_sentinel.backtesting.full_job import _utc_argument, main

START = datetime(2024, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


class FakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def import_manifest(self, path: Path):
        if "risk" in path.name:
            return SimpleNamespace(dataset_id="risk-v1", manifest_sha256="b" * 64)
        return SimpleNamespace(dataset_id="market-v1", manifest_sha256="a" * 64)

    def coverage(self):
        return START, END


class FakeStatistics:
    def as_payload(self):
        return {"resolved": 100, "strict_win_rate_percent": "61"}


def _report(variant: str):
    return SimpleNamespace(
        variant=SimpleNamespace(value=variant),
        verdict=SimpleNamespace(value="PASSED"),
        statistics=FakeStatistics(),
        folds=(1, 2, 3),
        candidate_count=100,
        no_fill_count=0,
        unresolved_count=0,
        reasons=(),
    )


class FakeRun:
    candidate_count = 120
    created_signal_count = 100
    rejection_counts = (("score below threshold", 20),)

    def evaluate(self, generated_at: datetime):
        if generated_at != END:
            raise AssertionError("unexpected evaluation time")
        return SimpleNamespace(
            fixed=_report("FIXED"),
            managed=_report("MANAGED"),
            completed_pairs=100,
            average_managed_delta_r=None,
        )


class FakeRunner:
    def run(self, market_store, start, end, risk_store):
        if (start, end) != (START, END) or market_store is risk_store:
            raise AssertionError("unexpected runner inputs")
        return FakeRun()


class HistoricalFullJobTests(TestCase):
    def test_runs_both_immutable_inputs_and_emits_machine_readable_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            arguments = [
                str(Path(directory) / "market.json"),
                str(Path(directory) / "risk.json"),
                START.isoformat(),
                END.isoformat(),
            ]
            with (
                patch("btc_sentinel.backtesting.full_job.HistoricalReplayStore", FakeStore),
                patch("btc_sentinel.backtesting.full_job.HistoricalRiskStore", FakeStore),
                patch("btc_sentinel.backtesting.full_job.HistoricalReplayRunner", FakeRunner),
                redirect_stdout(output),
            ):
                result = main(arguments)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["event"], "historical_replay_completed")
        self.assertEqual(payload["market_dataset_id"], "market-v1")
        self.assertEqual(payload["risk_dataset_id"], "risk-v1")
        self.assertEqual(payload["managed"]["verdict"], "PASSED")
        self.assertEqual(payload["managed"]["statistics"]["strict_win_rate_percent"], "61")

    def test_existing_database_fails_closed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "market-replay.sqlite3").write_text("preserve", encoding="utf-8")
            error = StringIO()
            arguments = [
                str(root / "market.json"),
                str(root / "risk.json"),
                START.isoformat(),
                END.isoformat(),
                "--work-directory",
                str(root),
            ]
            with redirect_stderr(error):
                result = main(arguments)

            self.assertEqual((root / "market-replay.sqlite3").read_text(), "preserve")

        self.assertEqual(result, 1)
        self.assertEqual(json.loads(error.getvalue())["event"], "historical_replay_failed")

    def test_timestamp_argument_requires_timezone_and_normalizes_to_utc(self) -> None:
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "timezone"):
            _utc_argument("2024-01-01T00:00:00")
        self.assertEqual(_utc_argument("2024-01-01T01:00:00+01:00"), START)

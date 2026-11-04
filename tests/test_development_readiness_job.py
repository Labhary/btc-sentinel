import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from btc_sentinel.backtesting.readiness_job import main

START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2022, 1, 1, tzinfo=UTC)


class FakeStore:
    def __init__(self, _path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def import_manifest(self, _path):
        return SimpleNamespace(dataset_id="market", manifest_sha256="a" * 64)

    def import_native_monthly_manifest(self, _path):
        return SimpleNamespace(dataset_id="monthly", manifest_sha256="b" * 64)

    def import_native_weekly_manifest(self, _path):
        return SimpleNamespace(
            dataset_id="weekly", manifest_sha256="c" * 64, overlap_checked_count=40
        )


class FakeRunner:
    def audit_analysis_readiness(self, _store, start, end):
        if (start, end) != (START, END):
            raise AssertionError("wrong readiness range")
        return SimpleNamespace(
            coverage_start=start,
            coverage_end=end,
            candidate_count=100,
            analysis_ready_candidate_count=10,
            analysis_ready_first_boundary=datetime(2021, 10, 1, tzinfo=UTC),
            analysis_ready_last_boundary=datetime(2021, 12, 31, tzinfo=UTC),
            rejection_counts=(("warm-up", 90),),
        )


class DevelopmentReadinessJobTests(TestCase):
    def test_emits_weekly_provenance_without_risk_signals_or_performance(self) -> None:
        output = StringIO()
        with (
            patch("btc_sentinel.backtesting.readiness_job.HistoricalReplayStore", FakeStore),
            patch("btc_sentinel.backtesting.readiness_job.HistoricalReplayRunner", FakeRunner),
            redirect_stdout(output),
        ):
            result = main(
                [
                    "market.json",
                    START.isoformat(),
                    END.isoformat(),
                    "--monthly-manifest",
                    "monthly.json",
                    "--weekly-manifest",
                    "weekly.json",
                ]
            )
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["native_weekly_manifest_sha256"], "c" * 64)
        self.assertEqual(report["native_weekly_overlap_checked_count"], 40)
        self.assertEqual(report["analysis_ready_boundaries"], 10)
        self.assertEqual(report["performance_verdict"], "NOT_RUN")
        self.assertEqual(
            report["stop_reason"], "ANALYSIS_INPUTS_READY_FOR_SEPARATE_RISK_VALIDATION"
        )
        self.assertTrue(
            {"created_signals", "fixed", "managed", "risk_dataset_id"}.isdisjoint(report)
        )

    def test_optional_weekly_input_does_not_change_original_audit_contract(self) -> None:
        output = StringIO()
        with (
            patch("btc_sentinel.backtesting.readiness_job.HistoricalReplayStore", FakeStore),
            patch("btc_sentinel.backtesting.readiness_job.HistoricalReplayRunner", FakeRunner),
            redirect_stdout(output),
        ):
            result = main(["market.json", START.isoformat(), END.isoformat()])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertIsNone(report["native_weekly_dataset_id"])
        self.assertIsNone(report["native_weekly_overlap_checked_count"])
        self.assertEqual(report["performance_verdict"], "NOT_RUN")

    def test_refuses_to_overwrite_readiness_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market-readiness.sqlite3"
            database.write_text("preserve")
            error = StringIO()
            with redirect_stderr(error):
                result = main(
                    [
                        "market.json",
                        START.isoformat(),
                        END.isoformat(),
                        "--work-directory",
                        directory,
                    ]
                )
            self.assertEqual(result, 1)
            self.assertEqual(database.read_text(), "preserve")
            self.assertIn("already exists", json.loads(error.getvalue())["error"])

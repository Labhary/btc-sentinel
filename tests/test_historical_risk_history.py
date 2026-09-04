import hashlib
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting.risk_history import (
    HistoricalDataError,
    HistoricalRiskStore,
    parse_risk_manifest,
)
from btc_sentinel.backtesting.risk_job import main
from btc_sentinel.news.models import RiskDecision

START = datetime(2024, 1, 1, tzinfo=UTC)
SOURCES = ("fed_monetary", "sec_releases", "bls_calendar")


def _point(
    evaluated_at: datetime,
    *,
    decision: str = "CLEAR",
    block_until: datetime | None = None,
    reasons: list[str] | None = None,
    issues: list[dict[str, object]] | None = None,
    source_ids: list[str] | None = None,
    observed: list[datetime] | None = None,
) -> dict[str, object]:
    return {
        "evaluated_at": evaluated_at.isoformat(),
        "decision": decision,
        "block_until": None if block_until is None else block_until.isoformat(),
        "reasons": reasons or [],
        "coverage_issues": issues or [],
        "source_ids": source_ids or [],
        "evidence_observed_at": [item.isoformat() for item in observed or []],
    }


class HistoricalRiskHistoryTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(
        self,
        points: list[dict[str, object]],
        *,
        sources: tuple[str, ...] = SOURCES,
        checksum: str | None = None,
        points_path: str = "risk-points.jsonl",
    ) -> Path:
        content = b"".join(
            json.dumps(point, separators=(",", ":")).encode() + b"\n" for point in points
        )
        (self.root / "risk-points.jsonl").write_bytes(content)
        end = START + timedelta(minutes=15 * len(points))
        payload = {
            "schema_version": 1,
            "dataset_id": "official-risk-test-v1",
            "coverage_start": START.isoformat(),
            "coverage_end": end.isoformat(),
            "interval": "15m",
            "derivation_version": "test-v1",
            "source_coverage": list(sources),
            "excluded_features": ["historical_coinbase_status"],
            "points_path": points_path,
            "points_sha256": checksum or hashlib.sha256(content).hexdigest(),
            "point_count": len(points),
        }
        path = self.root / "risk-manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_imports_continuous_timeline_and_queries_exact_point(self) -> None:
        points = [
            _point(START),
            _point(
                START + timedelta(minutes=15),
                decision="BLOCK",
                block_until=START + timedelta(hours=1),
                reasons=["scheduled macro release window"],
                source_ids=["bls_calendar"],
                observed=[START],
            ),
        ]
        path = self._manifest(points)

        with HistoricalRiskStore(self.root / "risk.sqlite3") as store:
            summary = store.import_manifest(path)
            assessment = store.assessment_at(START + timedelta(minutes=15))
            retained = store.connection.execute(
                "SELECT source_ids, evidence_observed_at FROM risk_points WHERE evaluated_at = ?",
                ((START + timedelta(minutes=15)).isoformat(),),
            ).fetchone()

        self.assertEqual(summary.point_count, 2)
        self.assertEqual(summary.coverage_start, START)
        self.assertIs(assessment.decision, RiskDecision.BLOCK)
        self.assertEqual(assessment.block_until, START + timedelta(hours=1))
        self.assertEqual(json.loads(retained[0]), ["bls_calendar"])
        self.assertEqual(json.loads(retained[1]), [START.isoformat()])

    def test_manifest_requires_every_official_source_and_safe_path(self) -> None:
        point = _point(START)
        with self.assertRaisesRegex(HistoricalDataError, "required official source"):
            self._manifest([point], sources=("fed_monetary", "sec_releases")).read_bytes()
            parse_risk_manifest((self.root / "risk-manifest.json").read_bytes())

        path = self._manifest([point], points_path="../risk-points.jsonl")
        with self.assertRaisesRegex(HistoricalDataError, "safe JSONL path"):
            parse_risk_manifest(path.read_bytes())

    def test_checksum_failure_rolls_back_all_rows(self) -> None:
        path = self._manifest([_point(START)], checksum="0" * 64)
        with HistoricalRiskStore(self.root / "risk.sqlite3") as store:
            with self.assertRaisesRegex(HistoricalDataError, "checksum mismatch"):
                store.import_manifest(path)
            point_count = store.connection.execute("SELECT COUNT(*) FROM risk_points").fetchone()
            metadata_count = store.connection.execute(
                "SELECT COUNT(*) FROM risk_metadata"
            ).fetchone()
        self.assertEqual(point_count, (0,))
        self.assertEqual(metadata_count, (0,))

    def test_rejects_gaps_future_evidence_and_undeclared_sources(self) -> None:
        invalid_cases = (
            (
                [_point(START), _point(START + timedelta(minutes=30))],
                "gapped or unordered",
            ),
            (
                [_point(START, observed=[START + timedelta(seconds=1)])],
                "future evidence",
            ),
            (
                [_point(START, source_ids=["unknown_source"])],
                "undeclared source",
            ),
        )
        for index, (points, message) in enumerate(invalid_cases):
            with self.subTest(message=message):
                path = self._manifest(points)
                with (
                    HistoricalRiskStore(self.root / f"risk-{index}.sqlite3") as store,
                    self.assertRaisesRegex(HistoricalDataError, message),
                ):
                    store.import_manifest(path)

    def test_required_coverage_issue_must_block_and_name_declared_source(self) -> None:
        required_issue = {
            "source_id": "fed_monetary",
            "detail": "archive unavailable",
            "required": True,
        }
        invalid_cases = (
            ([_point(START, issues=[required_issue])], "must block"),
            (
                [
                    _point(
                        START,
                        decision="BLOCK",
                        reasons=["missing coverage"],
                        issues=[{**required_issue, "source_id": "unknown_source"}],
                    )
                ],
                "undeclared source",
            ),
        )
        for index, (points, message) in enumerate(invalid_cases):
            with self.subTest(message=message):
                path = self._manifest(points)
                with (
                    HistoricalRiskStore(self.root / f"issue-{index}.sqlite3") as store,
                    self.assertRaisesRegex(HistoricalDataError, message),
                ):
                    store.import_manifest(path)

    def test_block_window_is_consistent_with_decision_and_time(self) -> None:
        invalid_cases = (
            (
                [_point(START, block_until=START + timedelta(hours=1))],
                "Only a blocking",
            ),
            (
                [
                    _point(
                        START,
                        decision="BLOCK",
                        block_until=START,
                        reasons=["risk window"],
                    )
                ],
                "must be after",
            ),
        )
        for index, (points, message) in enumerate(invalid_cases):
            with self.subTest(message=message):
                path = self._manifest(points)
                with (
                    HistoricalRiskStore(self.root / f"block-{index}.sqlite3") as store,
                    self.assertRaisesRegex(HistoricalDataError, message),
                ):
                    store.import_manifest(path)

    def test_store_cannot_be_reinitialized_or_queried_at_missing_time(self) -> None:
        path = self._manifest([_point(START)])
        with HistoricalRiskStore(self.root / "risk.sqlite3") as store:
            store.import_manifest(path)
            with self.assertRaisesRegex(HistoricalDataError, "already been initialized"):
                store.import_manifest(path)
            with self.assertRaisesRegex(HistoricalDataError, "no exact candidate point"):
                store.assessment_at(START + timedelta(minutes=15))

    def test_duplicate_and_unknown_manifest_fields_are_rejected(self) -> None:
        duplicate = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(HistoricalDataError, "Duplicate"):
            parse_risk_manifest(duplicate)

        path = self._manifest([_point(START)])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        with self.assertRaisesRegex(HistoricalDataError, r"unknown=\['unexpected'\]"):
            parse_risk_manifest(json.dumps(payload).encode())

    def test_validation_command_reports_not_run_and_fails_closed(self) -> None:
        path = self._manifest([_point(START)])
        output = StringIO()
        with redirect_stdout(output):
            result = main([str(path)])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["event"], "historical_risk_validated")
        self.assertEqual(payload["performance_verdict"], "NOT_RUN")
        self.assertEqual(payload["source_coverage"], list(SOURCES))

        error = StringIO()
        with redirect_stderr(error):
            rejected = main([str(self.root / "missing.json")])
        self.assertEqual(rejected, 1)
        self.assertEqual(json.loads(error.getvalue())["event"], "historical_risk_rejected")

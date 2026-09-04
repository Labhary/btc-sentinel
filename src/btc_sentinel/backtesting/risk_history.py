"""Immutable point-in-time news and macro risk timeline for historical replay."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.news.models import CoverageIssue, RiskAssessment, RiskDecision
from btc_sentinel.news.sources import OFFICIAL_FEEDS
from btc_sentinel.time_utils import ensure_utc

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REQUIRED_SOURCES = frozenset(source.source_id for source in OFFICIAL_FEEDS if source.required)
_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_id",
    "coverage_start",
    "coverage_end",
    "interval",
    "derivation_version",
    "source_coverage",
    "excluded_features",
    "points_path",
    "points_sha256",
    "point_count",
}
_POINT_FIELDS = {
    "evaluated_at",
    "decision",
    "block_until",
    "reasons",
    "coverage_issues",
    "source_ids",
    "evidence_observed_at",
}
_ISSUE_FIELDS = {"source_id", "detail", "required"}
_MAXIMUM_POINTS_BYTES = 512_000_000


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalDataError(f"Duplicate historical risk JSON field: {key}")
        result[key] = value
    return result


def _fields(value: dict[str, object], expected: set[str], name: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        raise HistoricalDataError(
            f"{name} fields are invalid: missing={missing}, unknown={unknown}"
        )


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDataError(f"{name} must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataError(f"{name} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalDataError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise HistoricalDataError(f"{name} must be an array of non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise HistoricalDataError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class HistoricalRiskManifest:
    dataset_id: str
    coverage_start: datetime
    coverage_end: datetime
    derivation_version: str
    source_coverage: tuple[str, ...]
    excluded_features: tuple[str, ...]
    points_path: str
    points_sha256: str
    point_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage_start", ensure_utc(self.coverage_start))
        object.__setattr__(self, "coverage_end", ensure_utc(self.coverage_end))
        if not _DATASET_ID.fullmatch(self.dataset_id) or not self.derivation_version:
            raise HistoricalDataError("Historical risk dataset identity is invalid")
        if self.coverage_end <= self.coverage_start:
            raise HistoricalDataError("Historical risk coverage is invalid")
        if self.coverage_start.minute % 15 or self.coverage_end.minute % 15:
            raise HistoricalDataError("Historical risk coverage must align to 15-minute UTC bounds")
        if self.coverage_start.second or self.coverage_end.second:
            raise HistoricalDataError("Historical risk coverage must be second-aligned")
        if self.coverage_start.microsecond or self.coverage_end.microsecond:
            raise HistoricalDataError("Historical risk coverage must be microsecond-aligned")
        path = PurePosixPath(self.points_path)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".jsonl":
            raise HistoricalDataError("Historical risk points_path must be a safe JSONL path")
        if not _SHA256.fullmatch(self.points_sha256):
            raise HistoricalDataError("Historical risk points SHA-256 is invalid")
        expected = int((self.coverage_end - self.coverage_start) / timedelta(minutes=15))
        if self.point_count != expected or self.point_count < 1:
            raise HistoricalDataError("Historical risk point count does not cover every boundary")
        if not _REQUIRED_SOURCES.issubset(self.source_coverage):
            raise HistoricalDataError("Historical risk coverage omits a required official source")
        if set(self.source_coverage) & set(self.excluded_features):
            raise HistoricalDataError("Historical risk coverage and exclusions overlap")


@dataclass(frozen=True, slots=True)
class HistoricalRiskImportSummary:
    dataset_id: str
    manifest_sha256: str
    point_count: int
    coverage_start: datetime
    coverage_end: datetime


def parse_risk_manifest(raw: bytes) -> tuple[HistoricalRiskManifest, str]:
    if len(raw) > 256_000:
        raise HistoricalDataError("Historical risk manifest is too large")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalDataError("Historical risk manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise HistoricalDataError("Historical risk manifest must be an object")
    _fields(value, _MANIFEST_FIELDS, "historical risk manifest")
    if value["schema_version"] != 1 or value["interval"] != "15m":
        raise HistoricalDataError("Historical risk manifest schema or interval is unsupported")
    point_count = value["point_count"]
    if isinstance(point_count, bool) or not isinstance(point_count, int):
        raise HistoricalDataError("Historical risk point_count must be an integer")
    for name in ("dataset_id", "derivation_version", "points_path", "points_sha256"):
        if not isinstance(value[name], str):
            raise HistoricalDataError(f"Historical risk {name} must be a string")
    manifest = HistoricalRiskManifest(
        dataset_id=value["dataset_id"],
        coverage_start=_utc(value["coverage_start"], "coverage_start"),
        coverage_end=_utc(value["coverage_end"], "coverage_end"),
        derivation_version=value["derivation_version"],
        source_coverage=_strings(value["source_coverage"], "source_coverage"),
        excluded_features=_strings(value["excluded_features"], "excluded_features"),
        points_path=value["points_path"],
        points_sha256=value["points_sha256"],
        point_count=point_count,
    )
    return manifest, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class _HistoricalRiskPoint:
    assessment: RiskAssessment
    source_ids: tuple[str, ...]
    evidence_observed_at: tuple[datetime, ...]


def _parse_point(raw: str, expected_at: datetime, sources: set[str]) -> _HistoricalRiskPoint:
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise HistoricalDataError("Historical risk point is not valid JSON") from exc
    if not isinstance(value, dict):
        raise HistoricalDataError("Historical risk point must be an object")
    _fields(value, _POINT_FIELDS, "historical risk point")
    evaluated_at = _utc(value["evaluated_at"], "evaluated_at")
    if evaluated_at != expected_at:
        raise HistoricalDataError("Historical risk points are gapped or unordered")
    try:
        decision = RiskDecision(value["decision"])
    except (TypeError, ValueError) as exc:
        raise HistoricalDataError("Historical risk decision is unsupported") from exc
    block_until = (
        None if value["block_until"] is None else _utc(value["block_until"], "block_until")
    )
    if block_until is not None and decision is not RiskDecision.BLOCK:
        raise HistoricalDataError("Only a blocking historical risk point may set block_until")
    if block_until is not None and block_until <= evaluated_at:
        raise HistoricalDataError("Historical risk block_until must be after evaluated_at")
    reasons = _strings(value["reasons"], "reasons")
    source_ids = _strings(value["source_ids"], "source_ids")
    if not set(source_ids).issubset(sources):
        raise HistoricalDataError("Historical risk point cites an undeclared source")
    observed = tuple(
        _utc(item, "evidence_observed_at")
        for item in _strings(value["evidence_observed_at"], "evidence_observed_at")
    )
    if any(item > evaluated_at for item in observed):
        raise HistoricalDataError("Historical risk point contains future evidence")
    raw_issues = value["coverage_issues"]
    if not isinstance(raw_issues, list):
        raise HistoricalDataError("Historical risk coverage_issues must be an array")
    issues: list[CoverageIssue] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            raise HistoricalDataError("Historical risk coverage issue must be an object")
        _fields(item, _ISSUE_FIELDS, "historical risk coverage issue")
        if not isinstance(item["required"], bool):
            raise HistoricalDataError("Historical risk issue required flag must be boolean")
        if (
            not isinstance(item["source_id"], str)
            or not item["source_id"]
            or not isinstance(item["detail"], str)
            or not item["detail"]
        ):
            raise HistoricalDataError("Historical risk issue fields must be non-empty strings")
        if item["source_id"] not in sources:
            raise HistoricalDataError("Historical risk issue cites an undeclared source")
        issues.append(CoverageIssue(item["source_id"], item["detail"], item["required"]))
    if any(issue.required for issue in issues) and decision is not RiskDecision.BLOCK:
        raise HistoricalDataError("A required historical risk gap must block")
    return _HistoricalRiskPoint(
        RiskAssessment(
            evaluated_at=evaluated_at,
            decision=decision,
            block_until=block_until,
            events=(),
            scheduled_events=(),
            reasons=reasons,
            coverage_issues=tuple(issues),
        ),
        source_ids,
        observed,
    )


class HistoricalRiskStore:
    """Validate and query a checksum-bound 15-minute risk-decision timeline."""

    performance_eligible = True

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS risk_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_points (
                evaluated_at TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                block_until TEXT,
                reasons TEXT NOT NULL,
                coverage_issues TEXT NOT NULL,
                source_ids TEXT NOT NULL,
                evidence_observed_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def __enter__(self) -> HistoricalRiskStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.connection.close()

    def _metadata(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT value FROM risk_metadata WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise HistoricalDataError("Historical risk store is not initialized")
        return str(row[0])

    @property
    def source_coverage(self) -> tuple[str, ...]:
        return tuple(json.loads(self._metadata("source_coverage")))

    @property
    def excluded_features(self) -> tuple[str, ...]:
        return tuple(json.loads(self._metadata("excluded_features")))

    def coverage(self) -> tuple[datetime, datetime]:
        return (
            ensure_utc(datetime.fromisoformat(self._metadata("coverage_start"))),
            ensure_utc(datetime.fromisoformat(self._metadata("coverage_end"))),
        )

    def import_manifest(self, manifest_path: Path) -> HistoricalRiskImportSummary:
        if self.connection.execute("SELECT 1 FROM risk_metadata LIMIT 1").fetchone():
            raise HistoricalDataError("Historical risk store has already been initialized")
        manifest, manifest_sha = parse_risk_manifest(manifest_path.read_bytes())
        root = manifest_path.resolve().parent
        points_path = (root / manifest.points_path).resolve()
        try:
            points_path.relative_to(root)
        except ValueError as exc:
            raise HistoricalDataError("Historical risk points path escapes its root") from exc
        if not points_path.is_file():
            raise HistoricalDataError("Historical risk points file is missing")
        digest = hashlib.sha256()
        expected_at = manifest.coverage_start
        count = 0
        byte_count = 0
        try:
            self.connection.execute("BEGIN")
            with points_path.open("rb") as source:
                for binary in source:
                    byte_count += len(binary)
                    if byte_count > _MAXIMUM_POINTS_BYTES:
                        raise HistoricalDataError("Historical risk points file exceeds size limit")
                    digest.update(binary)
                    if len(binary) > 64_000:
                        raise HistoricalDataError("Historical risk point exceeds the line limit")
                    try:
                        raw = binary.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise HistoricalDataError("Historical risk points are not UTF-8") from exc
                    point = _parse_point(raw, expected_at, set(manifest.source_coverage))
                    assessment = point.assessment
                    self.connection.execute(
                        "INSERT INTO risk_points VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            assessment.evaluated_at.isoformat(),
                            assessment.decision.value,
                            None
                            if assessment.block_until is None
                            else assessment.block_until.isoformat(),
                            json.dumps(assessment.reasons, separators=(",", ":")),
                            json.dumps(
                                [
                                    {
                                        "source_id": issue.source_id,
                                        "detail": issue.detail,
                                        "required": issue.required,
                                    }
                                    for issue in assessment.coverage_issues
                                ],
                                separators=(",", ":"),
                            ),
                            json.dumps(point.source_ids, separators=(",", ":")),
                            json.dumps(
                                [item.isoformat() for item in point.evidence_observed_at],
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    count += 1
                    expected_at += timedelta(minutes=15)
            if digest.hexdigest() != manifest.points_sha256:
                raise HistoricalDataError("Historical risk points checksum mismatch")
            if count != manifest.point_count or expected_at != manifest.coverage_end:
                raise HistoricalDataError("Historical risk points do not match declared coverage")
            self.connection.executemany(
                "INSERT INTO risk_metadata VALUES (?, ?)",
                (
                    ("dataset_id", manifest.dataset_id),
                    ("manifest_sha256", manifest_sha),
                    ("coverage_start", manifest.coverage_start.isoformat()),
                    ("coverage_end", manifest.coverage_end.isoformat()),
                    ("derivation_version", manifest.derivation_version),
                    ("source_coverage", json.dumps(manifest.source_coverage)),
                    ("excluded_features", json.dumps(manifest.excluded_features)),
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return HistoricalRiskImportSummary(
            manifest.dataset_id,
            manifest_sha256=manifest_sha,
            point_count=count,
            coverage_start=manifest.coverage_start,
            coverage_end=manifest.coverage_end,
        )

    def assessment_at(self, as_of: datetime) -> RiskAssessment:
        now = ensure_utc(as_of)
        row = self.connection.execute(
            """
            SELECT decision, block_until, reasons, coverage_issues
            FROM risk_points WHERE evaluated_at = ?
            """,
            (now.isoformat(),),
        ).fetchone()
        if row is None:
            raise HistoricalDataError("Historical risk timeline has no exact candidate point")
        issues = tuple(CoverageIssue(**item) for item in json.loads(row[3]))
        return RiskAssessment(
            evaluated_at=now,
            decision=RiskDecision(row[0]),
            block_until=None if row[1] is None else datetime.fromisoformat(row[1]),
            events=(),
            scheduled_events=(),
            reasons=tuple(json.loads(row[2])),
            coverage_issues=issues,
        )

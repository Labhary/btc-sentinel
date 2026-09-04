"""Provenance-bound normalized official evidence for historical risk derivation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.news.models import (
    NewsCategory,
    RawNewsItem,
    ScheduledEvent,
    SourceSpec,
    VolatilityImpact,
)
from btc_sentinel.news.sources import OFFICIAL_FEEDS

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SOURCES = {source.source_id: source for source in OFFICIAL_FEEDS}
_REQUIRED = frozenset(source.source_id for source in OFFICIAL_FEEDS if source.required)
_TOP_FIELDS = {
    "schema_version",
    "dataset_id",
    "coverage_start",
    "coverage_end",
    "sources",
}
_SOURCE_FIELDS = {
    "source_id",
    "path",
    "sha256",
    "record_count",
    "coverage_start",
    "coverage_end",
}
_NEWS_FIELDS = {"kind", "title", "url", "published_at", "observed_at"}
_SCHEDULED_FIELDS = {
    "kind",
    "external_id",
    "title",
    "starts_at",
    "observed_at",
    "url",
}


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalDataError(f"Duplicate historical evidence field: {key}")
        result[key] = value
    return result


def _fields(value: dict[str, object], expected: set[str], name: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        raise HistoricalDataError(f"{name} fields invalid: missing={missing}, unknown={unknown}")


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDataError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataError(f"{name} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalDataError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _string(value: object, name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HistoricalDataError(f"{name} must be a non-empty string")
    return value


def _scheduled_risk(title: str) -> tuple[NewsCategory, VolatilityImpact]:
    normalized = title.casefold()
    if any(
        name in normalized
        for name in ("consumer price index", "producer price index", "employment situation")
    ):
        return NewsCategory.ECONOMIC, VolatilityImpact.HIGH
    if any(
        name in normalized for name in ("job openings and labor turnover", "employment cost index")
    ):
        return NewsCategory.ECONOMIC, VolatilityImpact.MEDIUM
    raise HistoricalDataError("Historical calendar evidence is outside the fixed risk policy")


@dataclass(frozen=True, slots=True)
class EvidenceSourceSpec:
    source: SourceSpec
    path: str
    sha256: str
    record_count: int
    coverage_start: datetime
    coverage_end: datetime

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path or path.suffix != ".jsonl":
            raise HistoricalDataError("Historical evidence path must be a safe JSONL path")
        if not _SHA256.fullmatch(self.sha256):
            raise HistoricalDataError("Historical evidence SHA-256 is invalid")
        if self.record_count < 1:
            raise HistoricalDataError("Historical evidence source must contain records")
        if self.coverage_end <= self.coverage_start:
            raise HistoricalDataError("Historical evidence source coverage is invalid")


@dataclass(frozen=True, slots=True)
class ObservedNews:
    item: RawNewsItem
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ObservedScheduledEvent:
    event: ScheduledEvent
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class HistoricalRiskEvidence:
    dataset_id: str
    manifest_sha256: str
    coverage_start: datetime
    coverage_end: datetime
    source_specs: tuple[EvidenceSourceSpec, ...]
    news: tuple[ObservedNews, ...]
    scheduled_events: tuple[ObservedScheduledEvent, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(spec.source.source_id for spec in self.source_specs)


def _source_spec(value: object, index: int) -> EvidenceSourceSpec:
    if not isinstance(value, dict):
        raise HistoricalDataError(f"sources[{index}] must be an object")
    _fields(value, _SOURCE_FIELDS, f"sources[{index}]")
    source_id = _string(value["source_id"], f"sources[{index}].source_id")
    if source_id not in _SOURCES:
        raise HistoricalDataError("Historical evidence source is not in the fixed catalog")
    count = value["record_count"]
    if isinstance(count, bool) or not isinstance(count, int):
        raise HistoricalDataError("Historical evidence record_count must be an integer")
    path = _string(value["path"], f"sources[{index}].path")
    sha256 = _string(value["sha256"], f"sources[{index}].sha256")
    assert source_id is not None and path is not None and sha256 is not None
    return EvidenceSourceSpec(
        _SOURCES[source_id],
        path,
        sha256,
        count,
        _utc(value["coverage_start"], f"sources[{index}].coverage_start"),
        _utc(value["coverage_end"], f"sources[{index}].coverage_end"),
    )


def _parse_manifest(raw: bytes) -> tuple[dict[str, object], str]:
    if len(raw) > 256_000:
        raise HistoricalDataError("Historical evidence manifest is too large")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalDataError("Historical evidence manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise HistoricalDataError("Historical evidence manifest must be an object")
    _fields(value, _TOP_FIELDS, "historical evidence manifest")
    if value["schema_version"] != 1:
        raise HistoricalDataError("Historical evidence schema is unsupported")
    return value, hashlib.sha256(raw).hexdigest()


class HistoricalRiskEvidenceLoader:
    """Validate normalized source records, hashes, coverage, and observation times."""

    def __init__(self, *, maximum_file_bytes: int = 100_000_000, maximum_records: int = 100_000):
        if maximum_file_bytes < 1 or maximum_records < 1:
            raise ValueError("Historical evidence limits must be positive")
        self.maximum_file_bytes = maximum_file_bytes
        self.maximum_records = maximum_records

    def load(self, manifest_path: Path) -> HistoricalRiskEvidence:
        value, manifest_sha = _parse_manifest(manifest_path.read_bytes())
        dataset_id = _string(value["dataset_id"], "dataset_id")
        if dataset_id is None or not _DATASET_ID.fullmatch(dataset_id):
            raise HistoricalDataError("Historical evidence dataset identifier is invalid")
        coverage_start = _utc(value["coverage_start"], "coverage_start")
        coverage_end = _utc(value["coverage_end"], "coverage_end")
        if coverage_end <= coverage_start:
            raise HistoricalDataError("Historical evidence coverage is invalid")
        if any(
            value
            for value in (
                coverage_start.minute % 15,
                coverage_start.second,
                coverage_start.microsecond,
                coverage_end.minute % 15,
                coverage_end.second,
                coverage_end.microsecond,
            )
        ):
            raise HistoricalDataError("Historical evidence coverage must align to 15 minutes")
        raw_sources = value["sources"]
        if not isinstance(raw_sources, list):
            raise HistoricalDataError("Historical evidence sources must be an array")
        specs = tuple(_source_spec(item, index) for index, item in enumerate(raw_sources))
        source_ids = tuple(spec.source.source_id for spec in specs)
        if len(set(source_ids)) != len(source_ids):
            raise HistoricalDataError("Historical evidence source IDs must be unique")
        if not _REQUIRED.issubset(source_ids):
            raise HistoricalDataError("Historical evidence omits a required official source")
        if any(
            spec.coverage_start != coverage_start or spec.coverage_end != coverage_end
            for spec in specs
        ):
            raise HistoricalDataError("Every evidence source must cover the full declared range")

        news: list[ObservedNews] = []
        scheduled: list[ObservedScheduledEvent] = []
        root = manifest_path.resolve().parent
        total_records = 0
        for spec in specs:
            path = (root / spec.path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise HistoricalDataError("Historical evidence path escapes its root") from exc
            if not path.is_file():
                raise HistoricalDataError("Historical evidence source file is missing")
            digest = hashlib.sha256()
            byte_count = 0
            source_count = 0
            with path.open("rb") as source_file:
                for line in source_file:
                    byte_count += len(line)
                    if byte_count > self.maximum_file_bytes or len(line) > 64_000:
                        raise HistoricalDataError("Historical evidence source exceeds size limit")
                    digest.update(line)
                    record = self._record(line, spec.source)
                    if isinstance(record, ObservedNews):
                        news.append(record)
                    else:
                        scheduled.append(record)
                    source_count += 1
                    total_records += 1
                    if total_records > self.maximum_records:
                        raise HistoricalDataError("Historical evidence exceeds the record limit")
            if source_count != spec.record_count:
                raise HistoricalDataError("Historical evidence record count mismatch")
            if digest.hexdigest() != spec.sha256:
                raise HistoricalDataError("Historical evidence checksum mismatch")

        if any(record.observed_at >= coverage_end for record in (*news, *scheduled)):
            raise HistoricalDataError("Historical evidence was observed outside declared coverage")

        identities = [
            (record.item.source.source_id, record.item.url, record.item.published_at)
            for record in news
        ] + [
            (record.event.source.source_id, record.event.external_id, record.event.starts_at)
            for record in scheduled
        ]
        if len(set(identities)) != len(identities):
            raise HistoricalDataError("Historical evidence contains duplicate records")
        return HistoricalRiskEvidence(
            dataset_id,
            manifest_sha,
            coverage_start,
            coverage_end,
            specs,
            tuple(sorted(news, key=lambda record: record.item.published_at)),
            tuple(sorted(scheduled, key=lambda record: record.event.starts_at)),
        )

    @staticmethod
    def _record(line: bytes, source: SourceSpec) -> ObservedNews | ObservedScheduledEvent:
        try:
            value = json.loads(line, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalDataError("Historical evidence record is not valid JSON") from exc
        if not isinstance(value, dict):
            raise HistoricalDataError("Historical evidence record must be an object")
        kind = value.get("kind")
        if source.media_type in {"rss", "atom"} and kind == "news":
            _fields(value, _NEWS_FIELDS, "historical news evidence")
            published_at = _utc(value["published_at"], "published_at")
            observed_at = _utc(value["observed_at"], "observed_at")
            if observed_at < published_at:
                raise HistoricalDataError("Historical news cannot be observed before publication")
            title = _string(value["title"], "title")
            url = _string(value["url"], "url")
            assert title is not None and url is not None
            if urlsplit(url).hostname != urlsplit(source.url).hostname:
                raise HistoricalDataError("Historical news URL is outside its official source")
            return ObservedNews(RawNewsItem(source, title, url, published_at), observed_at)
        if source.media_type == "ical" and kind == "scheduled":
            _fields(value, _SCHEDULED_FIELDS, "historical scheduled evidence")
            observed_at = _utc(value["observed_at"], "observed_at")
            title = _string(value["title"], "title")
            external_id = _string(value["external_id"], "external_id")
            url = _string(value["url"], "url", optional=True)
            assert title is not None and external_id is not None
            category, volatility = _scheduled_risk(title)
            return ObservedScheduledEvent(
                ScheduledEvent(
                    external_id,
                    title,
                    _utc(value["starts_at"], "starts_at"),
                    category,
                    volatility,
                    source,
                    url,
                ),
                observed_at,
            )
        raise HistoricalDataError("Historical evidence kind contradicts its fixed source")

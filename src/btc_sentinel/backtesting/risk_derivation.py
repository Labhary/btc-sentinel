"""Deterministically derive a 15-minute risk timeline from immutable evidence."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.backtesting.risk_evidence import (
    HistoricalRiskEvidence,
    HistoricalRiskEvidenceLoader,
    ObservedNews,
    ObservedScheduledEvent,
)
from btc_sentinel.backtesting.risk_history import HistoricalRiskStore
from btc_sentinel.news.engine import NewsRiskEngine, NewsRiskPolicy
from btc_sentinel.news.models import NewsCollection
from btc_sentinel.news.sources import GDELT_DISCOVERY, OFFICIAL_FEEDS

_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DERIVATION_VERSION = "news-risk-v0.5.0"


@dataclass(frozen=True, slots=True)
class HistoricalRiskBuild:
    manifest_path: Path
    dataset_id: str
    point_count: int
    manifest_sha256: str


def _visible_news(
    evidence: HistoricalRiskEvidence,
    candidate: datetime,
    policy: NewsRiskPolicy,
) -> tuple[ObservedNews, ...]:
    return tuple(
        record
        for record in evidence.news
        if record.observed_at <= candidate
        and candidate - policy.lookback <= record.item.published_at <= candidate
    )


def _visible_scheduled(
    evidence: HistoricalRiskEvidence,
    candidate: datetime,
    policy: NewsRiskPolicy,
) -> tuple[ObservedScheduledEvent, ...]:
    window = max(policy.extreme_event_pre, policy.extreme_event_post)
    return tuple(
        record
        for record in evidence.scheduled_events
        if record.observed_at <= candidate
        and candidate - window <= record.event.starts_at <= candidate + window
    )


def _point(
    evidence: HistoricalRiskEvidence,
    candidate: datetime,
    engine: NewsRiskEngine,
) -> dict[str, object]:
    news = _visible_news(evidence, candidate, engine.policy)
    scheduled = _visible_scheduled(evidence, candidate, engine.policy)
    assessment = engine.evaluate(
        NewsCollection(
            candidate,
            tuple(record.item for record in news),
            tuple(record.event for record in scheduled),
            (),
        ),
        candidate,
    )
    inputs = (*news, *scheduled)
    return {
        "evaluated_at": candidate.isoformat(),
        "decision": assessment.decision.value,
        "block_until": (
            None if assessment.block_until is None else assessment.block_until.isoformat()
        ),
        "reasons": list(assessment.reasons),
        "coverage_issues": [],
        "source_ids": sorted(
            {
                record.item.source.source_id
                if isinstance(record, ObservedNews)
                else record.event.source.source_id
                for record in inputs
            }
        ),
        "evidence_observed_at": sorted({record.observed_at.isoformat() for record in inputs}),
    }


class HistoricalRiskTimelineBuilder:
    """Create a validated timeline whose derivation binds the evidence manifest hash."""

    def __init__(
        self,
        evidence_loader: HistoricalRiskEvidenceLoader | None = None,
        engine: NewsRiskEngine | None = None,
    ) -> None:
        self.evidence_loader = evidence_loader or HistoricalRiskEvidenceLoader()
        self.engine = engine or NewsRiskEngine()

    def build(
        self,
        evidence_manifest: Path,
        output_directory: Path,
        dataset_id: str,
    ) -> HistoricalRiskBuild:
        if not _DATASET_ID.fullmatch(dataset_id):
            raise HistoricalDataError("Historical risk dataset identifier is invalid")
        if output_directory.exists():
            raise HistoricalDataError("Historical risk output directory already exists")
        evidence = self.evidence_loader.load(evidence_manifest)
        output_directory.mkdir(parents=True)
        points_path = output_directory / "risk-points.jsonl"
        digest = hashlib.sha256()
        count = 0
        candidate = evidence.coverage_start
        with points_path.open("xb") as destination:
            while candidate < evidence.coverage_end:
                line = (
                    json.dumps(
                        _point(evidence, candidate, self.engine),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )
                destination.write(line)
                digest.update(line)
                count += 1
                candidate += timedelta(minutes=15)

        covered = set(evidence.source_ids)
        optional_exclusions = sorted(
            f"historical_{source.source_id}"
            for source in (*OFFICIAL_FEEDS, GDELT_DISCOVERY)
            if not source.required and source.source_id not in covered
        )
        manifest = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "coverage_start": evidence.coverage_start.isoformat(),
            "coverage_end": evidence.coverage_end.isoformat(),
            "interval": "15m",
            "derivation_version": (
                f"{_DERIVATION_VERSION}+evidence-sha256:{evidence.manifest_sha256}"
            ),
            "source_coverage": list(evidence.source_ids),
            "excluded_features": optional_exclusions,
            "points_path": points_path.name,
            "points_sha256": digest.hexdigest(),
            "point_count": count,
        }
        temporary = output_directory / "risk-manifest.json.part"
        final = output_directory / "risk-manifest.json"
        temporary.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        with (
            tempfile.TemporaryDirectory(prefix="btc-sentinel-risk-build-") as directory,
            HistoricalRiskStore(Path(directory) / "risk.sqlite3") as store,
        ):
            summary = store.import_manifest(temporary)
        temporary.rename(final)
        return HistoricalRiskBuild(final, dataset_id, count, summary.manifest_sha256)

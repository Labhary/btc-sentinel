import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from btc_sentinel.backtesting import HistoricalDataError, HistoricalRiskStore
from btc_sentinel.backtesting.risk_derivation import HistoricalRiskTimelineBuilder
from btc_sentinel.backtesting.risk_evidence import HistoricalRiskEvidenceLoader
from btc_sentinel.news.models import RiskDecision

START = datetime(2024, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=2)


def _news(title: str, url: str, published: datetime, observed: datetime) -> dict[str, object]:
    return {
        "kind": "news",
        "title": title,
        "url": url,
        "published_at": published.isoformat(),
        "observed_at": observed.isoformat(),
    }


def _scheduled(title: str, starts: datetime, observed: datetime) -> dict[str, object]:
    return {
        "kind": "scheduled",
        "external_id": f"event-{starts.timestamp():.0f}",
        "title": title,
        "starts_at": starts.isoformat(),
        "observed_at": observed.isoformat(),
        "url": "https://www.bls.gov/schedule/news_release/cpi.htm",
    }


class HistoricalRiskDerivationTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "evidence").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _evidence_manifest(
        self,
        *,
        fed: list[dict[str, object]] | None = None,
        sec: list[dict[str, object]] | None = None,
        bls: list[dict[str, object]] | None = None,
        omit: str | None = None,
        corrupt: str | None = None,
    ) -> Path:
        records = {
            "fed_monetary": fed
            or [
                _news(
                    "Federal Reserve FOMC interest rate decision",
                    "https://www.federalreserve.gov/newsevents/pressreleases/monetary-test.htm",
                    START + timedelta(minutes=15),
                    START + timedelta(minutes=15),
                )
            ],
            "sec_releases": sec
            or [
                _news(
                    "SEC statement concerning bitcoin markets",
                    "https://www.sec.gov/newsroom/press-releases/test",
                    START + timedelta(minutes=30),
                    START + timedelta(minutes=30),
                )
            ],
            "bls_calendar": bls
            or [
                _scheduled(
                    "Consumer Price Index",
                    START + timedelta(hours=1),
                    START,
                )
            ],
        }
        sources = []
        for source_id, values in records.items():
            if source_id == omit:
                continue
            content = b"".join(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                for value in values
            )
            relative = f"evidence/{source_id}.jsonl"
            (self.root / relative).write_bytes(content)
            sources.append(
                {
                    "source_id": source_id,
                    "path": relative,
                    "sha256": "0" * 64
                    if source_id == corrupt
                    else hashlib.sha256(content).hexdigest(),
                    "record_count": len(values),
                    "coverage_start": START.isoformat(),
                    "coverage_end": END.isoformat(),
                }
            )
        payload = {
            "schema_version": 1,
            "dataset_id": "official-risk-evidence-test-v1",
            "coverage_start": START.isoformat(),
            "coverage_end": END.isoformat(),
            "sources": sources,
        }
        path = self.root / "evidence-manifest.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def test_loads_required_evidence_and_derives_valid_continuous_timeline(self) -> None:
        evidence_path = self._evidence_manifest()
        evidence = HistoricalRiskEvidenceLoader().load(evidence_path)
        output = self.root / "derived"
        result = HistoricalRiskTimelineBuilder().build(
            evidence_path,
            output,
            "official-risk-derived-test-v1",
        )

        self.assertEqual(evidence.source_ids, ("fed_monetary", "sec_releases", "bls_calendar"))
        self.assertEqual(result.point_count, 8)
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertIn(evidence.manifest_sha256, manifest["derivation_version"])
        self.assertFalse((output / "risk-manifest.json.part").exists())

        with HistoricalRiskStore(self.root / "risk.sqlite3") as store:
            store.import_manifest(result.manifest_path)
            first = store.assessment_at(START)
            after_fed = store.assessment_at(START + timedelta(minutes=15))
        self.assertIs(first.decision, RiskDecision.BLOCK)
        self.assertIn("Consumer Price Index", " ".join(first.reasons))
        self.assertIs(after_fed.decision, RiskDecision.BLOCK)
        self.assertIn("Federal Reserve", " ".join(after_fed.reasons))

    def test_rejects_missing_source_checksum_and_false_observation_time(self) -> None:
        with self.assertRaisesRegex(HistoricalDataError, "required official source"):
            HistoricalRiskEvidenceLoader().load(self._evidence_manifest(omit="sec_releases"))
        with self.assertRaisesRegex(HistoricalDataError, "checksum mismatch"):
            HistoricalRiskEvidenceLoader().load(self._evidence_manifest(corrupt="fed_monetary"))

        future_publication = START + timedelta(hours=1)
        false_observation = START + timedelta(minutes=30)
        with self.assertRaisesRegex(HistoricalDataError, "before publication"):
            HistoricalRiskEvidenceLoader().load(
                self._evidence_manifest(
                    fed=[
                        _news(
                            "Federal Reserve FOMC interest rate decision",
                            "https://www.federalreserve.gov/newsevents/test.htm",
                            future_publication,
                            false_observation,
                        )
                    ]
                )
            )

    def test_rejects_external_news_urls_wrong_kinds_and_duplicate_records(self) -> None:
        invalid_cases = (
            (
                [
                    _news(
                        "Federal Reserve FOMC decision",
                        "https://example.com/fabricated",
                        START,
                        START,
                    )
                ],
                "outside its official source",
            ),
            (
                [_scheduled("Consumer Price Index", START, START)],
                "contradicts its fixed source",
            ),
        )
        for fed, message in invalid_cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(HistoricalDataError, message),
            ):
                HistoricalRiskEvidenceLoader().load(self._evidence_manifest(fed=fed))

        duplicate = _news(
            "Federal Reserve bitcoin decision",
            "https://www.federalreserve.gov/newsevents/duplicate.htm",
            START,
            START,
        )
        with self.assertRaisesRegex(HistoricalDataError, "duplicate records"):
            HistoricalRiskEvidenceLoader().load(self._evidence_manifest(fed=[duplicate, duplicate]))

    def test_builder_refuses_to_overwrite_output(self) -> None:
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            HistoricalRiskTimelineBuilder().build(
                self._evidence_manifest(),
                output,
                "risk-test-v1",
            )

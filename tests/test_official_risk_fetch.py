import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase
from urllib.parse import urlsplit

from btc_sentinel.backtesting import HistoricalDataError
from btc_sentinel.backtesting.official_risk_fetch import (
    OfficialRiskArchiveBuilder,
    UrllibOfficialPageDownloader,
    _bls_records,
    _fed_record,
    _sec_records,
)
from btc_sentinel.backtesting.risk_derivation import HistoricalRiskTimelineBuilder
from btc_sentinel.backtesting.risk_evidence import HistoricalRiskEvidenceLoader


def _bls_page(year: int, modified: str | None = "November 17, 2023") -> bytes:
    sections = []
    for month in (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ):
        modification = "" if modified is None else f" Last Modified Date: {modified}"
        sections.append(
            f"<h1>{month} {year}</h1><table><tr><td>Monday, {month} 01, {year}</td>"
            "<td>08:30 AM</td><td>Consumer Price Index for prior month</td></tr></table>"
            f"<p>NOTE: All times on calendar are Eastern Time.{modification}</p>"
        )
    return ("<html><body>" + "".join(sections) + "</body></html>").encode()


class FakeDownloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        parsed = urlsplit(url)
        if parsed.hostname == "www.federalreserve.gov" and parsed.path.endswith("press-fomc.htm"):
            return (
                b'<a href="/newsevents/pressreleases/monetary20240131a.htm">'
                b"Federal Reserve issues FOMC statement</a>"
            )
        if parsed.hostname == "www.federalreserve.gov":
            return b"""<html><body><h2>Press Release</h2><p>January 31, 2024</p>
            <h3>Federal Reserve issues FOMC statement</h3>
            <p>For release at 2:00 p.m. EST</p></body></html>"""
        if parsed.hostname == "www.sec.gov":
            return b"""<table><tr><td><time datetime="2024-01-10T21:01:02Z">Jan. 10</time></td>
            <td><a href="/newsroom/press-releases/2024-1">SEC Approves Spot Bitcoin ETFs</a></td>
            </tr></table>"""
        if parsed.hostname == "www.bls.gov":
            return _bls_page(2024)
        raise AssertionError(url)


class OfficialRiskArchiveTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_parses_exact_fed_and_sec_timestamps(self) -> None:
        fed = _fed_record(
            b"January 31, 2024 Federal Reserve issues FOMC statement For release at 2:00 p.m. EST",
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20240131a.htm",
            "Federal Reserve issues FOMC statement",
        )
        sec = _sec_records(
            b'<time datetime="2024-01-10T21:01:02Z">Jan. 10</time>'
            b'<a href="/newsroom/press-releases/2024-1">SEC Bitcoin action</a>',
            2024,
        )[0]
        self.assertEqual(fed["published_at"], "2024-01-31T19:00:00+00:00")
        self.assertEqual(sec["published_at"], "2024-01-10T21:01:02+00:00")

    def test_preserves_the_official_2021_sec_archive_path_typo(self) -> None:
        records = _sec_records(
            b'<time datetime="2021-04-05T16:50:03Z">April 5, 2021</time>'
            b'<a href="/newsroom/press-releases/22021-67">SEC webcast</a>',
            2021,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["url"],
            "https://www.sec.gov/newsroom/press-releases/22021-67",
        )

    def test_bls_last_modified_time_creates_conservative_gap(self) -> None:
        records, gaps = _bls_records(_bls_page(2024, "January 15, 2024"), 2024)
        self.assertEqual(len(records), 12)
        self.assertEqual(gaps[0][0], datetime(2024, 1, 1, tzinfo=UTC))
        self.assertGreater(gaps[0][1], datetime(2024, 1, 15, tzinfo=UTC))
        self.assertEqual(records[0]["starts_at"], "2024-01-01T13:30:00+00:00")

    def test_sec_exception_does_not_accept_foreign_hosts_or_other_typos(self) -> None:
        for href in (
            "https://example.com/newsroom/press-releases/22021-67",
            "/newsroom/press-releases/22021-68",
        ):
            with self.subTest(href=href), self.assertRaises(HistoricalDataError):
                _sec_records(
                    (
                        '<time datetime="2021-04-05T16:50:03Z">April 5</time>'
                        f'<a href="{href}">SEC webcast</a>'
                    ).encode(),
                    2021,
                )

    def test_sec_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(HistoricalDataError, "invalid publication timestamp"):
            _sec_records(
                b'<time datetime="2021-04-05T16:50:03">April 5</time>'
                b'<a href="/newsroom/press-releases/2021-67">SEC webcast</a>',
                2021,
            )

    def test_missing_bls_modified_date_blocks_the_entire_month(self) -> None:
        retrieved = datetime(2026, 1, 1, tzinfo=UTC)
        records, gaps = _bls_records(_bls_page(2024, None), 2024, retrieved)
        self.assertEqual(len(records), 12)
        self.assertEqual(len(gaps), 12)
        self.assertEqual(
            gaps[0],
            (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)),
        )
        self.assertEqual(records[0]["observed_at"], retrieved.isoformat())

    def test_builds_v2_manifest_with_raw_artifacts_and_blocking_gaps(self) -> None:
        downloader = FakeDownloader()
        output = self.root / "official"
        result = OfficialRiskArchiveBuilder(downloader).build(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
            output,
            "official-risk-2024-v1",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(result.artifact_count, 4)
        self.assertEqual(result.record_count, 14)
        self.assertEqual(result.coverage_gap_count, 4)
        self.assertTrue(all((output / item["path"]).is_file() for item in manifest["artifacts"]))

        risk_output = self.root / "risk"
        HistoricalRiskTimelineBuilder().build(
            result.manifest_path,
            risk_output,
            "official-risk-derived-2024-v1",
        )
        first = json.loads((risk_output / "risk-points.jsonl").read_text().splitlines()[0])
        self.assertEqual(first["decision"], "BLOCK")
        self.assertEqual(
            {issue["source_id"] for issue in first["coverage_issues"]},
            {"fed_monetary", "sec_releases", "bls_calendar"},
        )

        first_artifact = output / manifest["artifacts"][0]["path"]
        first_artifact.write_bytes(first_artifact.read_bytes() + b"tampered")
        with self.assertRaisesRegex(HistoricalDataError, "artifact checksum mismatch"):
            HistoricalRiskEvidenceLoader().load(result.manifest_path)

    def test_late_bls_archive_blocks_uncertain_period(self) -> None:
        downloader = FakeDownloader()
        original_fetch = downloader.fetch

        def late_fetch(url: str) -> bytes:
            if urlsplit(url).hostname == "www.bls.gov":
                return _bls_page(2024, "January 15, 2024")
            return original_fetch(url)

        downloader.fetch = late_fetch  # type: ignore[method-assign]
        result = OfficialRiskArchiveBuilder(downloader).build(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
            self.root / "late",
            "official-risk-late-v1",
        )
        risk = self.root / "late-risk"
        HistoricalRiskTimelineBuilder().build(result.manifest_path, risk, "late-risk-v1")
        first = json.loads((risk / "risk-points.jsonl").read_text().splitlines()[0])
        self.assertEqual(first["decision"], "BLOCK")
        self.assertTrue(first["coverage_issues"][0]["required"])

    def test_rejects_unapproved_url_and_overwrite(self) -> None:
        downloader = UrllibOfficialPageDownloader(maximum_attempts=1)
        with self.assertRaisesRegex(HistoricalDataError, "fixed source catalog"):
            downloader.fetch("https://example.com/archive")
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(HistoricalDataError, "already exists"):
            OfficialRiskArchiveBuilder(FakeDownloader()).build(
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                output,
                "test-v1",
            )

    def test_rejects_non_year_boundaries_and_excessive_range(self) -> None:
        builder = OfficialRiskArchiveBuilder(FakeDownloader(), maximum_years=1)
        with self.assertRaisesRegex(HistoricalDataError, "calendar-year boundary"):
            builder.build(
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                self.root / "bad",
                "test-v1",
            )
        with self.assertRaisesRegex(HistoricalDataError, "exceeds"):
            builder.build(
                datetime(2023, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                self.root / "long",
                "test-v1",
            )

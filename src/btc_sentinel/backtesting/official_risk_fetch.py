"""Conservative reconstruction of historical risk evidence from official archives."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from btc_sentinel.backtesting.dataset import HistoricalDataError
from btc_sentinel.backtesting.risk_evidence import HistoricalRiskEvidenceLoader

_FED_ORIGIN = "https://www.federalreserve.gov"
_SEC_ORIGIN = "https://www.sec.gov"
_BLS_ORIGIN = "https://www.bls.gov"
_FED_ARCHIVE = re.compile(r"/newsevents/pressreleases/(\d{4})-press-fomc\.htm")
_FED_RELEASE = re.compile(r"/newsevents/pressreleases/monetary\d{8}[a-z]\.htm")
_BLS_ARCHIVE = re.compile(r"/schedule/(\d{4})/home\.htm")
_SEC_RELEASE = re.compile(r"/newsroom/press-releases/(\d{4})-[A-Za-z0-9-]+")
_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EASTERN = ZoneInfo("America/New_York")
_MONTHS = (
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
)
_BLS_TITLES = (
    "Consumer Price Index",
    "Producer Price Index",
    "Employment Situation",
    "Job Openings and Labor Turnover",
    "Employment Cost Index",
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise HistoricalDataError("Official archive URL is invalid")
    if parsed.hostname == "www.federalreserve.gov":
        approved_path = _FED_ARCHIVE.fullmatch(parsed.path) or _FED_RELEASE.fullmatch(parsed.path)
        if parsed.query or not approved_path:
            raise HistoricalDataError("Federal Reserve URL is outside the fixed archive paths")
        return
    if parsed.hostname == "www.bls.gov":
        if parsed.query or not _BLS_ARCHIVE.fullmatch(parsed.path):
            raise HistoricalDataError("BLS URL is outside the fixed archive path")
        return
    if parsed.hostname == "www.sec.gov" and parsed.path == "/newsroom/press-releases":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"year", "month", "page"} or query["month"] != ["All"]:
            raise HistoricalDataError("SEC URL has unexpected archive filters")
        if not re.fullmatch(r"20\d{2}", query["year"][0]) or not query["page"][0].isdigit():
            raise HistoricalDataError("SEC URL has invalid archive filters")
        return
    raise HistoricalDataError("Official archive URL is outside the fixed source catalog")


class OfficialPageDownloader(Protocol):
    def fetch(self, url: str) -> bytes: ...


class UrllibOfficialPageDownloader:
    """Fetch fixed official HTML pages with no redirects and bounded retries."""

    def __init__(
        self,
        *,
        maximum_bytes: int = 5_000_000,
        timeout_seconds: float = 30,
        maximum_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if maximum_bytes < 1 or not 0 < timeout_seconds <= 60:
            raise ValueError("Official page size and timeout limits are invalid")
        if not 1 <= maximum_attempts <= 5:
            raise ValueError("Official page attempts must be between 1 and 5")
        self.maximum_bytes = maximum_bytes
        self.timeout_seconds = timeout_seconds
        self.maximum_attempts = maximum_attempts
        self.sleeper = sleeper
        self.opener = build_opener(_NoRedirect())

    def fetch(self, url: str) -> bytes:
        _validate_url(url)
        final_error: Exception | None = None
        for attempt in range(1, self.maximum_attempts + 1):
            try:
                request = Request(
                    url,
                    method="GET",
                    headers={
                        "Accept": "text/html",
                        "User-Agent": "btc-sentinel/official-history (+https://github.com/Labhary/btc-sentinel)",
                    },
                )
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise HistoricalDataError(
                            "Official archive returned an unexpected response"
                        )
                    content_type = response.headers.get_content_type()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise HistoricalDataError("Official archive did not return HTML")
                    payload = response.read(self.maximum_bytes + 1)
                    if not payload or len(payload) > self.maximum_bytes:
                        raise HistoricalDataError("Official archive response size is invalid")
                    payload.decode("utf-8")
                    return payload
            except HistoricalDataError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
                final_error = exc
                status = exc.code if isinstance(exc, HTTPError) else None
                if (
                    status not in {None, 408, 429, 500, 502, 503, 504}
                    or attempt == self.maximum_attempts
                ):
                    break
                self.sleeper(min(2 ** (attempt - 1), 5))
        raise HistoricalDataError("Official archive download failed") from final_error


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.times: list[tuple[str, str]] = []
        self.sections: list[str] = []
        self._href: str | None = None
        self._datetime: str | None = None
        self._capture: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._capture = []
        elif tag == "time" and attributes.get("datetime"):
            self._datetime = attributes["datetime"]
            self._capture = []
        elif tag in {"h1", "h2"}:
            self.sections.append("\n@@SECTION@@ ")

    def handle_data(self, data: str) -> None:
        self.sections.append(f" {data} ")
        if self._href is not None or self._datetime is not None:
            self._capture.append(data)

    def handle_endtag(self, tag: str) -> None:
        text = " ".join("".join(self._capture).split())
        if tag == "a" and self._href is not None:
            self.links.append((self._href, text))
            self._href = None
            self._capture = []
        elif tag == "time" and self._datetime is not None:
            self.times.append((self._datetime, text))
            self._datetime = None
            self._capture = []

    @property
    def text(self) -> str:
        return " ".join(html.unescape("".join(self.sections)).split())


def _document(payload: bytes) -> _Document:
    parser = _Document()
    try:
        parser.feed(payload.decode("utf-8"))
        parser.close()
    except (UnicodeDecodeError, ValueError) as exc:
        raise HistoricalDataError("Official archive HTML is malformed") from exc
    return parser


def _fed_links(payload: bytes, year: int) -> tuple[tuple[str, str], ...]:
    document = _document(payload)
    links = []
    for href, title in document.links:
        path = urlsplit(urljoin(_FED_ORIGIN, href)).path
        if _FED_RELEASE.fullmatch(path) and path.startswith(
            f"/newsevents/pressreleases/monetary{year}"
        ):
            links.append((urljoin(_FED_ORIGIN, path), title))
    unique = tuple(dict.fromkeys(links))
    if not unique:
        raise HistoricalDataError("Federal Reserve archive contained no FOMC releases")
    return unique


def _fed_record(payload: bytes, url: str, fallback_title: str) -> dict[str, object]:
    text = _document(payload).text
    date_match = re.search(r"\b(" + "|".join(_MONTHS) + r") \d{1,2}, 20\d{2}\b", text)
    time_match = re.search(
        r"For release at (\d{1,2}:\d{2})\s*([ap])\.?m\.?(?:\s+(?:ET|EST|EDT))?",
        text,
        re.IGNORECASE,
    )
    if date_match is None or time_match is None:
        raise HistoricalDataError("Federal Reserve release lacks an exact official release time")
    date_value = datetime.strptime(date_match.group(0), "%B %d, %Y").date()
    hour, minute = (int(part) for part in time_match.group(1).split(":"))
    if time_match.group(2).casefold() == "p" and hour != 12:
        hour += 12
    if time_match.group(2).casefold() == "a" and hour == 12:
        hour = 0
    published = datetime.combine(date_value, clock_time(hour, minute), _EASTERN).astimezone(UTC)
    title = fallback_title.strip()
    if not title:
        raise HistoricalDataError("Federal Reserve release title is missing")
    return {
        "kind": "news",
        "title": title,
        "url": url,
        "published_at": published.isoformat(),
        "observed_at": published.isoformat(),
    }


def _sec_records(payload: bytes, year: int) -> tuple[dict[str, object], ...]:
    document = _document(payload)
    releases = [
        (urljoin(_SEC_ORIGIN, href), title)
        for href, title in document.links
        if _SEC_RELEASE.fullmatch(urlsplit(urljoin(_SEC_ORIGIN, href)).path)
        and urlsplit(urljoin(_SEC_ORIGIN, href)).path.startswith(
            f"/newsroom/press-releases/{year}-"
        )
    ]
    timestamps = []
    for value, _ in document.times:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as exc:
            raise HistoricalDataError(
                "SEC archive contains an invalid publication timestamp"
            ) from exc
        if parsed.year == year:
            timestamps.append(parsed)
    if len(releases) != len(timestamps):
        raise HistoricalDataError("SEC archive rows could not be paired with timestamps")
    return tuple(
        {
            "kind": "news",
            "title": title,
            "url": url,
            "published_at": published.isoformat(),
            "observed_at": published.isoformat(),
        }
        for (url, title), published in zip(releases, timestamps, strict=True)
    )


def _bls_records(
    payload: bytes, year: int
) -> tuple[tuple[dict[str, object], ...], tuple[tuple[datetime, datetime], ...]]:
    raw_text = html.unescape(payload.decode("utf-8"))
    visible = _document(payload).text
    records: list[dict[str, object]] = []
    gaps: list[tuple[datetime, datetime]] = []
    headings = list(re.finditer(r"@@SECTION@@\s+(" + "|".join(_MONTHS) + rf")\s+{year}\b", visible))
    if len(headings) != 12:
        # Some official templates strip heading markers; preserve a bounded fallback.
        visible = re.sub(r"<[^>]+>", " ", raw_text)
        visible = " ".join(html.unescape(visible).split())
        headings = list(re.finditer(r"\b(" + "|".join(_MONTHS) + rf")\s+{year}\b", visible))
        headings = headings[-12:]
    if len(headings) != 12:
        raise HistoricalDataError("BLS archive does not contain exactly twelve monthly sections")
    for index, heading in enumerate(headings):
        month = _MONTHS.index(heading.group(1)) + 1
        end_index = headings[index + 1].start() if index + 1 < len(headings) else len(visible)
        section = visible[heading.end() : end_index]
        modified_match = re.search(
            r"Last Modified Date:\s*(" + "|".join(_MONTHS) + r")\s+\d{1,2},\s+20\d{2}",
            section,
        )
        if modified_match is None:
            raise HistoricalDataError("BLS monthly schedule lacks a last-modified date")
        modified_date = datetime.strptime(
            modified_match.group(0).split(":", 1)[1].strip(), "%B %d, %Y"
        ).date()
        observed = datetime.combine(modified_date, clock_time.max, _EASTERN).astimezone(UTC)
        month_start = datetime(year, month, 1, tzinfo=UTC)
        month_end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        if observed > month_start:
            gaps.append((month_start, min(observed, month_end)))
        weekday = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        row_pattern = re.compile(
            weekday + r",\s+"
            r"(" + "|".join(_MONTHS) + rf")\s+(\d{{1,2}}),\s+{year}\s+"
            r"(\d{1,2}:\d{2})\s+([AP]M)\s+(.*?)\s+"
            rf"(?={weekday},|NOTE:|Last Modified Date:)",
        )
        for match in row_pattern.finditer(section + " NOTE:"):
            title = " ".join(match.group(5).split())
            if not title.startswith(_BLS_TITLES):
                continue
            starts = (
                datetime.strptime(
                    f"{match.group(1)} {match.group(2)}, {year} {match.group(3)} {match.group(4)}",
                    "%B %d, %Y %I:%M %p",
                )
                .replace(tzinfo=_EASTERN)
                .astimezone(UTC)
            )
            slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:80]
            records.append(
                {
                    "kind": "scheduled",
                    "external_id": f"bls:{starts.date().isoformat()}:{slug}",
                    "title": title,
                    "starts_at": starts.isoformat(),
                    "observed_at": observed.isoformat(),
                    "url": f"{_BLS_ORIGIN}/schedule/{year}/home.htm",
                }
            )
    if not records:
        raise HistoricalDataError("BLS archive contained no policy-relevant scheduled releases")
    return tuple(records), tuple(gap for gap in gaps if gap[1] > gap[0])


def _coalesce_gaps(gaps: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(gaps, key=lambda item: (str(item["source_id"]), str(item["start"])))
    result: list[dict[str, object]] = []
    for gap in ordered:
        if (
            result
            and result[-1]["source_id"] == gap["source_id"]
            and datetime.fromisoformat(str(gap["start"]))
            <= datetime.fromisoformat(str(result[-1]["end"]))
        ):
            result[-1]["end"] = max(str(result[-1]["end"]), str(gap["end"]))
            details = sorted({str(result[-1]["detail"]), str(gap["detail"])})
            result[-1]["detail"] = "; ".join(details)
        else:
            result.append(dict(gap))
    return result


@dataclass(frozen=True, slots=True)
class OfficialRiskArchiveBuild:
    manifest_path: Path
    dataset_id: str
    artifact_count: int
    record_count: int
    coverage_gap_count: int


class OfficialRiskArchiveBuilder:
    """Acquire raw official pages and create checksum-bound normalized evidence."""

    def __init__(
        self,
        downloader: OfficialPageDownloader | None = None,
        *,
        maximum_years: int = 10,
        maximum_sec_pages_per_year: int = 20,
    ) -> None:
        if not 1 <= maximum_years <= 20 or not 1 <= maximum_sec_pages_per_year <= 50:
            raise ValueError("Official archive bounds are invalid")
        self.downloader = downloader or UrllibOfficialPageDownloader()
        self.maximum_years = maximum_years
        self.maximum_sec_pages_per_year = maximum_sec_pages_per_year

    def build(
        self,
        start: datetime,
        end: datetime,
        output_directory: Path,
        dataset_id: str,
        *,
        retrieved_at: datetime | None = None,
    ) -> OfficialRiskArchiveBuild:
        first = self._year_boundary(start, "start")
        stop = self._year_boundary(end, "end")
        years = tuple(range(first.year, stop.year))
        if not years or len(years) > self.maximum_years:
            raise HistoricalDataError("Official archive year range is empty or exceeds its limit")
        if not _DATASET_ID.fullmatch(dataset_id):
            raise HistoricalDataError("Historical evidence dataset identifier is invalid")
        if output_directory.exists():
            raise HistoricalDataError("Historical evidence output directory already exists")
        retrieved = (retrieved_at or datetime.now(UTC)).astimezone(UTC)
        output_directory.mkdir(parents=True)
        raw = output_directory / "raw"
        normalized = output_directory / "evidence"
        raw.mkdir()
        normalized.mkdir()
        artifacts: list[dict[str, object]] = []
        source_records: dict[str, list[dict[str, object]]] = {
            "fed_monetary": [],
            "sec_releases": [],
            "bls_calendar": [],
        }
        gaps: list[dict[str, object]] = []
        for source_id in ("fed_monetary", "sec_releases"):
            gaps.append(
                {
                    "source_id": source_id,
                    "start": first.isoformat(),
                    "end": (first + timedelta(hours=24)).isoformat(),
                    "detail": "prior 24-hour official-news lookback is outside archive coverage",
                }
            )
        gaps.extend(
            (
                {
                    "source_id": "bls_calendar",
                    "start": first.isoformat(),
                    "end": (first + timedelta(hours=2)).isoformat(),
                    "detail": "prior scheduled-event risk window is outside archive coverage",
                },
                {
                    "source_id": "bls_calendar",
                    "start": (stop - timedelta(hours=2)).isoformat(),
                    "end": stop.isoformat(),
                    "detail": "following scheduled-event risk window is outside archive coverage",
                },
            )
        )

        def acquire(source_id: str, artifact_id: str, url: str) -> tuple[bytes, str]:
            payload = self.downloader.fetch(url)
            digest = hashlib.sha256(payload).hexdigest()
            if not _SHA256.fullmatch(digest):
                raise AssertionError("SHA-256 implementation returned an invalid digest")
            relative = f"raw/{artifact_id}.html"
            (output_directory / relative).write_bytes(payload)
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "source_id": source_id,
                    "path": relative,
                    "url": url,
                    "sha256": digest,
                    "retrieved_at": retrieved.isoformat(),
                }
            )
            return payload, artifact_id

        for year in years:
            fed_url = f"{_FED_ORIGIN}/newsevents/pressreleases/{year}-press-fomc.htm"
            fed_index, fed_index_id = acquire("fed_monetary", f"fed-fomc-{year}", fed_url)
            for position, (release_url, title) in enumerate(_fed_links(fed_index, year), start=1):
                release, release_id = acquire(
                    "fed_monetary", f"fed-fomc-{year}-{position:02d}", release_url
                )
                record = _fed_record(release, release_url, title)
                record["artifact_ids"] = [fed_index_id, release_id]
                source_records["fed_monetary"].append(record)

            for page in range(self.maximum_sec_pages_per_year):
                sec_url = f"{_SEC_ORIGIN}/newsroom/press-releases?year={year}&month=All&page={page}"
                payload, artifact_id = acquire(
                    "sec_releases", f"sec-press-{year}-{page:02d}", sec_url
                )
                records = _sec_records(payload, year)
                for record in records:
                    record["artifact_ids"] = [artifact_id]
                    source_records["sec_releases"].append(record)
                if len(records) < 25:
                    break
            else:
                raise HistoricalDataError("SEC archive exceeded the bounded page count")

            bls_url = f"{_BLS_ORIGIN}/schedule/{year}/home.htm"
            payload, artifact_id = acquire("bls_calendar", f"bls-schedule-{year}", bls_url)
            records, year_gaps = _bls_records(payload, year)
            for record in records:
                record["artifact_ids"] = [artifact_id]
                source_records["bls_calendar"].append(record)
            gaps.extend(
                {
                    "source_id": "bls_calendar",
                    "start": gap_start.isoformat(),
                    "end": gap_end.isoformat(),
                    "detail": (
                        "official BLS schedule was not yet evidenced by its stated "
                        "last-modified date"
                    ),
                }
                for gap_start, gap_end in year_gaps
            )

        source_specs = []
        total = 0
        for source_id, records in source_records.items():
            identities = [
                (item.get("url"), item.get("published_at", item.get("starts_at")))
                for item in records
            ]
            if not records or len(set(identities)) != len(identities):
                raise HistoricalDataError("Official archive records are empty or duplicated")
            records.sort(key=lambda item: str(item.get("published_at", item.get("starts_at"))))
            content = b"".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                for item in records
            )
            relative = f"evidence/{source_id}.jsonl"
            (output_directory / relative).write_bytes(content)
            source_specs.append(
                {
                    "source_id": source_id,
                    "path": relative,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "record_count": len(records),
                    "coverage_start": first.isoformat(),
                    "coverage_end": stop.isoformat(),
                }
            )
            total += len(records)

        gaps = _coalesce_gaps(gaps)
        manifest = {
            "schema_version": 2,
            "dataset_id": dataset_id,
            "coverage_start": first.isoformat(),
            "coverage_end": stop.isoformat(),
            "sources": source_specs,
            "artifacts": artifacts,
            "coverage_gaps": gaps,
        }
        partial = output_directory / "evidence-manifest.json.part"
        final = output_directory / "evidence-manifest.json"
        partial.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        HistoricalRiskEvidenceLoader().load(partial)
        partial.rename(final)
        return OfficialRiskArchiveBuild(final, dataset_id, len(artifacts), total, len(gaps))

    @staticmethod
    def _year_boundary(value: datetime, name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise HistoricalDataError(f"{name} must be timezone-aware")
        result = value.astimezone(UTC)
        if result != datetime(result.year, 1, 1, tzinfo=UTC):
            raise HistoricalDataError(f"{name} must be a UTC calendar-year boundary")
        return result

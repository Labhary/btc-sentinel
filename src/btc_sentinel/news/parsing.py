"""Bounded RSS/Atom, iCalendar, and GDELT parsers."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from btc_sentinel.news.errors import NewsValidationError
from btc_sentinel.news.models import (
    NewsCategory,
    RawNewsItem,
    ScheduledEvent,
    SourceSpec,
    VolatilityImpact,
)

MAX_FEED_BYTES = 2_000_000
MAX_FEED_ITEMS = 200
_TAG = re.compile(r"<[^>]+>")


def _safe_payload(payload: bytes, kind: str) -> bytes:
    if not payload or len(payload) > MAX_FEED_BYTES:
        raise NewsValidationError(f"{kind} payload is empty or too large")
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise NewsValidationError(f"{kind} payload contains a forbidden XML declaration")
    return payload


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return " ".join(unescape(_TAG.sub(" ", "".join(element.itertext()))).split())


def _parse_datetime(value: str) -> datetime:
    value = value.strip()
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise NewsValidationError("Feed timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise NewsValidationError("Feed timestamp must include a timezone")
    return result.astimezone(UTC)


def parse_rss_or_atom(payload: bytes, source: SourceSpec) -> tuple[RawNewsItem, ...]:
    if source.media_type not in {"rss", "atom"}:
        raise NewsValidationError("Source is not configured as RSS or Atom")
    try:
        root = ElementTree.fromstring(_safe_payload(payload, "Feed"))
    except ElementTree.ParseError as exc:
        raise NewsValidationError("Feed XML is malformed") from exc
    records: list[RawNewsItem] = []
    items = root.findall(".//item")
    if items:
        for item in items[:MAX_FEED_ITEMS]:
            title = _text(item.find("title"))
            link = _text(item.find("link")) or _text(item.find("guid"))
            published = _text(item.find("pubDate")) or _text(item.find("date"))
            summary = _text(item.find("description"))
            records.append(
                RawNewsItem(
                    source, title, urljoin(source.url, link), _parse_datetime(published), summary
                )
            )
        return tuple(records)

    namespace = "{http://www.w3.org/2005/Atom}"
    entries = root.findall(f".//{namespace}entry")
    for entry in entries[:MAX_FEED_ITEMS]:
        link_node = entry.find(f"{namespace}link")
        link = "" if link_node is None else link_node.attrib.get("href", "")
        title = _text(entry.find(f"{namespace}title"))
        published = _text(entry.find(f"{namespace}published")) or _text(
            entry.find(f"{namespace}updated")
        )
        summary = _text(entry.find(f"{namespace}summary")) or _text(
            entry.find(f"{namespace}content")
        )
        records.append(
            RawNewsItem(
                source, title, urljoin(source.url, link), _parse_datetime(published), summary
            )
        )
    if not records:
        raise NewsValidationError("Feed contains no supported entries")
    return tuple(records)


def _unfold_ical(payload: bytes) -> list[str]:
    try:
        text = _safe_payload(payload, "iCalendar").decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NewsValidationError("iCalendar is not valid UTF-8") from exc
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ical_datetime(key: str, value: str) -> datetime:
    parameters = key.split(";")[1:]
    if any(item.upper() == "VALUE=DATE" for item in parameters):
        raise NewsValidationError("All-day calendar rows are not timed risk events")
    timezone_name = next(
        (item.split("=", 1)[1] for item in parameters if item.upper().startswith("TZID=")),
        None,
    )
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        if timezone_name is None:
            raise NewsValidationError("Calendar timestamp is missing a timezone")
        return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(UTC)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise NewsValidationError("Calendar timestamp is invalid") from exc


def _scheduled_classification(title: str) -> tuple[NewsCategory, VolatilityImpact] | None:
    normalized = title.casefold()
    if "consumer price index" in normalized or "producer price index" in normalized:
        return NewsCategory.ECONOMIC, VolatilityImpact.HIGH
    if "employment situation" in normalized:
        return NewsCategory.ECONOMIC, VolatilityImpact.HIGH
    if "job openings and labor turnover" in normalized:
        return NewsCategory.ECONOMIC, VolatilityImpact.MEDIUM
    if "employment cost index" in normalized:
        return NewsCategory.ECONOMIC, VolatilityImpact.MEDIUM
    if "fomc" in normalized or "federal open market committee" in normalized:
        return NewsCategory.CENTRAL_BANK, VolatilityImpact.EXTREME
    return None


def parse_ical(payload: bytes, source: SourceSpec) -> tuple[ScheduledEvent, ...]:
    if source.media_type != "ical":
        raise NewsValidationError("Source is not configured as iCalendar")
    events: list[ScheduledEvent] = []
    current: dict[str, str] | None = None
    for line in _unfold_ical(payload):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if current.get("STATUS", "").upper() != "CANCELLED":
                title = current.get("SUMMARY", "").replace("\\,", ",").strip()
                classification = _scheduled_classification(title)
                start_key = next((key for key in current if key.startswith("DTSTART")), None)
                if classification is not None and start_key and current.get("UID"):
                    category, volatility = classification
                    events.append(
                        ScheduledEvent(
                            current["UID"],
                            title,
                            _ical_datetime(start_key, current[start_key]),
                            category,
                            volatility,
                            source,
                            current.get("URL"),
                        )
                    )
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value
    return tuple(events[:MAX_FEED_ITEMS])


def parse_gdelt_json(payload: bytes, source: SourceSpec) -> tuple[RawNewsItem, ...]:
    if source.media_type != "json":
        raise NewsValidationError("Source is not configured as JSON")
    try:
        document = json.loads(_safe_payload(payload, "GDELT").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NewsValidationError("GDELT response is not valid JSON") from exc
    articles = document.get("articles") if isinstance(document, dict) else None
    if not isinstance(articles, list):
        raise NewsValidationError("GDELT response is missing articles")
    result: list[RawNewsItem] = []
    for article in articles[:MAX_FEED_ITEMS]:
        if not isinstance(article, dict):
            raise NewsValidationError("GDELT article is not an object")
        seen = str(article.get("seendate", ""))
        if re.fullmatch(r"\d{8}T\d{6}Z", seen):
            published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        else:
            published = _parse_datetime(seen)
        result.append(
            RawNewsItem(
                source,
                str(article.get("title", "")),
                str(article.get("url", "")),
                published,
                publisher_domain=str(article.get("domain", "")) or None,
            )
        )
    return tuple(result)

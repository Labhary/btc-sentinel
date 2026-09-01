"""Collect every fixed source independently and preserve coverage failures."""

from __future__ import annotations

from datetime import datetime

from btc_sentinel.news.errors import NewsError
from btc_sentinel.news.models import (
    CoverageIssue,
    NewsCollection,
    RawNewsItem,
    ScheduledEvent,
    SourceSpec,
)
from btc_sentinel.news.parsing import parse_gdelt_json, parse_ical, parse_rss_or_atom
from btc_sentinel.news.sources import GDELT_DISCOVERY, OFFICIAL_FEEDS
from btc_sentinel.news.transport import PublicNewsTransport
from btc_sentinel.time_utils import ensure_utc


class NewsCollector:
    def __init__(
        self,
        transport: PublicNewsTransport,
        *,
        sources: tuple[SourceSpec, ...] = (*OFFICIAL_FEEDS, GDELT_DISCOVERY),
    ) -> None:
        self.transport = transport
        self.sources = tuple(sources)

    def collect(self, as_of: datetime) -> NewsCollection:
        captured = ensure_utc(as_of)
        items: list[RawNewsItem] = []
        scheduled: list[ScheduledEvent] = []
        issues: list[CoverageIssue] = []
        for source in self.sources:
            try:
                payload = self.transport.fetch(source)
                if source.media_type in {"rss", "atom"}:
                    items.extend(parse_rss_or_atom(payload, source))
                elif source.media_type == "ical":
                    scheduled.extend(parse_ical(payload, source))
                elif source.media_type == "json":
                    items.extend(parse_gdelt_json(payload, source))
            except NewsError as exc:
                issues.append(CoverageIssue(source.source_id, str(exc), source.required))
        return NewsCollection(captured, tuple(items), tuple(scheduled), tuple(issues))

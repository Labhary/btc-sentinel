from __future__ import annotations

from datetime import UTC, datetime, timedelta

from btc_sentinel.news.models import RawNewsItem, SourceSpec, SourceTier

NEWS_NOW = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
OFFICIAL = SourceSpec(
    "official_test",
    "Official test source",
    "https://official.example/feed.xml",
    SourceTier.OFFICIAL,
    True,
    "rss",
)
AGGREGATOR = SourceSpec(
    "aggregator_test",
    "Aggregator test source",
    "https://aggregator.example/feed.json",
    SourceTier.AGGREGATOR,
    False,
    "json",
)


def item(
    title: str,
    *,
    source: SourceSpec = OFFICIAL,
    url: str = "https://official.example/news/1",
    age: timedelta = timedelta(minutes=10),
    domain: str | None = None,
    summary: str = "",
) -> RawNewsItem:
    return RawNewsItem(source, title, url, NEWS_NOW - age, summary, domain)


def rss(title: str = "SEC approves spot Bitcoin ETF") -> bytes:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title><item>
<title>{title}</title><link>https://official.example/news/1</link>
<pubDate>Tue, 01 Sep 2026 14:50:00 GMT</pubDate>
<description>Official Bitcoin update</description>
</item></channel></rss>""".encode()

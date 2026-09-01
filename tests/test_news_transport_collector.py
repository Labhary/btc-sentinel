from unittest import TestCase

from btc_sentinel.news.collector import NewsCollector
from btc_sentinel.news.errors import NewsTransportError
from btc_sentinel.news.sources import BLS_CALENDAR, FED_MONETARY
from btc_sentinel.news.transport import FeedResponse, PublicNewsTransport
from tests.news_fixtures import NEWS_NOW, OFFICIAL, rss


class FakeAdapter:
    def __init__(self, responses: list[FeedResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: float) -> FeedResponse:
        self.urls.append(url)
        return self.responses.pop(0)


class NewsTransportCollectorTests(TestCase):
    def test_transport_fetches_allowlisted_source(self) -> None:
        adapter = FakeAdapter([FeedResponse(200, rss())])
        transport = PublicNewsTransport(adapter, sleeper=lambda _: None)
        self.assertEqual(transport.fetch(FED_MONETARY), rss())

    def test_transport_rejects_unknown_source(self) -> None:
        transport = PublicNewsTransport(FakeAdapter([]), sleeper=lambda _: None)
        with self.assertRaisesRegex(NewsTransportError, "allowlist"):
            transport.fetch(OFFICIAL)

    def test_transport_retries_transient_failure(self) -> None:
        adapter = FakeAdapter([FeedResponse(503, b""), FeedResponse(200, rss())])
        transport = PublicNewsTransport(adapter, sleeper=lambda _: None)
        self.assertEqual(transport.fetch(FED_MONETARY), rss())
        self.assertEqual(len(adapter.urls), 2)

    def test_transport_does_not_retry_client_error(self) -> None:
        adapter = FakeAdapter([FeedResponse(404, b"")])
        transport = PublicNewsTransport(adapter, sleeper=lambda _: None)
        with self.assertRaisesRegex(NewsTransportError, "404"):
            transport.fetch(FED_MONETARY)
        self.assertEqual(len(adapter.urls), 1)

    def test_transport_rejects_oversized_response(self) -> None:
        adapter = FakeAdapter([FeedResponse(200, b"x" * 101)])
        transport = PublicNewsTransport(adapter, maximum_bytes=100, sleeper=lambda _: None)
        with self.assertRaisesRegex(NewsTransportError, "too large"):
            transport.fetch(FED_MONETARY)

    def test_collector_preserves_required_failure(self) -> None:
        transport = PublicNewsTransport(FakeAdapter([FeedResponse(503, b"")]), maximum_attempts=1)
        result = NewsCollector(transport, sources=(FED_MONETARY,)).collect(NEWS_NOW)
        self.assertTrue(result.issues[0].required)

    def test_collector_parses_rss(self) -> None:
        transport = PublicNewsTransport(FakeAdapter([FeedResponse(200, rss())]))
        result = NewsCollector(transport, sources=(FED_MONETARY,)).collect(NEWS_NOW)
        self.assertEqual(len(result.items), 1)

    def test_collector_parses_ical(self) -> None:
        payload = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cpi
SUMMARY:Consumer Price Index
DTSTART:20260911T123000Z
END:VEVENT
END:VCALENDAR"""
        transport = PublicNewsTransport(FakeAdapter([FeedResponse(200, payload)]))
        result = NewsCollector(transport, sources=(BLS_CALENDAR,)).collect(NEWS_NOW)
        self.assertEqual(len(result.scheduled_events), 1)

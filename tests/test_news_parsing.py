from datetime import UTC, datetime
from unittest import TestCase

from btc_sentinel.news.errors import NewsValidationError
from btc_sentinel.news.models import VolatilityImpact
from btc_sentinel.news.parsing import parse_gdelt_json, parse_ical, parse_rss_or_atom
from btc_sentinel.news.sources import BLS_CALENDAR, GDELT_DISCOVERY
from tests.news_fixtures import AGGREGATOR, OFFICIAL, rss


class NewsParsingTests(TestCase):
    def test_rss_parses_item(self) -> None:
        records = parse_rss_or_atom(rss(), OFFICIAL)
        self.assertEqual(records[0].title, "SEC approves spot Bitcoin ETF")
        self.assertEqual(records[0].published_at, datetime(2026, 9, 1, 14, 50, tzinfo=UTC))

    def test_atom_parses_entry(self) -> None:
        payload = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>Coinbase outage affects Bitcoin</title><link href="https://status.example/inc/1"/>
        <updated>2026-09-01T14:50:00Z</updated><summary>Incident</summary>
        </entry></feed>"""
        source = OFFICIAL.__class__(
            "atom", "Atom", "https://status.example/history.atom", OFFICIAL.tier, False, "atom"
        )
        self.assertEqual(
            parse_rss_or_atom(payload, source)[0].title, "Coinbase outage affects Bitcoin"
        )

    def test_feed_rejects_doctype(self) -> None:
        with self.assertRaisesRegex(NewsValidationError, "forbidden"):
            parse_rss_or_atom(b"<!DOCTYPE foo><rss></rss>", OFFICIAL)

    def test_feed_rejects_malformed_xml(self) -> None:
        with self.assertRaisesRegex(NewsValidationError, "malformed"):
            parse_rss_or_atom(b"<rss><item>", OFFICIAL)

    def test_feed_rejects_empty_entries(self) -> None:
        with self.assertRaisesRegex(NewsValidationError, "no supported"):
            parse_rss_or_atom(b"<feed></feed>", OFFICIAL)

    def test_ical_parses_high_impact_release(self) -> None:
        payload = b"""BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:cpi-1\r\nSUMMARY:Consumer Price Index\r\nDTSTART;TZID=America/New_York:20260911T083000\r\nURL:https://www.bls.gov/cpi/\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
        event = parse_ical(payload, BLS_CALENDAR)[0]
        self.assertIs(event.volatility, VolatilityImpact.HIGH)
        self.assertEqual(event.starts_at.hour, 12)

    def test_ical_ignores_irrelevant_release(self) -> None:
        payload = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:x
SUMMARY:Regional report
DTSTART:20260911T120000Z
END:VEVENT
END:VCALENDAR"""
        self.assertEqual(parse_ical(payload, BLS_CALENDAR), ())

    def test_ical_skips_cancelled_event(self) -> None:
        payload = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:x
SUMMARY:Consumer Price Index
STATUS:CANCELLED
DTSTART:20260911T120000Z
END:VEVENT
END:VCALENDAR"""
        self.assertEqual(parse_ical(payload, BLS_CALENDAR), ())

    def test_gdelt_parses_articles(self) -> None:
        payload = b'{"articles":[{"title":"Bitcoin ETF approved","url":"https://media.example/a","domain":"media.example","seendate":"20260901T145000Z"}]}'
        record = parse_gdelt_json(payload, GDELT_DISCOVERY)[0]
        self.assertEqual(record.publisher_domain, "media.example")

    def test_gdelt_rejects_missing_articles(self) -> None:
        with self.assertRaisesRegex(NewsValidationError, "missing articles"):
            parse_gdelt_json(b"{}", GDELT_DISCOVERY)

    def test_json_parser_requires_json_source(self) -> None:
        with self.assertRaises(NewsValidationError):
            parse_gdelt_json(
                b"{}",
                AGGREGATOR.__class__(
                    "rss", "RSS", "https://example.com/rss", AGGREGATOR.tier, False, "rss"
                ),
            )

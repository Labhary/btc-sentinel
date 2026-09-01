from dataclasses import replace
from datetime import timedelta
from unittest import TestCase

from btc_sentinel.news.engine import NewsRiskEngine, canonical_title, canonical_url
from btc_sentinel.news.models import (
    ConfirmationStatus,
    CoverageIssue,
    NewsCategory,
    NewsCollection,
    NewsDirection,
    RiskDecision,
    ScheduledEvent,
    VolatilityImpact,
)
from btc_sentinel.news.sources import BLS_CALENDAR
from tests.news_fixtures import AGGREGATOR, NEWS_NOW, item


class NewsEngineTests(TestCase):
    def evaluate(self, items=(), scheduled=(), issues=()):
        return NewsRiskEngine().evaluate(
            NewsCollection(NEWS_NOW, tuple(items), tuple(scheduled), tuple(issues)), NEWS_NOW
        )

    def test_canonical_title_removes_punctuation(self) -> None:
        self.assertEqual(canonical_title("  Bitcoin: ETF—Approved! "), "bitcoin etf approved")

    def test_canonical_url_removes_tracking_and_fragment(self) -> None:
        self.assertEqual(
            canonical_url("https://EXAMPLE.com/a/?utm_source=x&id=2#top"),
            "https://example.com/a?id=2",
        )

    def test_official_high_impact_news_blocks(self) -> None:
        result = self.evaluate([item("SEC approves spot Bitcoin ETF")])
        self.assertIs(result.decision, RiskDecision.BLOCK)
        self.assertIs(result.events[0].confirmation, ConfirmationStatus.OFFICIAL_CONFIRMED)
        self.assertEqual(result.events[0].reliability_score, 100)
        self.assertIs(result.events[0].direction, NewsDirection.POSITIVE)

    def test_single_aggregated_headline_only_cautions(self) -> None:
        result = self.evaluate(
            [item("Bitcoin exchange outage", source=AGGREGATOR, domain="one.example")]
        )
        self.assertIs(result.decision, RiskDecision.CAUTION)
        self.assertIs(result.events[0].confirmation, ConfirmationStatus.UNCONFIRMED)
        self.assertEqual(result.events[0].reliability_score, 35)

    def test_two_publishers_corroborate_story(self) -> None:
        first = item(
            "Major Bitcoin exchange outage",
            source=AGGREGATOR,
            domain="one.example",
            url="https://one.example/a",
        )
        second = item(
            "Major Bitcoin exchange outage reported",
            source=AGGREGATOR,
            domain="two.example",
            url="https://two.example/b",
        )
        result = self.evaluate([first, second])
        self.assertIs(result.events[0].confirmation, ConfirmationStatus.CORROBORATED)
        self.assertEqual(result.events[0].reliability_score, 75)
        self.assertIs(result.decision, RiskDecision.BLOCK)

    def test_corroboration_wait_starts_from_newest_evidence(self) -> None:
        first = item(
            "Major Bitcoin exchange outage",
            source=AGGREGATOR,
            domain="one.example",
            url="https://one.example/a",
            age=timedelta(minutes=50),
        )
        second = item(
            "Major Bitcoin exchange outage reported",
            source=AGGREGATOR,
            domain="two.example",
            url="https://two.example/b",
            age=timedelta(minutes=5),
        )
        result = self.evaluate([first, second])
        self.assertEqual(result.block_until, NEWS_NOW + timedelta(minutes=55))

    def test_duplicate_url_is_one_event(self) -> None:
        first = item("Bitcoin ETF approved", url="https://example.com/a?utm_source=x")
        second = item("Spot BTC fund receives approval", url="https://example.com/a")
        self.assertEqual(len(self.evaluate([first, second]).events), 1)

    def test_irrelevant_story_is_removed(self) -> None:
        self.assertEqual(self.evaluate([item("Regional banking appointment")]).events, ())

    def test_stale_story_is_removed(self) -> None:
        self.assertEqual(
            self.evaluate([item("Bitcoin exchange outage", age=timedelta(days=2))]).events, ()
        )

    def test_future_dated_story_is_removed(self) -> None:
        future = replace(
            item("Bitcoin exchange outage"), published_at=NEWS_NOW + timedelta(hours=1)
        )
        self.assertEqual(self.evaluate([future]).events, ())

    def test_required_coverage_failure_blocks(self) -> None:
        result = self.evaluate(issues=[CoverageIssue("bls", "unavailable", True)])
        self.assertIs(result.decision, RiskDecision.BLOCK)
        self.assertIsNone(result.block_until)

    def test_optional_coverage_failure_cautions(self) -> None:
        result = self.evaluate(issues=[CoverageIssue("gdelt", "unavailable", False)])
        self.assertIs(result.decision, RiskDecision.CAUTION)

    def test_high_scheduled_event_blocks_before_release(self) -> None:
        scheduled = ScheduledEvent(
            "cpi",
            "Consumer Price Index",
            NEWS_NOW + timedelta(minutes=30),
            NewsCategory.ECONOMIC,
            VolatilityImpact.HIGH,
            BLS_CALENDAR,
        )
        result = self.evaluate(scheduled=[scheduled])
        self.assertIs(result.decision, RiskDecision.BLOCK)
        self.assertEqual(result.block_until, scheduled.starts_at + timedelta(minutes=45))

    def test_medium_scheduled_event_does_not_block(self) -> None:
        scheduled = ScheduledEvent(
            "jolts",
            "Job Openings and Labor Turnover Survey",
            NEWS_NOW,
            NewsCategory.ECONOMIC,
            VolatilityImpact.MEDIUM,
            BLS_CALENDAR,
        )
        self.assertIs(self.evaluate(scheduled=[scheduled]).decision, RiskDecision.CLEAR)

    def test_contradictory_confirmed_news_blocks(self) -> None:
        positive = item("SEC approved Bitcoin ETF", url="https://official.example/positive")
        negative = item("Regulator banned Bitcoin product", url="https://official.example/negative")
        result = self.evaluate([positive, negative])
        self.assertIn("directionally contradictory", " ".join(result.reasons))

    def test_low_impact_official_news_does_not_force_block(self) -> None:
        result = self.evaluate([item("Federal Reserve publishes neutral Bitcoin research note")])
        self.assertIs(result.decision, RiskDecision.CLEAR)

    def test_news_engine_never_creates_signal(self) -> None:
        result = self.evaluate([item("SEC approves spot Bitcoin ETF")])
        self.assertFalse(result.news_can_create_signal)
        self.assertFalse(result.allows_new_signal)

    def test_category_stablecoin_precedes_bitcoin(self) -> None:
        result = self.evaluate([item("Bitcoin market watches USDT stablecoin depeg")])
        self.assertIs(result.events[0].category, NewsCategory.STABLECOIN)

    def test_negative_keywords_classify_negative(self) -> None:
        result = self.evaluate([item("Coinbase outage suspends Bitcoin trading")])
        self.assertIs(result.events[0].direction, NewsDirection.NEGATIVE)

    def test_mixed_direction_is_uncertain(self) -> None:
        result = self.evaluate([item("Bitcoin ETF approved while exchange outage continues")])
        self.assertIs(result.events[0].direction, NewsDirection.UNCERTAIN)

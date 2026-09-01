from datetime import datetime
from unittest import TestCase

from btc_sentinel.news.errors import NewsValidationError
from btc_sentinel.news.models import (
    NewsCollection,
    RawNewsItem,
    RiskAssessment,
    RiskDecision,
    SourceSpec,
    SourceTier,
)
from tests.news_fixtures import NEWS_NOW, OFFICIAL, item


class NewsModelTests(TestCase):
    def test_source_requires_https(self) -> None:
        with self.assertRaises(NewsValidationError):
            SourceSpec("bad", "Bad", "http://example.com/feed", SourceTier.OFFICIAL, True, "rss")

    def test_source_rejects_credentials(self) -> None:
        with self.assertRaises(NewsValidationError):
            SourceSpec(
                "bad",
                "Bad",
                "https://user:pass@example.com/feed",
                SourceTier.OFFICIAL,
                True,
                "rss",
            )

    def test_source_rejects_unknown_media_type(self) -> None:
        with self.assertRaises(NewsValidationError):
            SourceSpec("bad", "Bad", "https://example.com/feed", SourceTier.OFFICIAL, True, "html")

    def test_item_normalizes_whitespace(self) -> None:
        value = RawNewsItem(OFFICIAL, "  Bitcoin   update ", "https://example.com/a", NEWS_NOW)
        self.assertEqual(value.title, "Bitcoin update")

    def test_item_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(NewsValidationError):
            RawNewsItem(OFFICIAL, "Bitcoin", "https://example.com/a", datetime(2026, 1, 1))

    def test_item_rejects_non_https_url(self) -> None:
        with self.assertRaises(NewsValidationError):
            RawNewsItem(OFFICIAL, "Bitcoin", "http://example.com/a", NEWS_NOW)

    def test_news_collection_freezes_sequences(self) -> None:
        collection = NewsCollection(NEWS_NOW, [item("Bitcoin update")], [], [])  # type: ignore[arg-type]
        self.assertIsInstance(collection.items, tuple)

    def test_news_cannot_create_signal(self) -> None:
        with self.assertRaises(NewsValidationError):
            RiskAssessment(NEWS_NOW, RiskDecision.CLEAR, None, (), (), (), (), True)

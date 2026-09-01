"""Immutable records for news, schedules, and risk decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from btc_sentinel.errors import DomainValidationError
from btc_sentinel.news.errors import NewsValidationError
from btc_sentinel.time_utils import ensure_utc


def _news_utc(value: datetime) -> datetime:
    try:
        return ensure_utc(value)
    except DomainValidationError as exc:
        raise NewsValidationError("News timestamp must be timezone-aware") from exc


class SourceTier(StrEnum):
    OFFICIAL = "OFFICIAL"
    REPUTABLE = "REPUTABLE"
    AGGREGATOR = "AGGREGATOR"
    UNKNOWN = "UNKNOWN"


class NewsCategory(StrEnum):
    REGULATORY = "REGULATORY"
    ETF = "ETF"
    CENTRAL_BANK = "CENTRAL_BANK"
    ECONOMIC = "ECONOMIC"
    EXCHANGE = "EXCHANGE"
    STABLECOIN = "STABLECOIN"
    INSOLVENCY = "INSOLVENCY"
    INSTITUTIONAL = "INSTITUTIONAL"
    GEOPOLITICAL = "GEOPOLITICAL"
    BITCOIN = "BITCOIN"
    OTHER = "OTHER"


class NewsDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNCERTAIN = "UNCERTAIN"


class VolatilityImpact(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class ConfirmationStatus(StrEnum):
    OFFICIAL_CONFIRMED = "OFFICIAL_CONFIRMED"
    CORROBORATED = "CORROBORATED"
    UNCONFIRMED = "UNCONFIRMED"


class RiskDecision(StrEnum):
    CLEAR = "CLEAR"
    CAUTION = "CAUTION"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    name: str
    url: str
    tier: SourceTier
    required: bool
    media_type: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if not self.source_id or not self.name or parsed.scheme != "https" or not parsed.hostname:
            raise NewsValidationError("News sources require an ID, name, and HTTPS URL")
        if (
            parsed.username
            or parsed.password
            or self.media_type
            not in {
                "rss",
                "atom",
                "ical",
                "json",
            }
        ):
            raise NewsValidationError("News source URL or media type is unsafe")


@dataclass(frozen=True, slots=True)
class RawNewsItem:
    source: SourceSpec
    title: str
    url: str
    published_at: datetime
    summary: str = ""
    publisher_domain: str | None = None

    def __post_init__(self) -> None:
        title = " ".join(self.title.split())
        summary = " ".join(self.summary.split())
        parsed = urlsplit(self.url)
        if not title or len(title) > 500:
            raise NewsValidationError("News title is missing or too long")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise NewsValidationError("News item requires a safe HTTPS URL")
        if len(summary) > 4000:
            raise NewsValidationError("News summary is too long")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "published_at", _news_utc(self.published_at))
        object.__setattr__(self, "publisher_domain", self.publisher_domain or parsed.hostname)


@dataclass(frozen=True, slots=True)
class NewsEvent:
    fingerprint: str
    title: str
    published_at: datetime
    category: NewsCategory
    direction: NewsDirection
    volatility: VolatilityImpact
    confirmation: ConfirmationStatus
    reliability_score: int
    source_ids: tuple[str, ...]
    publisher_domains: tuple[str, ...]
    urls: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _news_utc(self.published_at))
        for name in ("source_ids", "publisher_domains", "urls"):
            object.__setattr__(self, name, tuple(dict.fromkeys(getattr(self, name))))
        if not self.fingerprint or not self.title or not self.urls:
            raise NewsValidationError("A news event requires identity, title, and evidence")
        if not 0 <= self.reliability_score <= 100:
            raise NewsValidationError("News reliability score must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    external_id: str
    title: str
    starts_at: datetime
    category: NewsCategory
    volatility: VolatilityImpact
    source: SourceSpec
    url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "starts_at", _news_utc(self.starts_at))
        if not self.external_id or not self.title:
            raise NewsValidationError("Scheduled event requires an ID and title")
        if self.url is not None and urlsplit(self.url).scheme != "https":
            raise NewsValidationError("Scheduled event URL must use HTTPS")


@dataclass(frozen=True, slots=True)
class CoverageIssue:
    source_id: str
    detail: str
    required: bool

    def __post_init__(self) -> None:
        if not self.source_id or not self.detail:
            raise NewsValidationError("Coverage issue requires source and detail")


@dataclass(frozen=True, slots=True)
class NewsCollection:
    collected_at: datetime
    items: tuple[RawNewsItem, ...]
    scheduled_events: tuple[ScheduledEvent, ...]
    issues: tuple[CoverageIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "collected_at", _news_utc(self.collected_at))
        for name in ("items", "scheduled_events", "issues"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    evaluated_at: datetime
    decision: RiskDecision
    block_until: datetime | None
    events: tuple[NewsEvent, ...]
    scheduled_events: tuple[ScheduledEvent, ...]
    reasons: tuple[str, ...]
    coverage_issues: tuple[CoverageIssue, ...]
    news_can_create_signal: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluated_at", _news_utc(self.evaluated_at))
        if self.block_until is not None:
            object.__setattr__(self, "block_until", _news_utc(self.block_until))
        for name in ("events", "scheduled_events", "reasons", "coverage_issues"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.news_can_create_signal:
            raise NewsValidationError("News is a risk filter and cannot create a signal")
        if self.decision is RiskDecision.BLOCK and not self.reasons:
            raise NewsValidationError("A blocking decision requires an auditable reason")

    @property
    def allows_new_signal(self) -> bool:
        return self.decision is not RiskDecision.BLOCK

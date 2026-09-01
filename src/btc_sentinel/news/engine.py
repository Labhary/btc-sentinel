"""Deterministic deduplication, classification, and news risk windows."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from btc_sentinel.news.models import (
    ConfirmationStatus,
    NewsCategory,
    NewsCollection,
    NewsDirection,
    NewsEvent,
    RawNewsItem,
    RiskAssessment,
    RiskDecision,
    ScheduledEvent,
    SourceTier,
    VolatilityImpact,
)
from btc_sentinel.time_utils import ensure_utc

_WORD = re.compile(r"[a-z0-9]+")
_TRACKING = {"fbclid", "gclid", "ref", "source"}

_RELEVANT = {
    "bitcoin",
    "btc",
    "crypto",
    "digital asset",
    "etf",
    "exchange-traded fund",
    "federal reserve",
    "fomc",
    "interest rate",
    "inflation",
    "consumer price index",
    "producer price index",
    "employment situation",
    "payroll",
    "stablecoin",
    "tether",
    "usdt",
    "usdc",
    "coinbase",
    "binance",
    "hack",
    "exploit",
    "outage",
    "bankruptcy",
    "insolvency",
    "geopolitical",
}
_POSITIVE = {
    "approve",
    "approves",
    "approved",
    "approval",
    "adoption",
    "inflow",
    "launch",
    "resumes",
    "resolved",
    "recovery",
}
_NEGATIVE = {
    "ban",
    "banned",
    "breach",
    "charges",
    "depeg",
    "exploit",
    "hack",
    "halted",
    "insolvency",
    "lawsuit",
    "outage",
    "reject",
    "rejected",
    "suspend",
    "war",
    "attack",
    "bankruptcy",
}
_EXTREME = {"depeg", "hack", "exploit", "insolvency", "bankruptcy", "war", "attack"}
_HIGH = {
    "outage",
    "halted",
    "suspend",
    "approval",
    "approved",
    "approves",
    "ban",
    "banned",
    "rejected",
}


def canonical_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_WORD.findall(normalized))


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    filtered = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=False)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, urlencode(filtered), "")
    )


def _tokens(item: RawNewsItem) -> set[str]:
    return set(canonical_title(item.title).split())


def _similar(left: RawNewsItem, right: RawNewsItem) -> bool:
    if canonical_url(left.url) == canonical_url(right.url):
        return True
    first, second = _tokens(left), _tokens(right)
    union = first | second
    return bool(union) and len(first & second) / len(union) >= 0.62


def _category(text: str) -> NewsCategory:
    checks = (
        (NewsCategory.ETF, ("etf", "exchange-traded fund")),
        (NewsCategory.STABLECOIN, ("stablecoin", "tether", "usdt", "usdc", "depeg")),
        (NewsCategory.INSOLVENCY, ("bankruptcy", "insolvency")),
        (NewsCategory.EXCHANGE, ("coinbase", "binance", "exchange outage")),
        (NewsCategory.CENTRAL_BANK, ("federal reserve", "fomc", "interest rate")),
        (
            NewsCategory.ECONOMIC,
            ("inflation", "consumer price index", "producer price index", "payroll"),
        ),
        (NewsCategory.REGULATORY, ("sec ", "regulation", "regulator", "lawsuit", "ban")),
        (NewsCategory.GEOPOLITICAL, ("geopolitical", "war", "attack")),
        (NewsCategory.INSTITUTIONAL, ("institution", "treasury", "reserve asset")),
        (NewsCategory.BITCOIN, ("bitcoin", "btc")),
    )
    return next(
        (category for category, terms in checks if any(term in text for term in terms)),
        NewsCategory.OTHER,
    )


def _direction(words: set[str]) -> NewsDirection:
    positive = bool(words & _POSITIVE)
    negative = bool(words & _NEGATIVE)
    if positive and negative:
        return NewsDirection.UNCERTAIN
    if positive:
        return NewsDirection.POSITIVE
    if negative:
        return NewsDirection.NEGATIVE
    return NewsDirection.NEUTRAL


def _volatility(words: set[str], category: NewsCategory) -> VolatilityImpact:
    if words & _EXTREME:
        return VolatilityImpact.EXTREME
    if words & _HIGH or category is NewsCategory.ETF:
        return VolatilityImpact.HIGH
    if category is NewsCategory.CENTRAL_BANK and words & {"fomc", "rate", "rates", "decision"}:
        return VolatilityImpact.HIGH
    if category is not NewsCategory.OTHER:
        return VolatilityImpact.MEDIUM
    return VolatilityImpact.LOW


def _is_relevant(item: RawNewsItem) -> bool:
    text = f"{item.title} {item.summary}".casefold()
    return any(term in text for term in _RELEVANT)


@dataclass(frozen=True, slots=True)
class NewsRiskPolicy:
    lookback: timedelta = timedelta(hours=24)
    clock_skew: timedelta = timedelta(minutes=5)
    high_news_wait: timedelta = timedelta(minutes=60)
    extreme_news_wait: timedelta = timedelta(hours=3)
    high_event_pre: timedelta = timedelta(minutes=60)
    high_event_post: timedelta = timedelta(minutes=45)
    extreme_event_pre: timedelta = timedelta(hours=2)
    extreme_event_post: timedelta = timedelta(hours=2)


class NewsRiskEngine:
    def __init__(self, policy: NewsRiskPolicy | None = None) -> None:
        self.policy = policy or NewsRiskPolicy()

    def evaluate(self, collection: NewsCollection, as_of: datetime) -> RiskAssessment:
        now = ensure_utc(as_of)
        usable = [
            item
            for item in collection.items
            if _is_relevant(item)
            and now - self.policy.lookback <= item.published_at <= now + self.policy.clock_skew
        ]
        events = self._events(usable)
        reasons: list[str] = []
        block_until: datetime | None = None
        decision = RiskDecision.CLEAR

        required_issues = [issue for issue in collection.issues if issue.required]
        if required_issues:
            decision = RiskDecision.BLOCK
            reasons.append("required news coverage is incomplete")
        elif collection.issues:
            decision = RiskDecision.CAUTION
            reasons.append("optional news coverage is degraded")

        active_scheduled = self._active_scheduled(collection.scheduled_events, now)
        for scheduled, end in active_scheduled:
            decision = RiskDecision.BLOCK
            reasons.append(
                f"scheduled {scheduled.volatility.value.lower()} event: {scheduled.title}"
            )
            block_until = end if block_until is None else max(block_until, end)

        recent_confirmed: list[NewsEvent] = []
        for event in events:
            if event.confirmation is ConfirmationStatus.UNCONFIRMED:
                if event.volatility in {VolatilityImpact.HIGH, VolatilityImpact.EXTREME}:
                    if decision is RiskDecision.CLEAR:
                        decision = RiskDecision.CAUTION
                    reasons.append(f"unconfirmed high-impact headline: {event.title}")
                continue
            if event.volatility not in {VolatilityImpact.HIGH, VolatilityImpact.EXTREME}:
                continue
            recent_confirmed.append(event)
            wait = (
                self.policy.extreme_news_wait
                if event.volatility is VolatilityImpact.EXTREME
                else self.policy.high_news_wait
            )
            end = event.published_at + wait
            if now < end:
                decision = RiskDecision.BLOCK
                reasons.append(f"market-confirmation wait after: {event.title}")
                block_until = end if block_until is None else max(block_until, end)

        directional = {
            event.direction
            for event in recent_confirmed
            if now - event.published_at <= timedelta(hours=6)
            and event.direction in {NewsDirection.POSITIVE, NewsDirection.NEGATIVE}
        }
        if directional == {NewsDirection.POSITIVE, NewsDirection.NEGATIVE}:
            decision = RiskDecision.BLOCK
            reasons.append("confirmed high-impact news is directionally contradictory")
            end = now + self.policy.high_news_wait
            block_until = end if block_until is None else max(block_until, end)

        return RiskAssessment(
            now,
            decision,
            block_until,
            tuple(events),
            tuple(event for event, _ in active_scheduled),
            tuple(dict.fromkeys(reasons)),
            collection.issues,
        )

    def _events(self, items: list[RawNewsItem]) -> list[NewsEvent]:
        clusters: list[list[RawNewsItem]] = []
        for item in sorted(items, key=lambda value: (value.published_at, value.title)):
            match = next((cluster for cluster in clusters if _similar(cluster[0], item)), None)
            if match is None:
                clusters.append([item])
            else:
                match.append(item)
        result: list[NewsEvent] = []
        for cluster in clusters:
            preferred = next(
                (item for item in cluster if item.source.tier is SourceTier.OFFICIAL),
                cluster[0],
            )
            combined = " ".join(f"{item.title} {item.summary}" for item in cluster).casefold()
            words = set(_WORD.findall(combined))
            category = _category(combined)
            official = any(item.source.tier is SourceTier.OFFICIAL for item in cluster)
            domains = tuple(sorted({item.publisher_domain or "" for item in cluster}))
            confirmation = (
                ConfirmationStatus.OFFICIAL_CONFIRMED
                if official
                else ConfirmationStatus.CORROBORATED
                if len(domains) >= 2
                else ConfirmationStatus.UNCONFIRMED
            )
            reliability_score = {
                ConfirmationStatus.OFFICIAL_CONFIRMED: 100,
                ConfirmationStatus.CORROBORATED: 75,
                ConfirmationStatus.UNCONFIRMED: 35,
            }[confirmation]
            normalized = canonical_title(preferred.title)
            result.append(
                NewsEvent(
                    hashlib.sha256(normalized.encode()).hexdigest(),
                    preferred.title,
                    max(item.published_at for item in cluster),
                    category,
                    _direction(words),
                    _volatility(words, category),
                    confirmation,
                    reliability_score,
                    tuple(item.source.source_id for item in cluster),
                    domains,
                    tuple(canonical_url(item.url) for item in cluster),
                )
            )
        return sorted(
            result, key=lambda event: (event.published_at, event.fingerprint), reverse=True
        )

    def _active_scheduled(
        self,
        events: tuple[ScheduledEvent, ...],
        now: datetime,
    ) -> list[tuple[ScheduledEvent, datetime]]:
        active: list[tuple[ScheduledEvent, datetime]] = []
        for event in events:
            if event.volatility is VolatilityImpact.EXTREME:
                start = event.starts_at - self.policy.extreme_event_pre
                end = event.starts_at + self.policy.extreme_event_post
            elif event.volatility is VolatilityImpact.HIGH:
                start = event.starts_at - self.policy.high_event_pre
                end = event.starts_at + self.policy.high_event_post
            else:
                continue
            if start <= now <= end:
                active.append((event, end))
        return active

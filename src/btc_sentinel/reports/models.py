"""Immutable inputs and outputs for bounded paper-report rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from btc_sentinel.domain.enums import MarketRegime, OutcomeVariant, Side, SignalStatus
from btc_sentinel.domain.models import Target, as_decimal, as_utc
from btc_sentinel.errors import DomainValidationError


class ReportKind(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    NEWS_RISK = "NEWS_RISK"


@dataclass(frozen=True, slots=True)
class ReportSignal:
    signal_id: str
    status: SignalStatus
    side: Side
    regime: MarketRegime
    setup_score: int
    created_at: datetime
    expires_at: datetime
    entry_low: Decimal
    entry_high: Decimal
    original_stop: Decimal
    targets: tuple[Target, ...]
    strategy_version: str
    fill_price: Decimal | None = None
    activated_at: datetime | None = None
    managed_stop: Decimal | None = None
    fixed_track_active: bool = False
    managed_track_active: bool = False

    def __post_init__(self) -> None:
        for name in ("created_at", "expires_at"):
            object.__setattr__(self, name, as_utc(getattr(self, name), name))
        for name in ("entry_low", "entry_high", "original_stop"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name))
        for name in ("fill_price", "managed_stop"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_decimal(value, name))
        if self.activated_at is not None:
            object.__setattr__(self, "activated_at", as_utc(self.activated_at, "activated_at"))
        object.__setattr__(self, "targets", tuple(self.targets))
        if not self.signal_id or not self.strategy_version.strip():
            raise DomainValidationError("Report signal identity and strategy are required")
        if not 0 <= self.setup_score <= 100 or self.expires_at <= self.created_at:
            raise DomainValidationError("Report signal score or time range is invalid")
        if self.status is SignalStatus.ACTIVE and (
            self.fill_price is None or self.activated_at is None
        ):
            raise DomainValidationError("Active report signal requires fill and activation time")
        if self.status is SignalStatus.PENDING and (
            self.fill_price is not None
            or self.activated_at is not None
            or self.managed_stop is not None
            or self.fixed_track_active
            or self.managed_track_active
        ):
            raise DomainValidationError("Pending report signal cannot contain active trade state")


@dataclass(frozen=True, slots=True)
class ReportDocument:
    kind: ReportKind
    generated_at: datetime
    text: str
    dedupe_key: str
    period_start: datetime | None = None
    period_end: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", as_utc(self.generated_at, "generated_at"))
        for name in ("period_start", "period_end"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_utc(value, name))
        if not self.text or len(self.text) > 4096:
            raise DomainValidationError("Telegram report text must contain 1 to 4096 characters")
        if not self.dedupe_key:
            raise DomainValidationError("Report dedupe key is required")
        if (self.period_start is None) != (self.period_end is None):
            raise DomainValidationError("Report period bounds must be supplied together")
        if self.period_start is not None and self.period_start >= self.period_end:
            raise DomainValidationError("Report period must have positive duration")

    def as_telegram_payload(self) -> dict[str, Any]:
        """Return a sendMessage body without chat identity or delivery side effects."""
        return {"text": self.text, "disable_web_page_preview": True}


def active_variant_labels(signal: ReportSignal) -> tuple[str, ...]:
    values: list[str] = []
    if signal.fixed_track_active:
        values.append(OutcomeVariant.FIXED.value)
    if signal.managed_track_active:
        values.append(OutcomeVariant.MANAGED.value)
    return tuple(values)

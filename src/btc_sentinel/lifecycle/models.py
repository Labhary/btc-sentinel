"""Immutable lifecycle replay records loaded from durable storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from btc_sentinel.domain.enums import OutcomeVariant, Side, SignalStatus
from btc_sentinel.domain.models import Target, as_decimal, as_utc
from btc_sentinel.errors import DomainValidationError


class LifecycleAction(StrEnum):
    ACTIVATED = "ACTIVATED"
    EXPIRED = "EXPIRED"
    TARGET_CLOSED = "TARGET_CLOSED"
    STOP_CLOSED = "STOP_CLOSED"
    AMBIGUOUS_STOP_FIRST = "AMBIGUOUS_STOP_FIRST"
    ACTIVATION_TARGET_DEFERRED = "ACTIVATION_TARGET_DEFERRED"


@dataclass(frozen=True, slots=True)
class LifecycleSignal:
    signal_id: str
    status: SignalStatus
    side: Side
    created_at: datetime
    expires_at: datetime
    entry_low: Decimal
    entry_high: Decimal
    original_stop: Decimal
    targets: tuple[Target, ...]
    estimated_cost_rate: Decimal
    recommended_risk_percent: Decimal
    fill_price: Decimal | None
    activated_at: datetime | None
    active_variants: tuple[OutcomeVariant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", as_utc(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", as_utc(self.expires_at, "expires_at"))
        for name in (
            "entry_low",
            "entry_high",
            "original_stop",
            "estimated_cost_rate",
            "recommended_risk_percent",
        ):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name))
        if self.fill_price is not None:
            object.__setattr__(self, "fill_price", as_decimal(self.fill_price, "fill_price"))
        if self.activated_at is not None:
            object.__setattr__(self, "activated_at", as_utc(self.activated_at, "activated_at"))
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "active_variants", tuple(self.active_variants))
        if not self.signal_id or self.expires_at <= self.created_at:
            raise DomainValidationError("Lifecycle signal identity or time range is invalid")
        if self.entry_low <= 0 or self.entry_high < self.entry_low or self.original_stop <= 0:
            raise DomainValidationError("Lifecycle prices are invalid")
        if len(self.targets) < 1:
            raise DomainValidationError("Lifecycle signal requires at least TP1")
        if self.status in {SignalStatus.ACTIVE, SignalStatus.CLOSED} and (
            self.fill_price is None or self.activated_at is None
        ):
            raise DomainValidationError(
                "Activated lifecycle signal requires fill and activation time"
            )

    @property
    def conservative_entry(self) -> Decimal:
        return self.entry_high if self.side is Side.LONG else self.entry_low

    def result_r(self, exit_price: Decimal) -> Decimal:
        if self.fill_price is None:
            raise DomainValidationError("Cannot calculate an outcome before activation")
        price = as_decimal(exit_price, "exit_price")
        cost = self.fill_price * self.estimated_cost_rate
        gross_risk = (
            self.fill_price - self.original_stop
            if self.side is Side.LONG
            else self.original_stop - self.fill_price
        )
        total_risk = gross_risk + cost
        gross_result = (
            price - self.fill_price if self.side is Side.LONG else self.fill_price - price
        )
        return (gross_result - cost) / total_risk


@dataclass(frozen=True, slots=True)
class ReplayResult:
    signal_id: str
    processed_candles: int
    checkpoint: datetime | None
    final_status: SignalStatus
    actions: tuple[LifecycleAction, ...]

    def __post_init__(self) -> None:
        if self.checkpoint is not None:
            object.__setattr__(self, "checkpoint", as_utc(self.checkpoint, "checkpoint"))
        object.__setattr__(self, "actions", tuple(self.actions))
        if not self.signal_id or self.processed_candles < 0:
            raise DomainValidationError("Replay result is invalid")

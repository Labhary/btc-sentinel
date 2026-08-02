"""Immutable signal terms with conservative risk/reward validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from btc_sentinel.domain.enums import Bias, MarketRegime, Side, SignalStatus
from btc_sentinel.domain.ids import validate_signal_id
from btc_sentinel.errors import DomainValidationError


def as_decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, float):
        raise DomainValidationError(f"{name} must not be a binary float")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} must be a decimal value") from exc
    if not decimal.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    return decimal


def as_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Target:
    ordinal: int
    price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", as_decimal(self.price, "target price"))
        if self.ordinal not in {1, 2, 3}:
            raise DomainValidationError("Target ordinal must be 1, 2, or 3")
        if self.price <= 0:
            raise DomainValidationError("Target price must be positive")


@dataclass(frozen=True, slots=True)
class TimeframeBiases:
    monthly: Bias
    weekly: Bias
    daily: Bias
    four_hour: Bias
    one_hour: Bias
    fifteen_minute: Bias


@dataclass(frozen=True, slots=True)
class SignalTerms:
    side: Side
    entry_low: Decimal
    entry_high: Decimal
    original_stop: Decimal
    targets: tuple[Target, ...]
    created_at: datetime
    data_timestamp: datetime
    expires_at: datetime
    invalidation_condition: str
    expiration_condition: str
    recommended_risk_percent: Decimal
    estimated_round_trip_cost_rate: Decimal = Decimal("0.0015")
    minimum_planned_rr: Decimal = Decimal("2")
    symbol: str = "BTCUSDT"

    def __post_init__(self) -> None:
        for field_name in (
            "entry_low",
            "entry_high",
            "original_stop",
            "recommended_risk_percent",
            "estimated_round_trip_cost_rate",
            "minimum_planned_rr",
        ):
            object.__setattr__(self, field_name, as_decimal(getattr(self, field_name), field_name))
        for field_name in ("created_at", "data_timestamp", "expires_at"):
            object.__setattr__(self, field_name, as_utc(getattr(self, field_name), field_name))

        if self.symbol != "BTCUSDT":
            raise DomainValidationError("Version 1 accepts BTCUSDT only")
        if self.entry_low <= 0 or self.entry_high <= 0 or self.original_stop <= 0:
            raise DomainValidationError("Entry and stop prices must be positive")
        if self.entry_low > self.entry_high:
            raise DomainValidationError("entry_low cannot exceed entry_high")
        if not 2 <= len(self.targets) <= 3:
            raise DomainValidationError("A signal must contain TP1, TP2, and optionally TP3")
        target_ordinals = tuple(target.ordinal for target in self.targets)
        if target_ordinals != tuple(range(1, len(self.targets) + 1)):
            raise DomainValidationError("Targets must be consecutive and ordered from TP1")
        if self.data_timestamp > self.created_at:
            raise DomainValidationError("Data timestamp cannot be later than signal creation")
        if self.expires_at <= self.created_at:
            raise DomainValidationError("Signal expiry must be later than creation")
        if not self.invalidation_condition.strip() or not self.expiration_condition.strip():
            raise DomainValidationError("Invalidation and expiration conditions are required")
        if not Decimal("0.01") <= self.recommended_risk_percent <= Decimal("1"):
            raise DomainValidationError("Recommended risk must be between 0.01% and 1%")
        if not Decimal("0") <= self.estimated_round_trip_cost_rate <= Decimal("0.01"):
            raise DomainValidationError("Estimated round-trip cost rate must be between 0 and 1%")
        if self.minimum_planned_rr < Decimal("2"):
            raise DomainValidationError("Minimum planned risk/reward cannot be below 2R")

        prices = tuple(target.price for target in self.targets)
        if self.side is Side.LONG:
            if self.original_stop >= self.entry_low:
                raise DomainValidationError("A LONG stop must be below the entry zone")
            if any(price <= self.entry_high for price in prices):
                raise DomainValidationError("Every LONG target must be above the entry zone")
            if prices != tuple(sorted(prices)):
                raise DomainValidationError("LONG targets must increase")
        else:
            if self.original_stop <= self.entry_high:
                raise DomainValidationError("A SHORT stop must be above the entry zone")
            if any(price >= self.entry_low for price in prices):
                raise DomainValidationError("Every SHORT target must be below the entry zone")
            if prices != tuple(sorted(prices, reverse=True)):
                raise DomainValidationError("SHORT targets must decrease")

        if self.planned_r_for(self.targets[0]) < self.minimum_planned_rr:
            raise DomainValidationError("TP1 does not meet the minimum net planned risk/reward")

    @property
    def conservative_entry(self) -> Decimal:
        return self.entry_high if self.side is Side.LONG else self.entry_low

    def planned_r_for(self, target: Target) -> Decimal:
        entry = self.conservative_entry
        cost = entry * self.estimated_round_trip_cost_rate
        if self.side is Side.LONG:
            gross_reward = target.price - entry
            gross_risk = entry - self.original_stop
        else:
            gross_reward = entry - target.price
            gross_risk = self.original_stop - entry
        net_reward = gross_reward - cost
        total_risk = gross_risk + cost
        if net_reward <= 0 or total_risk <= 0:
            return Decimal("0")
        return net_reward / total_risk


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    terms: SignalTerms
    setup_score: int
    regime: MarketRegime
    biases: TimeframeBiases
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    strategy_version: str
    status: SignalStatus = SignalStatus.PENDING
    row_version: int = 1

    def __post_init__(self) -> None:
        validate_signal_id(self.signal_id)
        if not 0 <= self.setup_score <= 100:
            raise DomainValidationError("Setup score must be between 0 and 100")
        if self.status is not SignalStatus.PENDING:
            raise DomainValidationError("A new Signal object must start PENDING")
        if not self.reasons or any(not value.strip() for value in self.reasons):
            raise DomainValidationError("At least one non-empty setup reason is required")
        if any(not value.strip() for value in self.risks):
            raise DomainValidationError("Risk descriptions cannot be empty")
        if not self.strategy_version.strip():
            raise DomainValidationError("A strategy version is required")
        if self.row_version != 1:
            raise DomainValidationError("A new signal must start at row version 1")

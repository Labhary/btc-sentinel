"""Immutable inputs and outputs for strict paper-trading statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from btc_sentinel.domain.enums import OutcomeResult, OutcomeVariant
from btc_sentinel.domain.models import as_decimal, as_utc
from btc_sentinel.errors import DomainValidationError


def _decimal_payload(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    signal_id: str
    variant: OutcomeVariant
    result: OutcomeResult
    result_r: Decimal
    closed_at: datetime
    strategy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_r", as_decimal(self.result_r, "result_r"))
        object.__setattr__(self, "closed_at", as_utc(self.closed_at, "closed_at"))
        if not self.signal_id or not self.strategy_version.strip():
            raise DomainValidationError("Outcome sample identity and strategy are required")
        if self.result is OutcomeResult.WIN and self.result_r <= 0:
            raise DomainValidationError("WIN outcome requires positive R")
        if self.result is OutcomeResult.LOSS and self.result_r >= 0:
            raise DomainValidationError("LOSS outcome requires negative R")
        if self.result is OutcomeResult.BREAK_EVEN and self.result_r != 0:
            raise DomainValidationError("BREAK_EVEN outcome requires exactly zero R")


@dataclass(frozen=True, slots=True)
class VariantStatistics:
    variant: OutcomeVariant
    resolved: int
    wins: int
    losses: int
    break_even: int
    early_exits: int
    positive: int
    negative: int
    flat: int
    strict_win_rate_percent: Decimal | None
    strict_win_rate_95_low_percent: Decimal | None
    strict_win_rate_95_high_percent: Decimal | None
    decisive_win_rate_percent: Decimal | None
    positive_rate_percent: Decimal | None
    net_r: Decimal
    average_r: Decimal | None
    median_r: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_r: Decimal

    def as_payload(self) -> dict[str, Any]:
        return {
            "variant": self.variant.value,
            "resolved": self.resolved,
            "wins": self.wins,
            "losses": self.losses,
            "break_even": self.break_even,
            "early_exits": self.early_exits,
            "positive": self.positive,
            "negative": self.negative,
            "flat": self.flat,
            "strict_win_rate_percent": _decimal_payload(self.strict_win_rate_percent),
            "strict_win_rate_95_low_percent": _decimal_payload(self.strict_win_rate_95_low_percent),
            "strict_win_rate_95_high_percent": _decimal_payload(
                self.strict_win_rate_95_high_percent
            ),
            "decisive_win_rate_percent": _decimal_payload(self.decisive_win_rate_percent),
            "positive_rate_percent": _decimal_payload(self.positive_rate_percent),
            "net_r": _decimal_payload(self.net_r),
            "average_r": _decimal_payload(self.average_r),
            "median_r": _decimal_payload(self.median_r),
            "profit_factor": _decimal_payload(self.profit_factor),
            "max_drawdown_r": _decimal_payload(self.max_drawdown_r),
        }


@dataclass(frozen=True, slots=True)
class ComparisonStatistics:
    completed_pairs: int
    managed_better: int
    fixed_better: int
    ties: int
    unresolved_fixed: int
    unresolved_managed: int
    average_managed_delta_r: Decimal | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "completed_pairs": self.completed_pairs,
            "managed_better": self.managed_better,
            "fixed_better": self.fixed_better,
            "ties": self.ties,
            "unresolved_fixed": self.unresolved_fixed,
            "unresolved_managed": self.unresolved_managed,
            "average_managed_delta_r": _decimal_payload(self.average_managed_delta_r),
        }


@dataclass(frozen=True, slots=True)
class StatisticsReport:
    calculated_at: datetime
    fixed: VariantStatistics
    managed: VariantStatistics
    comparison: ComparisonStatistics
    strategy_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "calculated_at", as_utc(self.calculated_at, "calculated_at"))
        object.__setattr__(self, "strategy_counts", tuple(self.strategy_counts))

    def as_payload(self) -> dict[str, Any]:
        return {
            "calculated_at": self.calculated_at.isoformat().replace("+00:00", "Z"),
            "fixed": self.fixed.as_payload(),
            "managed": self.managed.as_payload(),
            "comparison": self.comparison.as_payload(),
            "strategy_counts": dict(self.strategy_counts),
            "rate_policy": {
                "strict_win_rate": "WIN / all resolved outcomes",
                "decisive_win_rate": "WIN / (WIN + LOSS); excludes break-even and early exits",
                "positive_rate": "result_r > 0 / all resolved outcomes",
            },
        }

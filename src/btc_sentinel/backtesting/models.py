"""Immutable records for conservative chronological backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from btc_sentinel.domain.enums import MarketRegime, OutcomeVariant, Side
from btc_sentinel.domain.models import Signal, as_decimal, as_utc
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.models import CandleSeries
from btc_sentinel.statistics import VariantStatistics


class BacktestOutcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"
    NO_FILL = "NO_FILL"
    UNRESOLVED = "UNRESOLVED"


class BacktestVerdict(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class HistoricalSignalCase:
    signal: Signal
    future_candles: CandleSeries
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", as_utc(self.as_of, "as_of"))
        if self.as_of <= self.signal.terms.created_at:
            raise DomainValidationError("Backtest as_of must be after signal creation")


@dataclass(frozen=True, slots=True)
class BacktestRunSpec:
    dataset_id: str
    coverage_start: datetime
    coverage_end: datetime
    strategy_version: str
    source_coverage: tuple[str, ...]
    excluded_features: tuple[str, ...]
    exhaustive_candidate_scan: bool
    fill_policy: str = "conservative-one-minute-v2"

    def __post_init__(self) -> None:
        for name in ("coverage_start", "coverage_end"):
            object.__setattr__(self, name, as_utc(getattr(self, name), name))
        for name in ("source_coverage", "excluded_features"):
            object.__setattr__(self, name, tuple(dict.fromkeys(getattr(self, name))))
        if not self.dataset_id.strip() or not self.strategy_version.strip():
            raise DomainValidationError("Backtest dataset and strategy identity are required")
        if self.coverage_end <= self.coverage_start:
            raise DomainValidationError("Backtest coverage range is invalid")
        if "SPOT_1m" not in self.source_coverage:
            raise DomainValidationError("Backtest source coverage must include SPOT_1m")
        if not self.exhaustive_candidate_scan:
            raise DomainValidationError("Backtest must scan the declared candidate universe")
        if self.fill_policy != "conservative-one-minute-v2":
            raise DomainValidationError("Backtest fill policy must match lifecycle baseline")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_id: str
    created_at: datetime
    terminal_at: datetime
    side: Side
    regime: MarketRegime
    variant: OutcomeVariant
    setup_score: int
    strategy_version: str
    planned_rr: Decimal
    outcome: BacktestOutcome
    result_r: Decimal | None
    entry_price: Decimal | None
    exit_price: Decimal | None
    original_stop: Decimal
    estimated_cost_rate: Decimal
    ambiguous: bool = False

    def __post_init__(self) -> None:
        for name in ("created_at", "terminal_at"):
            object.__setattr__(self, name, as_utc(getattr(self, name), name))
        object.__setattr__(self, "planned_rr", as_decimal(self.planned_rr, "planned_rr"))
        for name in (
            "result_r",
            "entry_price",
            "exit_price",
            "original_stop",
            "estimated_cost_rate",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_decimal(value, name))
        if (
            not self.signal_id
            or not self.strategy_version.strip()
            or self.terminal_at <= self.created_at
        ):
            raise DomainValidationError("Backtest trade identity or chronology is invalid")
        if not 0 <= self.setup_score <= 100 or self.planned_rr < Decimal("2"):
            raise DomainValidationError("Backtest setup score or planned R:R is invalid")
        resolved = self.outcome in {
            BacktestOutcome.WIN,
            BacktestOutcome.LOSS,
            BacktestOutcome.BREAK_EVEN,
        }
        if resolved != (self.result_r is not None):
            raise DomainValidationError("Only resolved backtest outcomes have result R")
        if resolved and (self.entry_price is None or self.exit_price is None):
            raise DomainValidationError("Resolved backtest outcome requires entry and exit prices")
        if self.outcome is BacktestOutcome.WIN and self.result_r <= 0:
            raise DomainValidationError("Backtest WIN requires positive R")
        if self.outcome is BacktestOutcome.LOSS and self.result_r >= 0:
            raise DomainValidationError("Backtest LOSS requires negative R")
        if self.outcome is BacktestOutcome.BREAK_EVEN and self.result_r != 0:
            raise DomainValidationError("Backtest BREAK_EVEN requires zero R")
        if self.original_stop <= 0 or not Decimal("0") <= self.estimated_cost_rate <= Decimal(
            "0.01"
        ):
            raise DomainValidationError("Backtest stop or estimated cost rate is invalid")
        if self.outcome is BacktestOutcome.NO_FILL and self.entry_price is not None:
            raise DomainValidationError("NO_FILL cannot contain an entry price")
        if resolved:
            recomputed = self.result_at_cost_multiplier(Decimal("1"))
            if abs(recomputed - self.result_r) > Decimal("1e-18"):
                raise DomainValidationError("Backtest result R does not match prices and costs")
        if self.outcome is BacktestOutcome.WIN and self.result_r < self.planned_rr:
            raise DomainValidationError("Backtest WIN did not reach its planned R:R")

    @property
    def resolved(self) -> bool:
        return self.outcome in {
            BacktestOutcome.WIN,
            BacktestOutcome.LOSS,
            BacktestOutcome.BREAK_EVEN,
        }

    def result_at_cost_multiplier(self, multiplier: Decimal) -> Decimal | None:
        factor = as_decimal(multiplier, "cost multiplier")
        if factor < 1:
            raise DomainValidationError("Cost multiplier cannot be below one")
        if not self.resolved:
            return None
        cost = self.entry_price * self.estimated_cost_rate * factor
        initial_risk = abs(self.entry_price - self.original_stop)
        gross = (
            self.exit_price - self.entry_price
            if self.side is Side.LONG
            else self.entry_price - self.exit_price
        )
        return (gross - cost) / (initial_risk + cost)


@dataclass(frozen=True, slots=True)
class WalkForwardPolicy:
    train_size: int = 120
    test_size: int = 40
    purge_size: int = 4
    minimum_train_trades: int = 30
    minimum_out_of_sample_trades: int = 100
    minimum_folds: int = 3
    minimum_regime_trades: int = 20
    score_thresholds: tuple[int, ...] = (75, 80, 85)
    primary_score_threshold: int = 80
    target_win_rate_percent: Decimal = Decimal("60")
    required_planned_rr: Decimal = Decimal("2")
    cost_multipliers: tuple[Decimal, ...] = (
        Decimal("1"),
        Decimal("1.5"),
        Decimal("2"),
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_win_rate_percent",
            as_decimal(self.target_win_rate_percent, "target_win_rate_percent"),
        )
        object.__setattr__(
            self,
            "required_planned_rr",
            as_decimal(self.required_planned_rr, "required_planned_rr"),
        )
        object.__setattr__(self, "score_thresholds", tuple(self.score_thresholds))
        object.__setattr__(
            self,
            "cost_multipliers",
            tuple(as_decimal(value, "cost multiplier") for value in self.cost_multipliers),
        )
        if (
            min(
                self.train_size,
                self.test_size,
                self.minimum_train_trades,
                self.minimum_out_of_sample_trades,
                self.minimum_folds,
                self.minimum_regime_trades,
            )
            < 1
        ):
            raise DomainValidationError("Walk-forward sizes and minimums must be positive")
        if self.purge_size < 0 or not self.score_thresholds:
            raise DomainValidationError("Purge size and score thresholds are invalid")
        if tuple(sorted(set(self.score_thresholds))) != self.score_thresholds:
            raise DomainValidationError("Score thresholds must be unique and increasing")
        if not all(60 <= value <= 100 for value in self.score_thresholds):
            raise DomainValidationError("Score thresholds must be between 60 and 100")
        if self.primary_score_threshold not in self.score_thresholds:
            raise DomainValidationError("Primary score threshold must be in sensitivity thresholds")
        if not Decimal("0") < self.target_win_rate_percent < Decimal("100"):
            raise DomainValidationError("Target win rate must be between zero and 100")
        if self.required_planned_rr < Decimal("2"):
            raise DomainValidationError("Required planned R:R cannot be below 2")
        if (
            not self.cost_multipliers
            or self.cost_multipliers[0] != 1
            or tuple(sorted(set(self.cost_multipliers))) != self.cost_multipliers
        ):
            raise DomainValidationError(
                "Cost multipliers must be unique, increasing, and start at one"
            )


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_number: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    selected_threshold: int
    training_resolved: int
    testing_resolved: int
    testing_net_r: Decimal


@dataclass(frozen=True, slots=True)
class SensitivityResult:
    threshold: int
    resolved: int
    average_r: Decimal | None
    net_r: Decimal


@dataclass(frozen=True, slots=True)
class CostStressResult:
    multiplier: Decimal
    resolved: int
    average_r: Decimal | None
    net_r: Decimal


@dataclass(frozen=True, slots=True)
class BacktestReport:
    verdict: BacktestVerdict
    variant: OutcomeVariant
    generated_at: datetime
    statistics: VariantStatistics
    folds: tuple[WalkForwardFold, ...]
    sensitivity: tuple[SensitivityResult, ...]
    cost_stress: tuple[CostStressResult, ...]
    regime_counts: tuple[tuple[MarketRegime, int], ...]
    candidate_count: int
    no_fill_count: int
    unresolved_count: int
    reasons: tuple[str, ...]
    policy: WalkForwardPolicy
    run_spec: BacktestRunSpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", as_utc(self.generated_at, "generated_at"))
        for name in ("folds", "sensitivity", "cost_stress", "regime_counts", "reasons"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.candidate_count < self.no_fill_count + self.unresolved_count:
            raise DomainValidationError("Backtest report candidate counts are contradictory")
        if self.verdict is not BacktestVerdict.PASSED and not self.reasons:
            raise DomainValidationError("Non-passing backtest report requires reasons")


@dataclass(frozen=True, slots=True)
class BacktestComparisonReport:
    fixed: BacktestReport
    managed: BacktestReport
    completed_pairs: int
    managed_better: int
    fixed_better: int
    ties: int
    unresolved_fixed: int
    unresolved_managed: int
    average_managed_delta_r: Decimal | None

    def __post_init__(self) -> None:
        counts = (
            self.completed_pairs,
            self.managed_better,
            self.fixed_better,
            self.ties,
            self.unresolved_fixed,
            self.unresolved_managed,
        )
        if any(count < 0 for count in counts):
            raise DomainValidationError("Backtest comparison counts cannot be negative")
        if self.managed_better + self.fixed_better + self.ties != self.completed_pairs:
            raise DomainValidationError("Backtest comparison pair counts are contradictory")
        if (self.completed_pairs == 0) != (self.average_managed_delta_r is None):
            raise DomainValidationError("Backtest comparison average requires completed pairs")

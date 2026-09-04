"""Exhaustive point-in-time signal evaluation over disk-backed history."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from btc_sentinel.backtesting.engine import BacktestEngine
from btc_sentinel.backtesting.models import (
    BacktestComparisonReport,
    BacktestRunSpec,
    BacktestTrade,
    WalkForwardPolicy,
)
from btc_sentinel.backtesting.replay import HistoricalReplayStore
from btc_sentinel.backtesting.simulator import IncrementalTradeReplay
from btc_sentinel.domain.enums import OutcomeVariant
from btc_sentinel.domain.ids import format_signal_id
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval
from btc_sentinel.news.models import CoverageIssue, RiskAssessment, RiskDecision
from btc_sentinel.signals import SignalDecision, SignalEngine, SignalHistory
from btc_sentinel.time_utils import ensure_utc, to_casablanca


class HistoricalRiskProvider(Protocol):
    """Supply risk knowledge captured no later than each historical decision."""

    source_coverage: tuple[str, ...]
    excluded_features: tuple[str, ...]
    performance_eligible: bool

    def assessment_at(self, as_of: datetime) -> RiskAssessment: ...


@dataclass(frozen=True, slots=True)
class FailClosedHistoricalRiskProvider:
    """Default provider that blocks instead of pretending missing history was clear."""

    source_coverage: tuple[str, ...] = ()
    excluded_features: tuple[str, ...] = ("historical_news_and_macro",)
    performance_eligible: bool = False

    def assessment_at(self, as_of: datetime) -> RiskAssessment:
        now = ensure_utc(as_of)
        issue = CoverageIssue(
            "historical_news_and_macro",
            "No verified point-in-time historical risk source was supplied",
            True,
        )
        return RiskAssessment(
            evaluated_at=now,
            decision=RiskDecision.BLOCK,
            block_until=None,
            events=(),
            scheduled_events=(),
            reasons=("required historical news and macro coverage is unavailable",),
            coverage_issues=(issue,),
        )


@dataclass(frozen=True, slots=True)
class HistoricalReplayRun:
    dataset_id: str
    strategy_version: str
    coverage_start: datetime
    coverage_end: datetime
    candidate_count: int
    created_signal_count: int
    fixed_trades: tuple[BacktestTrade, ...]
    managed_trades: tuple[BacktestTrade, ...]
    rejection_counts: tuple[tuple[str, int], ...]
    risk_source_coverage: tuple[str, ...]
    excluded_features: tuple[str, ...]
    performance_eligible: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage_start", ensure_utc(self.coverage_start))
        object.__setattr__(self, "coverage_end", ensure_utc(self.coverage_end))
        for name in (
            "fixed_trades",
            "managed_trades",
            "rejection_counts",
            "risk_source_coverage",
            "excluded_features",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if (
            not self.dataset_id
            or not self.strategy_version
            or self.coverage_end <= self.coverage_start
            or self.candidate_count < 0
        ):
            raise DomainValidationError("Historical replay run range or count is invalid")
        if self.created_signal_count != len(self.fixed_trades) or self.created_signal_count != len(
            self.managed_trades
        ):
            raise DomainValidationError("Historical replay trade counts are contradictory")

    def evaluate(
        self,
        generated_at: datetime,
        policy: WalkForwardPolicy | None = None,
    ) -> BacktestComparisonReport:
        if not self.performance_eligible:
            raise DomainValidationError(
                "Historical run cannot produce a verdict without eligible risk coverage"
            )
        run_spec = BacktestRunSpec(
            dataset_id=self.dataset_id,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            strategy_version=self.strategy_version,
            source_coverage=("SPOT_1m", "SPOT_15m_to_1M", *self.risk_source_coverage),
            excluded_features=(
                "historical_futures_candles",
                "historical_funding",
                "historical_open_interest",
                "historical_taker_volume",
                "historical_order_book",
                "historical_liquidations",
                *self.excluded_features,
            ),
            exhaustive_candidate_scan=True,
        )
        return BacktestEngine(policy).compare_trades(
            self.fixed_trades,
            self.managed_trades,
            ensure_utc(generated_at),
            run_spec,
        )


class HistoricalReplayRunner:
    """Evaluate every 15-minute boundary and stream future lifecycle candles."""

    def __init__(self, signal_engine: SignalEngine | None = None) -> None:
        self.signal_engine = signal_engine or SignalEngine()

    def run(
        self,
        store: HistoricalReplayStore,
        start: datetime,
        end: datetime,
        risk_provider: HistoricalRiskProvider | None = None,
    ) -> HistoricalReplayRun:
        first = ensure_utc(start)
        stop = ensure_utc(end)
        coverage_start, coverage_end = store.coverage()
        if first < coverage_start or stop > coverage_end or stop <= first:
            raise DomainValidationError("Historical run falls outside replay-store coverage")
        provider = risk_provider or FailClosedHistoricalRiskProvider()
        if set(provider.source_coverage) & set(provider.excluded_features):
            raise DomainValidationError("Historical risk coverage and exclusions overlap")
        if provider.performance_eligible and not provider.source_coverage:
            raise DomainValidationError("Performance-eligible risk coverage cannot be empty")

        candidates = store.candidate_times(first, stop)
        fixed: list[BacktestTrade] = []
        managed: list[BacktestTrade] = []
        rejections: Counter[str] = Counter()
        last_signal_at: datetime | None = None
        managed_active_until: datetime | None = None

        for sequence, candidate in enumerate(candidates, start=1):
            try:
                view = store.market_view(candidate)
            except DomainValidationError:
                rejections["historical market warm-up is incomplete"] += 1
                continue
            risk = provider.assessment_at(candidate)
            self._validate_risk(candidate, risk)
            active = managed_active_until is not None and candidate < managed_active_until
            signal_id = format_signal_id(to_casablanca(candidate).date(), sequence)
            evaluation = self.signal_engine.evaluate(
                signal_id,
                view,
                risk,
                candidate,
                SignalHistory(last_signal_at, active),
            )
            if evaluation.decision is SignalDecision.NO_SIGNAL:
                rejections.update(evaluation.rejection_reasons)
                continue
            assert evaluation.signal is not None
            fixed_trade, managed_trade = self._simulate(store, evaluation.signal, stop)
            fixed.append(fixed_trade)
            managed.append(managed_trade)
            last_signal_at = candidate
            managed_active_until = managed_trade.terminal_at

        return HistoricalReplayRun(
            dataset_id=store.metadata("dataset_id"),
            strategy_version=self.signal_engine.strategy_version,
            coverage_start=first,
            coverage_end=stop,
            candidate_count=len(candidates),
            created_signal_count=len(fixed),
            fixed_trades=tuple(fixed),
            managed_trades=tuple(managed),
            rejection_counts=tuple(sorted(rejections.items())),
            risk_source_coverage=provider.source_coverage,
            excluded_features=provider.excluded_features,
            performance_eligible=provider.performance_eligible,
        )

    @staticmethod
    def _validate_risk(candidate: datetime, risk: RiskAssessment) -> None:
        if risk.evaluated_at != candidate:
            raise DomainValidationError("Historical risk assessment must match candidate time")
        if any(event.published_at > candidate for event in risk.events):
            raise DomainValidationError("Historical risk assessment contains future news")
        if (
            any(issue.required for issue in risk.coverage_issues)
            and risk.decision is not RiskDecision.BLOCK
        ):
            raise DomainValidationError("Missing required historical risk coverage must block")

    @staticmethod
    def _simulate(store, signal, end) -> tuple[BacktestTrade, BacktestTrade]:
        fixed = IncrementalTradeReplay(signal, OutcomeVariant.FIXED)
        managed = IncrementalTradeReplay(signal, OutcomeVariant.MANAGED)
        for candle in store.iter_candles(MarketInterval.ONE_MINUTE, signal.terms.created_at, end):
            if fixed.trade is None:
                fixed.advance(candle)
            if managed.trade is None:
                managed.advance(candle)
            if fixed.trade is not None and managed.trade is not None:
                break
        return fixed.finish(end), managed.finish(end)

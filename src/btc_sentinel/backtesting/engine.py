"""Walk-forward evaluation that keeps training choices out of test windows."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from itertools import pairwise

from btc_sentinel.backtesting.models import (
    BacktestComparisonReport,
    BacktestOutcome,
    BacktestReport,
    BacktestRunSpec,
    BacktestTrade,
    BacktestVerdict,
    CostStressResult,
    HistoricalSignalCase,
    SensitivityResult,
    WalkForwardFold,
    WalkForwardPolicy,
)
from btc_sentinel.backtesting.simulator import simulate_fixed_case, simulate_managed_case
from btc_sentinel.domain.enums import MarketRegime, OutcomeResult, OutcomeVariant
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.statistics import OutcomeSample, VariantStatistics, calculate_statistics
from btc_sentinel.time_utils import ensure_utc

_DIRECTIONAL_REGIMES = (MarketRegime.BULLISH_TREND, MarketRegime.BEARISH_TREND)


def _statistics(
    trades: tuple[BacktestTrade, ...],
    as_of: datetime,
    variant: OutcomeVariant,
    cost_multiplier: Decimal = Decimal("1"),
) -> VariantStatistics:
    def category(trade: BacktestTrade, result_r: Decimal) -> OutcomeResult:
        if trade.outcome is BacktestOutcome.WIN:
            return OutcomeResult.WIN
        if trade.outcome is BacktestOutcome.LOSS:
            return OutcomeResult.LOSS
        return OutcomeResult.BREAK_EVEN if result_r == 0 else OutcomeResult.EARLY_EXIT

    samples = tuple(
        OutcomeSample(
            signal_id=trade.signal_id,
            variant=variant,
            result=category(trade, trade.result_at_cost_multiplier(cost_multiplier)),
            result_r=trade.result_at_cost_multiplier(cost_multiplier),
            closed_at=trade.terminal_at,
            strategy_version="backtest-v0.11.0",
        )
        for trade in trades
        if trade.resolved
    )
    report = calculate_statistics(samples, as_of)
    return report.fixed if variant is OutcomeVariant.FIXED else report.managed


def _eligible(trades: tuple[BacktestTrade, ...], threshold: int) -> tuple[BacktestTrade, ...]:
    return tuple(trade for trade in trades if trade.resolved and trade.setup_score >= threshold)


class BacktestEngine:
    """Simulate historical signals and judge only non-overlapping out-of-sample folds."""

    def __init__(
        self,
        policy: WalkForwardPolicy | None = None,
        variant: OutcomeVariant = OutcomeVariant.FIXED,
    ) -> None:
        self.policy = policy or WalkForwardPolicy()
        self.variant = variant

    def simulate(self, cases: tuple[HistoricalSignalCase, ...]) -> tuple[BacktestTrade, ...]:
        ordered = tuple(
            sorted(cases, key=lambda case: (case.signal.terms.created_at, case.signal.signal_id))
        )
        if tuple(cases) != ordered:
            raise DomainValidationError("Historical cases must be chronological")
        simulator = (
            simulate_fixed_case if self.variant is OutcomeVariant.FIXED else simulate_managed_case
        )
        trades = tuple(simulator(case) for case in ordered)
        for previous, current in pairwise(trades):
            if current.created_at < previous.terminal_at:
                raise DomainValidationError(
                    "Historical cases overlap and violate one-active-trade policy"
                )
        return trades

    def evaluate(
        self,
        trades: tuple[BacktestTrade, ...],
        generated_at: datetime,
        run_spec: BacktestRunSpec,
    ) -> BacktestReport:
        now = ensure_utc(generated_at)
        ordered = tuple(sorted(trades, key=lambda trade: (trade.created_at, trade.signal_id)))
        if tuple(trades) != ordered:
            raise DomainValidationError("Backtest trades must be chronological")
        if len({trade.signal_id for trade in trades}) != len(trades):
            raise DomainValidationError("Backtest signal IDs must be unique")
        if any(trade.variant is not self.variant for trade in trades):
            raise DomainValidationError("Backtest trades do not match the selected track variant")
        if any(trade.terminal_at > now for trade in trades):
            raise DomainValidationError("Backtest cannot include outcomes after generation time")
        if now < run_spec.coverage_end:
            raise DomainValidationError("Backtest generation time precedes dataset coverage end")
        if any(
            trade.created_at < run_spec.coverage_start or trade.terminal_at > run_spec.coverage_end
            for trade in trades
        ):
            raise DomainValidationError("Backtest trade falls outside declared dataset coverage")
        if any(trade.strategy_version != run_spec.strategy_version for trade in trades):
            raise DomainValidationError("Backtest mixes undeclared strategy versions")

        for previous, current in pairwise(ordered):
            if current.created_at < previous.terminal_at:
                raise DomainValidationError("Backtest trades overlap one-active-trade policy")

        folds, selected_oos, all_oos = self._walk_forward(ordered, now)
        metrics = _statistics(selected_oos, now, self.variant)
        sensitivity = tuple(
            self._sensitivity(all_oos, threshold, now) for threshold in self.policy.score_thresholds
        )
        cost_stress = tuple(
            self._cost_stress(selected_oos, multiplier, now)
            for multiplier in self.policy.cost_multipliers
        )
        regimes = Counter(trade.regime for trade in selected_oos if trade.resolved)
        regime_counts = tuple(
            (regime, regimes[regime]) for regime in MarketRegime if regimes[regime]
        )
        reasons, verdict = self._verdict(
            metrics,
            folds,
            sensitivity,
            cost_stress,
            regimes,
            selected_oos,
        )
        return BacktestReport(
            verdict=verdict,
            variant=self.variant,
            generated_at=now,
            statistics=metrics,
            folds=folds,
            sensitivity=sensitivity,
            cost_stress=cost_stress,
            regime_counts=regime_counts,
            candidate_count=len(ordered),
            no_fill_count=sum(trade.outcome is BacktestOutcome.NO_FILL for trade in ordered),
            unresolved_count=sum(trade.outcome is BacktestOutcome.UNRESOLVED for trade in ordered),
            reasons=reasons,
            policy=self.policy,
            run_spec=run_spec,
        )

    def _walk_forward(
        self, trades: tuple[BacktestTrade, ...], now: datetime
    ) -> tuple[
        tuple[WalkForwardFold, ...],
        tuple[BacktestTrade, ...],
        tuple[BacktestTrade, ...],
    ]:
        policy = self.policy
        folds: list[WalkForwardFold] = []
        selected: list[BacktestTrade] = []
        all_test: list[BacktestTrade] = []
        test_start = policy.train_size + policy.purge_size
        fold_number = 1
        while test_start + policy.test_size <= len(trades):
            train = trades[
                test_start - policy.purge_size - policy.train_size : test_start - policy.purge_size
            ]
            test = trades[test_start : test_start + policy.test_size]
            threshold = policy.primary_score_threshold
            test_selected = _eligible(test, threshold)
            test_stats = _statistics(test_selected, now, self.variant)
            folds.append(
                WalkForwardFold(
                    fold_number=fold_number,
                    train_start=train[0].created_at,
                    train_end=train[-1].terminal_at,
                    test_start=test[0].created_at,
                    test_end=test[-1].terminal_at,
                    selected_threshold=threshold,
                    training_resolved=len(_eligible(train, threshold)),
                    testing_resolved=test_stats.resolved,
                    testing_net_r=test_stats.net_r,
                )
            )
            selected.extend(test_selected)
            all_test.extend(test)
            fold_number += 1
            test_start += policy.test_size
        return tuple(folds), tuple(selected), tuple(all_test)

    def _sensitivity(
        self, trades: tuple[BacktestTrade, ...], threshold: int, now: datetime
    ) -> SensitivityResult:
        stats = _statistics(_eligible(trades, threshold), now, self.variant)
        return SensitivityResult(threshold, stats.resolved, stats.average_r, stats.net_r)

    def _cost_stress(
        self, trades: tuple[BacktestTrade, ...], multiplier: Decimal, now: datetime
    ) -> CostStressResult:
        stats = _statistics(trades, now, self.variant, multiplier)
        return CostStressResult(multiplier, stats.resolved, stats.average_r, stats.net_r)

    def _verdict(
        self,
        metrics: VariantStatistics,
        folds: tuple[WalkForwardFold, ...],
        sensitivity: tuple[SensitivityResult, ...],
        cost_stress: tuple[CostStressResult, ...],
        regimes: Counter[MarketRegime],
        selected_oos: tuple[BacktestTrade, ...],
    ) -> tuple[tuple[str, ...], BacktestVerdict]:
        reasons: list[str] = []
        inconclusive = False
        if len(folds) < self.policy.minimum_folds:
            reasons.append(
                f"only {len(folds)} walk-forward folds; need {self.policy.minimum_folds}"
            )
            inconclusive = True
        thin_training = tuple(
            fold.fold_number
            for fold in folds
            if fold.training_resolved < self.policy.minimum_train_trades
        )
        if thin_training:
            reasons.append(f"insufficient resolved training trades in folds {thin_training}")
            inconclusive = True
        if metrics.resolved < self.policy.minimum_out_of_sample_trades:
            reasons.append(
                f"only {metrics.resolved} out-of-sample trades; "
                f"need {self.policy.minimum_out_of_sample_trades}"
            )
            inconclusive = True
        for regime in _DIRECTIONAL_REGIMES:
            if regimes[regime] < self.policy.minimum_regime_trades:
                reasons.append(
                    f"{regime.value} has {regimes[regime]} out-of-sample trades; "
                    f"need {self.policy.minimum_regime_trades}"
                )
                inconclusive = True
        if inconclusive:
            return tuple(reasons), BacktestVerdict.INCONCLUSIVE

        target = self.policy.target_win_rate_percent
        if metrics.strict_win_rate_percent is None or metrics.strict_win_rate_percent <= target:
            reasons.append(f"observed strict win rate does not exceed {target}%")
        if (
            metrics.strict_win_rate_95_low_percent is None
            or metrics.strict_win_rate_95_low_percent <= target
        ):
            reasons.append(f"95% lower confidence bound does not exceed {target}%")
        if metrics.average_r is None or metrics.average_r <= 0 or metrics.net_r <= 0:
            reasons.append("out-of-sample expectancy is not positive")
        if any(trade.planned_rr < self.policy.required_planned_rr for trade in selected_oos):
            reasons.append(
                f"an out-of-sample trade planned less than {self.policy.required_planned_rr}R"
            )
        unstable = tuple(
            item.threshold
            for item in sensitivity
            if item.resolved >= self.policy.minimum_regime_trades
            and (item.average_r is None or item.average_r <= 0)
        )
        if unstable:
            reasons.append(f"score-threshold sensitivity is negative at {unstable}")
        failed_costs = tuple(
            item.multiplier for item in cost_stress if item.average_r is None or item.average_r <= 0
        )
        if failed_costs:
            reasons.append(f"expectancy is not positive under cost multipliers {failed_costs}")
        if reasons:
            return tuple(reasons), BacktestVerdict.FAILED
        return (), BacktestVerdict.PASSED

    def compare_variants(
        self,
        cases: tuple[HistoricalSignalCase, ...],
        generated_at: datetime,
        run_spec: BacktestRunSpec,
    ) -> BacktestComparisonReport:
        fixed_engine = BacktestEngine(self.policy, OutcomeVariant.FIXED)
        managed_engine = BacktestEngine(self.policy, OutcomeVariant.MANAGED)
        fixed_trades = fixed_engine.simulate(cases)
        managed_trades = managed_engine.simulate(cases)
        fixed_report = fixed_engine.evaluate(fixed_trades, generated_at, run_spec)
        managed_report = managed_engine.evaluate(managed_trades, generated_at, run_spec)

        fixed_test = fixed_engine._walk_forward(fixed_trades, generated_at)[2]
        managed_test = managed_engine._walk_forward(managed_trades, generated_at)[2]
        threshold = self.policy.primary_score_threshold
        fixed_by_id = {
            trade.signal_id: trade for trade in fixed_test if trade.setup_score >= threshold
        }
        managed_by_id = {
            trade.signal_id: trade for trade in managed_test if trade.setup_score >= threshold
        }
        paired_ids = sorted(
            signal_id
            for signal_id in fixed_by_id.keys() & managed_by_id.keys()
            if fixed_by_id[signal_id].resolved and managed_by_id[signal_id].resolved
        )
        deltas = tuple(
            managed_by_id[signal_id].result_r - fixed_by_id[signal_id].result_r
            for signal_id in paired_ids
        )
        fixed_resolved = {key for key, value in fixed_by_id.items() if value.resolved}
        managed_resolved = {key for key, value in managed_by_id.items() if value.resolved}
        return BacktestComparisonReport(
            fixed=fixed_report,
            managed=managed_report,
            completed_pairs=len(deltas),
            managed_better=sum(delta > 0 for delta in deltas),
            fixed_better=sum(delta < 0 for delta in deltas),
            ties=sum(delta == 0 for delta in deltas),
            unresolved_fixed=len(managed_resolved - fixed_resolved),
            unresolved_managed=len(fixed_resolved - managed_resolved),
            average_managed_delta_r=(
                None if not deltas else sum(deltas, Decimal("0")) / Decimal(len(deltas))
            ),
        )

"""Pure deterministic calculations over immutable outcome samples."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal

from btc_sentinel.domain.enums import OutcomeResult, OutcomeVariant
from btc_sentinel.statistics.models import (
    ComparisonStatistics,
    OutcomeSample,
    StatisticsReport,
    VariantStatistics,
)
from btc_sentinel.time_utils import ensure_utc

_HUNDRED = Decimal("100")
_Z_95 = Decimal("1.959963984540054")


def _rate(numerator: int, denominator: int) -> Decimal | None:
    return None if denominator == 0 else Decimal(numerator) * _HUNDRED / Decimal(denominator)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _wilson_interval(wins: int, total: int) -> tuple[Decimal | None, Decimal | None]:
    if total == 0:
        return None, None
    count = Decimal(total)
    probability = Decimal(wins) / count
    z_squared = _Z_95 * _Z_95
    denominator = Decimal("1") + z_squared / count
    center = (probability + z_squared / (Decimal("2") * count)) / denominator
    variance = (
        probability * (Decimal("1") - probability) + z_squared / (Decimal("4") * count)
    ) / count
    margin = _Z_95 * variance.sqrt() / denominator
    return (
        max(Decimal("0"), center - margin) * _HUNDRED,
        min(Decimal("1"), center + margin) * _HUNDRED,
    )


def _max_drawdown(values: tuple[Decimal, ...]) -> Decimal:
    cumulative = Decimal("0")
    peak = Decimal("0")
    maximum = Decimal("0")
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def _variant_statistics(
    variant: OutcomeVariant,
    samples: tuple[OutcomeSample, ...],
) -> VariantStatistics:
    selected = tuple(sample for sample in samples if sample.variant is variant)
    values = tuple(sample.result_r for sample in selected)
    wins = sum(sample.result is OutcomeResult.WIN for sample in selected)
    losses = sum(sample.result is OutcomeResult.LOSS for sample in selected)
    break_even = sum(sample.result is OutcomeResult.BREAK_EVEN for sample in selected)
    early_exits = sum(sample.result is OutcomeResult.EARLY_EXIT for sample in selected)
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    flat = sum(value == 0 for value in values)
    gross_positive = sum((value for value in values if value > 0), Decimal("0"))
    gross_negative = -sum((value for value in values if value < 0), Decimal("0"))
    resolved = len(selected)
    net_r = sum(values, Decimal("0"))
    interval_low, interval_high = _wilson_interval(wins, resolved)
    return VariantStatistics(
        variant=variant,
        resolved=resolved,
        wins=wins,
        losses=losses,
        break_even=break_even,
        early_exits=early_exits,
        positive=positive,
        negative=negative,
        flat=flat,
        strict_win_rate_percent=_rate(wins, resolved),
        strict_win_rate_95_low_percent=interval_low,
        strict_win_rate_95_high_percent=interval_high,
        decisive_win_rate_percent=_rate(wins, wins + losses),
        positive_rate_percent=_rate(positive, resolved),
        net_r=net_r,
        average_r=None if not values else net_r / Decimal(resolved),
        median_r=_median(values),
        profit_factor=None if gross_negative == 0 else gross_positive / gross_negative,
        max_drawdown_r=_max_drawdown(values),
    )


def calculate_statistics(
    samples: tuple[OutcomeSample, ...],
    calculated_at: datetime,
) -> StatisticsReport:
    ordered = tuple(
        sorted(samples, key=lambda item: (item.closed_at, item.signal_id, item.variant))
    )
    fixed_by_signal = {
        sample.signal_id: sample for sample in ordered if sample.variant is OutcomeVariant.FIXED
    }
    managed_by_signal = {
        sample.signal_id: sample for sample in ordered if sample.variant is OutcomeVariant.MANAGED
    }
    paired_ids = sorted(fixed_by_signal.keys() & managed_by_signal.keys())
    deltas = tuple(
        managed_by_signal[signal_id].result_r - fixed_by_signal[signal_id].result_r
        for signal_id in paired_ids
    )
    strategy_counts = Counter(sample.strategy_version for sample in ordered)
    return StatisticsReport(
        calculated_at=ensure_utc(calculated_at),
        fixed=_variant_statistics(OutcomeVariant.FIXED, ordered),
        managed=_variant_statistics(OutcomeVariant.MANAGED, ordered),
        comparison=ComparisonStatistics(
            completed_pairs=len(paired_ids),
            managed_better=sum(delta > 0 for delta in deltas),
            fixed_better=sum(delta < 0 for delta in deltas),
            ties=sum(delta == 0 for delta in deltas),
            unresolved_fixed=len(managed_by_signal.keys() - fixed_by_signal.keys()),
            unresolved_managed=len(fixed_by_signal.keys() - managed_by_signal.keys()),
            average_managed_delta_r=(
                None if not deltas else sum(deltas, Decimal("0")) / Decimal(len(deltas))
            ),
        ),
        strategy_counts=tuple(sorted(strategy_counts.items())),
    )

"""Pure conservative fixed-track simulation over completed one-minute candles."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from btc_sentinel.backtesting.models import BacktestOutcome, BacktestTrade, HistoricalSignalCase
from btc_sentinel.domain.enums import OutcomeVariant, Side
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue


def _first_safe_open(case: HistoricalSignalCase):
    created = case.signal.terms.created_at
    aligned = created.replace(second=0, microsecond=0)
    return aligned if created == aligned else aligned + timedelta(minutes=1)


def _result_r(case: HistoricalSignalCase, entry: Decimal, exit_price: Decimal) -> Decimal:
    terms = case.signal.terms
    cost = entry * terms.estimated_round_trip_cost_rate
    gross_risk = (
        entry - terms.original_stop if terms.side is Side.LONG else terms.original_stop - entry
    )
    total_risk = gross_risk + cost
    gross_result = exit_price - entry if terms.side is Side.LONG else entry - exit_price
    return (gross_result - cost) / total_risk


def _trade(
    case: HistoricalSignalCase,
    variant: OutcomeVariant,
    outcome: BacktestOutcome,
    terminal_at,
    entry: Decimal | None,
    exit_price: Decimal | None,
    *,
    ambiguous: bool = False,
) -> BacktestTrade:
    signal = case.signal
    terms = signal.terms
    result_r = None if entry is None or exit_price is None else _result_r(case, entry, exit_price)
    return BacktestTrade(
        signal_id=signal.signal_id,
        created_at=terms.created_at,
        terminal_at=terminal_at,
        side=terms.side,
        regime=signal.regime,
        variant=variant,
        setup_score=signal.setup_score,
        strategy_version=signal.strategy_version,
        planned_rr=terms.planned_r_for(terms.targets[0]),
        outcome=outcome,
        result_r=result_r,
        entry_price=entry,
        exit_price=exit_price,
        original_stop=terms.original_stop,
        estimated_cost_rate=terms.estimated_round_trip_cost_rate,
        ambiguous=ambiguous,
    )


def _simulate(case: HistoricalSignalCase, variant: OutcomeVariant) -> BacktestTrade:
    signal = case.signal
    terms = signal.terms
    series = case.future_candles
    if series.venue is not MarketVenue.SPOT or series.interval is not MarketInterval.ONE_MINUTE:
        raise DomainValidationError("Backtesting requires Spot BTCUSDT one-minute candles")
    if any(not candle.is_closed_at(case.as_of) for candle in series.candles):
        raise DomainValidationError("Backtesting received an incomplete future candle")
    expected = _first_safe_open(case)
    if series.candles[0].open_time != expected:
        raise DomainValidationError("Backtest future candles must begin at the first safe minute")

    entry: Decimal | None = None
    activated_at = None
    current_stop = terms.original_stop
    tp1 = terms.targets[0].price
    for candle in series.candles:
        if entry is None:
            expires_inside = candle.open_time >= terms.expires_at or (
                candle.open_time < terms.expires_at <= candle.close_time
            )
            if expires_inside:
                return _trade(
                    case,
                    variant,
                    BacktestOutcome.NO_FILL,
                    terms.expires_at,
                    None,
                    None,
                )
            touches_entry = candle.low <= terms.entry_high and candle.high >= terms.entry_low
            if not touches_entry:
                continue
            entry = terms.conservative_entry
            activated_at = candle.open_time

        touches_stop = (
            candle.low <= current_stop if terms.side is Side.LONG else candle.high >= current_stop
        )
        touches_target = candle.high >= tp1 if terms.side is Side.LONG else candle.low <= tp1
        activation_candle = candle.open_time == activated_at
        if touches_stop:
            if activation_candle:
                exit_price = current_stop
            elif terms.side is Side.LONG:
                exit_price = min(current_stop, candle.open)
            else:
                exit_price = max(current_stop, candle.open)
            result_r = _result_r(case, entry, exit_price)
            outcome = (
                BacktestOutcome.BREAK_EVEN
                if current_stop != terms.original_stop and result_r == 0
                else BacktestOutcome.LOSS
            )
            return _trade(
                case,
                variant,
                outcome,
                candle.close_time,
                entry,
                exit_price,
                ambiguous=touches_target,
            )
        if touches_target and not activation_candle:
            return _trade(
                case,
                variant,
                BacktestOutcome.WIN,
                candle.close_time,
                entry,
                tp1,
            )
        if (
            variant is OutcomeVariant.MANAGED
            and not activation_candle
            and current_stop == terms.original_stop
            and _result_r(case, entry, candle.close) >= Decimal("1.5")
        ):
            cost = entry * terms.estimated_round_trip_cost_rate
            current_stop = entry + cost if terms.side is Side.LONG else entry - cost

    return _trade(
        case,
        variant,
        BacktestOutcome.UNRESOLVED,
        case.as_of,
        entry,
        None,
    )


def simulate_fixed_case(case: HistoricalSignalCase) -> BacktestTrade:
    """Replay the unchanged Phase 7 baseline."""
    return _simulate(case, OutcomeVariant.FIXED)


def simulate_managed_case(case: HistoricalSignalCase) -> BacktestTrade:
    """Replay the default Phase 8 break-even rule with next-candle effect."""
    return _simulate(case, OutcomeVariant.MANAGED)

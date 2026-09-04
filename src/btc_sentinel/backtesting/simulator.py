"""Pure conservative fixed-track simulation over completed one-minute candles."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from btc_sentinel.backtesting.models import BacktestOutcome, BacktestTrade, HistoricalSignalCase
from btc_sentinel.domain.enums import OutcomeVariant, Side
from btc_sentinel.domain.models import Signal
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle
from btc_sentinel.time_utils import ensure_utc


def _first_safe_open(signal: Signal) -> datetime:
    created = signal.terms.created_at
    aligned = created.replace(second=0, microsecond=0)
    return aligned if created == aligned else aligned + timedelta(minutes=1)


def _result_r(signal: Signal, entry: Decimal, exit_price: Decimal) -> Decimal:
    terms = signal.terms
    cost = entry * terms.estimated_round_trip_cost_rate
    gross_risk = (
        entry - terms.original_stop if terms.side is Side.LONG else terms.original_stop - entry
    )
    total_risk = gross_risk + cost
    gross_result = exit_price - entry if terms.side is Side.LONG else entry - exit_price
    return (gross_result - cost) / total_risk


def _trade(
    signal: Signal,
    variant: OutcomeVariant,
    outcome: BacktestOutcome,
    terminal_at,
    entry: Decimal | None,
    exit_price: Decimal | None,
    *,
    ambiguous: bool = False,
) -> BacktestTrade:
    terms = signal.terms
    result_r = None if entry is None or exit_price is None else _result_r(signal, entry, exit_price)
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


class IncrementalTradeReplay:
    """Consume one completed minute at a time without retaining future history."""

    def __init__(self, signal: Signal, variant: OutcomeVariant) -> None:
        self.signal = signal
        self.variant = variant
        self.entry: Decimal | None = None
        self.activated_at: datetime | None = None
        self.current_stop = signal.terms.original_stop
        self.next_open = _first_safe_open(signal)
        self.trade: BacktestTrade | None = None

    def advance(self, candle: Candle) -> BacktestTrade | None:
        if self.trade is not None:
            return self.trade
        if candle.venue is not MarketVenue.SPOT or candle.interval is not MarketInterval.ONE_MINUTE:
            raise DomainValidationError("Backtesting requires Spot BTCUSDT one-minute candles")
        if candle.open_time != self.next_open:
            raise DomainValidationError("Backtest future candles must be continuous from creation")
        self.next_open = candle.open_time + timedelta(minutes=1)
        terms = self.signal.terms

        if self.entry is None:
            expires_inside = candle.open_time >= terms.expires_at or (
                candle.open_time < terms.expires_at <= candle.close_time
            )
            if expires_inside:
                self.trade = _trade(
                    self.signal,
                    self.variant,
                    BacktestOutcome.NO_FILL,
                    terms.expires_at,
                    None,
                    None,
                )
                return self.trade
            touches_entry = candle.low <= terms.entry_high and candle.high >= terms.entry_low
            if not touches_entry:
                return None
            self.entry = terms.conservative_entry
            self.activated_at = candle.open_time

        touches_stop = (
            candle.low <= self.current_stop
            if terms.side is Side.LONG
            else candle.high >= self.current_stop
        )
        target = terms.targets[0].price
        touches_target = candle.high >= target if terms.side is Side.LONG else candle.low <= target
        activation_candle = candle.open_time == self.activated_at
        if touches_stop:
            if activation_candle:
                exit_price = self.current_stop
            elif terms.side is Side.LONG:
                exit_price = min(self.current_stop, candle.open)
            else:
                exit_price = max(self.current_stop, candle.open)
            result_r = _result_r(self.signal, self.entry, exit_price)
            outcome = (
                BacktestOutcome.BREAK_EVEN
                if self.current_stop != terms.original_stop and result_r == 0
                else BacktestOutcome.LOSS
            )
            self.trade = _trade(
                self.signal,
                self.variant,
                outcome,
                candle.close_time,
                self.entry,
                exit_price,
                ambiguous=touches_target,
            )
            return self.trade
        if touches_target and not activation_candle:
            self.trade = _trade(
                self.signal,
                self.variant,
                BacktestOutcome.WIN,
                candle.close_time,
                self.entry,
                target,
            )
            return self.trade
        if (
            self.variant is OutcomeVariant.MANAGED
            and not activation_candle
            and self.current_stop == terms.original_stop
            and _result_r(self.signal, self.entry, candle.close) >= Decimal("1.5")
        ):
            cost = self.entry * terms.estimated_round_trip_cost_rate
            self.current_stop = self.entry + cost if terms.side is Side.LONG else self.entry - cost
        return None

    def finish(self, as_of: datetime) -> BacktestTrade:
        if self.trade is not None:
            return self.trade
        now = ensure_utc(as_of)
        if now <= self.signal.terms.created_at:
            raise DomainValidationError("Backtest end must follow signal creation")
        self.trade = _trade(
            self.signal,
            self.variant,
            BacktestOutcome.UNRESOLVED,
            now,
            self.entry,
            None,
        )
        return self.trade


def _simulate(case: HistoricalSignalCase, variant: OutcomeVariant) -> BacktestTrade:
    signal = case.signal
    series = case.future_candles
    if series.venue is not MarketVenue.SPOT or series.interval is not MarketInterval.ONE_MINUTE:
        raise DomainValidationError("Backtesting requires Spot BTCUSDT one-minute candles")
    if any(not candle.is_closed_at(case.as_of) for candle in series.candles):
        raise DomainValidationError("Backtesting received an incomplete future candle")
    expected = _first_safe_open(signal)
    if series.candles[0].open_time != expected:
        raise DomainValidationError("Backtest future candles must begin at the first safe minute")
    replay = IncrementalTradeReplay(signal, variant)
    for candle in series.candles:
        if trade := replay.advance(candle):
            return trade
    return replay.finish(case.as_of)


def simulate_fixed_case(case: HistoricalSignalCase) -> BacktestTrade:
    """Replay the unchanged Phase 7 baseline."""
    return _simulate(case, OutcomeVariant.FIXED)


def simulate_managed_case(case: HistoricalSignalCase) -> BacktestTrade:
    """Replay the default Phase 8 break-even rule with next-candle effect."""
    return _simulate(case, OutcomeVariant.MANAGED)

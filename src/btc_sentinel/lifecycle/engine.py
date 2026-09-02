"""Replay completed Spot one-minute candles into durable lifecycle changes."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from btc_sentinel.domain.enums import (
    OutcomeResult,
    OutcomeVariant,
    Side,
    SignalStatus,
    TradeEventType,
)
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.lifecycle.models import LifecycleAction, LifecycleSignal, ReplayResult
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle, CandleSeries
from btc_sentinel.persistence.repository import Repository
from btc_sentinel.time_utils import ensure_utc, iso_utc


class LifecycleReplayEngine:
    """Apply each completed candle once, using deterministic conservative ordering."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def replay(
        self,
        signal_id: str,
        series: CandleSeries,
        as_of: datetime,
    ) -> ReplayResult:
        now = ensure_utc(as_of)
        self._validate_series(series, now)
        key = f"lifecycle:{signal_id}"
        checkpoint = self.repository.get_checkpoint(key)
        state = self.repository.get_lifecycle_signal(signal_id)
        expected_open = (
            checkpoint + timedelta(minutes=1)
            if checkpoint is not None
            else self._first_safe_open(state.created_at)
        )
        candidates = tuple(candle for candle in series.candles if candle.open_time >= expected_open)
        if candidates and candidates[0].open_time != expected_open:
            raise DomainValidationError(
                f"Lifecycle replay gap: expected {iso_utc(expected_open)}, "
                f"received {iso_utc(candidates[0].open_time)}"
            )

        actions: list[LifecycleAction] = []
        processed = 0
        for candle in candidates:
            state = self.repository.get_lifecycle_signal(signal_id)
            activated_now = False
            if state.status is SignalStatus.PENDING:
                if self._must_expire(state, candle):
                    self.repository.expire_signal(
                        signal_id,
                        state.expires_at,
                        self._dedupe(signal_id, candle, "expire"),
                    )
                    actions.append(LifecycleAction.EXPIRED)
                elif self._touches_entry(state, candle):
                    self.repository.activate_signal(
                        signal_id,
                        state.conservative_entry,
                        candle.open_time,
                        self._dedupe(signal_id, candle, "activate"),
                    )
                    actions.append(LifecycleAction.ACTIVATED)
                    activated_now = True

            state = self.repository.get_lifecycle_signal(signal_id)
            if state.active_variants:
                actions.extend(self._process_active(state, candle, activated_now))

            self.repository.advance_checkpoint(
                key,
                candle.open_time,
                {"signal_id": signal_id, "source": "SPOT_1m"},
            )
            checkpoint = candle.open_time
            processed += 1

        final = self.repository.get_lifecycle_signal(signal_id)
        return ReplayResult(signal_id, processed, checkpoint, final.status, tuple(actions))

    @staticmethod
    def _validate_series(series: CandleSeries, now: datetime) -> None:
        if series.venue is not MarketVenue.SPOT or series.interval is not MarketInterval.ONE_MINUTE:
            raise DomainValidationError("Lifecycle replay requires Spot BTCUSDT one-minute candles")
        if any(not candle.is_closed_at(now) for candle in series.candles):
            raise DomainValidationError("Lifecycle replay received an incomplete candle")

    @staticmethod
    def _first_safe_open(created_at: datetime) -> datetime:
        aligned = created_at.replace(second=0, microsecond=0)
        return aligned if created_at == aligned else aligned + timedelta(minutes=1)

    @staticmethod
    def _must_expire(state: LifecycleSignal, candle: Candle) -> bool:
        if candle.open_time >= state.expires_at:
            return True
        return candle.open_time < state.expires_at <= candle.close_time

    @staticmethod
    def _touches_entry(state: LifecycleSignal, candle: Candle) -> bool:
        return candle.low <= state.entry_high and candle.high >= state.entry_low

    @staticmethod
    def _touches_stop(state: LifecycleSignal, candle: Candle) -> bool:
        return (
            candle.low <= state.original_stop
            if state.side is Side.LONG
            else candle.high >= state.original_stop
        )

    @staticmethod
    def _touches_tp1(state: LifecycleSignal, candle: Candle) -> bool:
        target = state.targets[0].price
        return candle.high >= target if state.side is Side.LONG else candle.low <= target

    def _process_active(
        self,
        state: LifecycleSignal,
        candle: Candle,
        activated_now: bool,
    ) -> list[LifecycleAction]:
        stop = self._touches_stop(state, candle)
        target = self._touches_tp1(state, candle)
        activation_candle = activated_now or state.activated_at == candle.open_time
        if stop:
            action = LifecycleAction.AMBIGUOUS_STOP_FIRST if target else LifecycleAction.STOP_CLOSED
            self._close_active_variants(
                state,
                self._stop_fill(state, candle, activation_candle),
                OutcomeResult.LOSS,
                TradeEventType.STOP_LOSS_HIT,
                candle,
                ambiguous=target,
            )
            return [action]
        if target and activation_candle:
            return [LifecycleAction.ACTIVATION_TARGET_DEFERRED]
        if target:
            self._close_active_variants(
                state,
                state.targets[0].price,
                OutcomeResult.WIN,
                TradeEventType.TP1_HIT,
                candle,
                ambiguous=False,
            )
            return [LifecycleAction.TARGET_CLOSED]
        return []

    @staticmethod
    def _stop_fill(
        state: LifecycleSignal,
        candle: Candle,
        activation_candle: bool,
    ) -> Decimal:
        if activation_candle:
            return state.original_stop
        if state.side is Side.LONG:
            return min(state.original_stop, candle.open)
        return max(state.original_stop, candle.open)

    def _close_active_variants(
        self,
        state: LifecycleSignal,
        price: Decimal,
        result: OutcomeResult,
        event: TradeEventType,
        candle: Candle,
        *,
        ambiguous: bool,
    ) -> None:
        result_r = state.result_r(price)
        result_percent = result_r * state.recommended_risk_percent
        variants = sorted(
            state.active_variants,
            key=lambda variant: variant is OutcomeVariant.MANAGED,
        )
        for variant in variants:
            self.repository.close_track(
                signal_id=state.signal_id,
                variant=variant,
                result=result,
                result_r=result_r,
                result_percent=result_percent,
                close_reason=(
                    "Stop counted first because TP1 and SL were both inside one completed candle."
                    if ambiguous
                    else "Original TP1 reached."
                    if result is OutcomeResult.WIN
                    else "Original stop reached."
                ),
                close_event=event,
                price=price,
                occurred_at=candle.close_time,
                dedupe_key=self._dedupe(state.signal_id, candle, f"{variant.value}:{event.value}"),
                details={
                    "policy": "conservative-one-minute-v1",
                    "ambiguous_same_candle": ambiguous,
                    "candle_open": iso_utc(candle.open_time),
                },
            )

    @staticmethod
    def _dedupe(signal_id: str, candle: Candle, action: str) -> str:
        return f"lifecycle:{signal_id}:{iso_utc(candle.open_time)}:{action}"

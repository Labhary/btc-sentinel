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
from btc_sentinel.lifecycle.models import (
    LifecycleAction,
    LifecycleSignal,
    ReplayResult,
    TrackState,
)
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
            if state.active_tracks:
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
    def _touches_stop(state: LifecycleSignal, track: TrackState, candle: Candle) -> bool:
        return (
            candle.low <= track.current_stop
            if state.side is Side.LONG
            else candle.high >= track.current_stop
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
        target = self._touches_tp1(state, candle)
        activation_candle = activated_now or state.activated_at == candle.open_time
        if (
            target
            and activation_candle
            and not any(self._touches_stop(state, track, candle) for track in state.active_tracks)
        ):
            return [LifecycleAction.ACTIVATION_TARGET_DEFERRED]

        actions: list[LifecycleAction] = []
        tracks = sorted(
            state.active_tracks,
            key=lambda track: track.variant is OutcomeVariant.MANAGED,
        )
        for track in tracks:
            stop = self._touches_stop(state, track, candle)
            if stop:
                ambiguous = target
                self._close_track(
                    state,
                    track,
                    self._stop_fill(state, track, candle, activation_candle),
                    candle,
                    ambiguous=ambiguous,
                )
                action = (
                    LifecycleAction.AMBIGUOUS_STOP_FIRST
                    if ambiguous
                    else LifecycleAction.STOP_CLOSED
                )
                if action not in actions:
                    actions.append(action)
            elif target and not activation_candle:
                self._close_track(
                    state,
                    track,
                    state.targets[0].price,
                    candle,
                    ambiguous=False,
                    target=True,
                )
                if LifecycleAction.TARGET_CLOSED not in actions:
                    actions.append(LifecycleAction.TARGET_CLOSED)
        return actions

    @staticmethod
    def _stop_fill(
        state: LifecycleSignal,
        track: TrackState,
        candle: Candle,
        activation_candle: bool,
    ) -> Decimal:
        if activation_candle:
            return track.current_stop
        if state.side is Side.LONG:
            return min(track.current_stop, candle.open)
        return max(track.current_stop, candle.open)

    def _close_track(
        self,
        state: LifecycleSignal,
        track: TrackState,
        price: Decimal,
        candle: Candle,
        *,
        ambiguous: bool,
        target: bool = False,
    ) -> None:
        result_r = state.track_result_r(track, price)
        result_percent = result_r * state.recommended_risk_percent
        protected_stop = track.current_stop != state.original_stop
        if target:
            result = OutcomeResult.WIN
            event = TradeEventType.TP1_HIT
            reason = "Original TP1 reached."
        elif protected_stop and result_r == 0:
            result = OutcomeResult.BREAK_EVEN
            event = TradeEventType.BREAK_EVEN
            reason = "Cost-adjusted break-even stop reached."
        elif protected_stop and result_r > 0:
            result = OutcomeResult.EARLY_EXIT
            event = TradeEventType.EARLY_EXIT
            reason = "Protected managed stop preserved a positive partial result."
        else:
            result = OutcomeResult.LOSS
            event = TradeEventType.STOP_LOSS_HIT
            reason = (
                "Stop counted first because TP1 and SL were both inside one completed candle."
                if ambiguous
                else "Active track stop reached."
            )
        self.repository.close_track(
            signal_id=state.signal_id,
            variant=track.variant,
            result=result,
            result_r=result_r,
            result_percent=result_percent,
            close_reason=reason,
            close_event=event,
            price=price,
            occurred_at=candle.close_time,
            dedupe_key=self._dedupe(
                state.signal_id, candle, f"{track.variant.value}:{event.value}"
            ),
            details={
                "policy": "conservative-one-minute-v2",
                "ambiguous_same_candle": ambiguous,
                "candle_open": iso_utc(candle.open_time),
                "remaining_fraction": format(track.remaining_fraction, "f"),
                "realized_r_before_close": format(track.realized_r, "f"),
            },
        )

    @staticmethod
    def _dedupe(signal_id: str, candle: Candle, action: str) -> str:
        return f"lifecycle:{signal_id}:{iso_utc(candle.open_time)}:{action}"

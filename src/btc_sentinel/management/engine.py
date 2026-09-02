"""Deterministic paper-position management using completed candles only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from btc_sentinel.domain.enums import ManagementAction, OutcomeVariant, Side
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.lifecycle.models import LifecycleSignal, TrackState
from btc_sentinel.management.models import ManagementDecision, ManagementReplayResult
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle, CandleSeries
from btc_sentinel.persistence.repository import Repository
from btc_sentinel.time_utils import ensure_utc, iso_utc


@dataclass(frozen=True, slots=True)
class ManagementPolicy:
    """Versioned experiment; partial exits are deliberately off by default."""

    break_even_trigger_r: Decimal = Decimal("1.5")
    partial_trigger_r: Decimal | None = None
    partial_fraction: Decimal = Decimal("0.5")
    strategy_version: str = "management-v0.8.0"

    def __post_init__(self) -> None:
        if self.break_even_trigger_r < Decimal("1"):
            raise ValueError("Break-even protection cannot trigger below 1R")
        if self.partial_trigger_r is not None and self.partial_trigger_r <= 0:
            raise ValueError("Partial trigger must be positive when enabled")
        if (
            self.partial_trigger_r is not None
            and self.partial_trigger_r < self.break_even_trigger_r
        ):
            raise ValueError("Partial trigger cannot precede break-even protection")
        if not Decimal("0") < self.partial_fraction < Decimal("1"):
            raise ValueError("Partial fraction must be strictly between zero and one")
        if not self.strategy_version.strip():
            raise ValueError("Management strategy version is required")


class PositionManagementEngine:
    """Record one auditable decision per completed one-minute candle."""

    def __init__(self, repository: Repository, policy: ManagementPolicy | None = None) -> None:
        self.repository = repository
        self.policy = policy or ManagementPolicy()

    def replay(
        self,
        signal_id: str,
        series: CandleSeries,
        as_of: datetime,
    ) -> ManagementReplayResult:
        now = ensure_utc(as_of)
        self._validate_series(series, now)
        state = self.repository.get_lifecycle_signal(signal_id)
        if state.activated_at is None or self._managed_track(state) is None:
            raise DomainValidationError("Management replay requires an active managed track")

        key = f"management:{signal_id}"
        checkpoint = self.repository.get_checkpoint(key)
        expected_open = (
            checkpoint + timedelta(minutes=1) if checkpoint is not None else state.activated_at
        )
        candidates = tuple(candle for candle in series.candles if candle.open_time >= expected_open)
        if candidates and candidates[0].open_time != expected_open:
            raise DomainValidationError(
                f"Management replay gap: expected {iso_utc(expected_open)}, "
                f"received {iso_utc(candidates[0].open_time)}"
            )

        decisions: list[ManagementDecision] = []
        processed = 0
        for candle in candidates:
            dedupe_key = self._dedupe(signal_id, candle)
            if not self.repository.management_decision_exists(dedupe_key):
                state = self.repository.get_lifecycle_signal(signal_id)
                track = self._managed_track(state)
                if track is None:
                    break
                decision = self._decide(state, track, candle, dedupe_key)
                self.repository.apply_management_decision(decision)
                decisions.append(decision)
            self.repository.advance_checkpoint(
                key,
                candle.open_time,
                {
                    "signal_id": signal_id,
                    "source": "SPOT_1m",
                    "strategy_version": self.policy.strategy_version,
                },
            )
            checkpoint = candle.open_time
            processed += 1

        return ManagementReplayResult(signal_id, processed, checkpoint, tuple(decisions))

    def _decide(
        self,
        state: LifecycleSignal,
        track: TrackState,
        candle: Candle,
        dedupe_key: str,
    ) -> ManagementDecision:
        current_r = state.result_r(candle.close)
        total_r = track.realized_r + track.remaining_fraction * current_r
        action = ManagementAction.HOLD
        reason = "Original stop and target remain the evidence-free default."
        updated_stop: Decimal | None = None
        remaining_after: Decimal | None = None
        realized_after: Decimal | None = None
        changes = False

        if candle.open_time == state.activated_at:
            reason = "No management is permitted on the activation candle."
        elif (
            track.current_stop == state.original_stop
            and current_r >= self.policy.break_even_trigger_r
        ):
            action = ManagementAction.MOVE_STOP_TO_BREAK_EVEN
            updated_stop = self._cost_adjusted_break_even(state)
            reason = (
                f"Completed-candle profit reached {self.policy.break_even_trigger_r}R; "
                "the protected stop becomes effective only after this candle."
            )
            changes = True
        elif (
            self.policy.partial_trigger_r is not None
            and track.remaining_fraction == Decimal("1")
            and current_r >= self.policy.partial_trigger_r
        ):
            action = ManagementAction.TAKE_PARTIAL_PROFIT
            remaining_after = Decimal("1") - self.policy.partial_fraction
            realized_after = track.realized_r + self.policy.partial_fraction * current_r
            reason = (
                f"Explicit experimental partial threshold {self.policy.partial_trigger_r}R "
                "was reached on a completed candle."
            )
            changes = True

        return ManagementDecision(
            signal_id=state.signal_id,
            decided_at=candle.close_time,
            action=action,
            current_price=candle.close,
            unrealized_r=total_r,
            unrealized_percent=total_r * state.recommended_risk_percent,
            reason=reason,
            updated_stop=updated_stop,
            changes_managed_result=changes,
            strategy_version=self.policy.strategy_version,
            evidence={
                "candle_open": iso_utc(candle.open_time),
                "candle_close": iso_utc(candle.close_time),
                "closed_candle": True,
                "effective_from_next_candle": changes,
                "remaining_fraction_before": format(track.remaining_fraction, "f"),
                "current_stop_before": format(track.current_stop, "f"),
                "partial_enabled": self.policy.partial_trigger_r is not None,
                "remaining_fraction_after": (
                    None if remaining_after is None else format(remaining_after, "f")
                ),
                "realized_r_after": (
                    None if realized_after is None else format(realized_after, "f")
                ),
            },
            dedupe_key=dedupe_key,
            remaining_fraction_after=remaining_after,
            realized_r_after=realized_after,
        )

    @staticmethod
    def _managed_track(state: LifecycleSignal) -> TrackState | None:
        return next(
            (track for track in state.active_tracks if track.variant is OutcomeVariant.MANAGED),
            None,
        )

    @staticmethod
    def _cost_adjusted_break_even(state: LifecycleSignal) -> Decimal:
        if state.fill_price is None:
            raise DomainValidationError("Break-even requires an activated trade")
        cost = state.fill_price * state.estimated_cost_rate
        return state.fill_price + cost if state.side is Side.LONG else state.fill_price - cost

    @staticmethod
    def _validate_series(series: CandleSeries, now: datetime) -> None:
        if series.venue is not MarketVenue.SPOT or series.interval is not MarketInterval.ONE_MINUTE:
            raise DomainValidationError(
                "Management replay requires Spot BTCUSDT one-minute candles"
            )
        if any(not candle.is_closed_at(now) for candle in series.candles):
            raise DomainValidationError("Management replay received an incomplete candle")

    def _dedupe(self, signal_id: str, candle: Candle) -> str:
        return f"management:{signal_id}:{iso_utc(candle.open_time)}:{self.policy.strategy_version}"

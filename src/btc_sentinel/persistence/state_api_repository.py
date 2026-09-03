"""Repository implementation backed by finite signed Worker commands."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from btc_sentinel.domain.enums import (
    MarketRegime,
    OutcomeResult,
    OutcomeVariant,
    Side,
    SignalStatus,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.domain.models import Signal, Target
from btc_sentinel.errors import RecordNotFoundError
from btc_sentinel.lifecycle.models import LifecycleSignal, TrackState
from btc_sentinel.management.models import ManagementDecision
from btc_sentinel.reports.models import ReportSignal
from btc_sentinel.runtime.state_api import StateApiClient, StateApiError
from btc_sentinel.statistics import calculate_statistics
from btc_sentinel.statistics.models import OutcomeSample
from btc_sentinel.time_utils import iso_utc


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise StateApiError("State repository returned an invalid timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateApiError("State repository returned an invalid timestamp") from exc


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise StateApiError("State repository returned an invalid record")
    return value


class StateApiRepository:
    """Map the domain repository port to a fixed Worker operation vocabulary."""

    def __init__(self, client: StateApiClient) -> None:
        self.client = client

    def _command(self, operation: str, **arguments: object) -> object:
        return self.client.repository_command(operation, arguments)

    def allocate_signal_id(self, business_date: date) -> str:
        result = self._command("allocate_signal_id", business_date=business_date.isoformat())
        if not isinstance(result, str):
            raise StateApiError("State repository returned an invalid signal ID")
        return result

    def create_signal(self, signal: Signal) -> None:
        terms = signal.terms
        targets_text = ", ".join(format(target.price, "f") for target in terms.targets)
        notification_text = "\n".join(
            (
                f"BTC Sentinel paper signal {signal.signal_id}",
                f"{terms.side.value} entry {terms.entry_low}-{terms.entry_high}",
                f"Stop {terms.original_stop} | Targets {targets_text}",
                f"Setup score {signal.setup_score}/100 (not a win probability)",
                "Paper analysis only — no order was placed.",
            )
        )
        self._command(
            "create_signal",
            signal={
                "signal_id": signal.signal_id,
                "symbol": terms.symbol,
                "side": terms.side.value,
                "status": signal.status.value,
                "setup_score": signal.setup_score,
                "regime": signal.regime.value,
                "biases": {
                    "monthly": signal.biases.monthly.value,
                    "weekly": signal.biases.weekly.value,
                    "daily": signal.biases.daily.value,
                    "four_hour": signal.biases.four_hour.value,
                    "one_hour": signal.biases.one_hour.value,
                    "fifteen_minute": signal.biases.fifteen_minute.value,
                },
                "created_at": iso_utc(terms.created_at),
                "data_timestamp": iso_utc(terms.data_timestamp),
                "expires_at": iso_utc(terms.expires_at),
                "entry_low": _decimal(terms.entry_low),
                "entry_high": _decimal(terms.entry_high),
                "original_stop": _decimal(terms.original_stop),
                "estimated_cost_rate": _decimal(terms.estimated_round_trip_cost_rate),
                "minimum_planned_rr": _decimal(terms.minimum_planned_rr),
                "invalidation_condition": terms.invalidation_condition,
                "expiration_condition": terms.expiration_condition,
                "recommended_risk_percent": _decimal(terms.recommended_risk_percent),
                "reasons": list(signal.reasons),
                "risks": list(signal.risks),
                "strategy_version": signal.strategy_version,
                "row_version": signal.row_version,
                "targets": [
                    {
                        "ordinal": target.ordinal,
                        "price": _decimal(target.price),
                        "planned_r": _decimal(terms.planned_r_for(target)),
                    }
                    for target in terms.targets
                ],
            },
            notification={
                "message_type": "SIGNAL",
                "text": notification_text,
                "dedupe_key": f"notify:signal:{signal.signal_id}:created",
                "signal_id": signal.signal_id,
                "created_at": iso_utc(terms.created_at),
            },
        )

    def get_signal_status(self, signal_id: str) -> SignalStatus:
        result = self._command("get_signal_status", signal_id=signal_id)
        if result is None:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        return SignalStatus(result)

    def _get_signal_strategy(self, signal_id: str) -> str:
        result = self._command("get_signal_strategy", signal_id=signal_id)
        if not isinstance(result, str) or not result:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        return result

    def get_lifecycle_signal(self, signal_id: str) -> LifecycleSignal:
        raw = self._command("get_lifecycle_signal", signal_id=signal_id)
        if raw is None:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        item = _mapping(raw)
        return LifecycleSignal(
            signal_id=str(item["signal_id"]),
            status=SignalStatus(item["status"]),
            side=Side(item["side"]),
            created_at=_time(item["created_at"]),
            expires_at=_time(item["expires_at"]),
            entry_low=Decimal(item["entry_low"]),
            entry_high=Decimal(item["entry_high"]),
            original_stop=Decimal(item["original_stop"]),
            targets=tuple(
                Target(int(target["ordinal"]), Decimal(target["price"]))
                for target in item["targets"]
            ),
            estimated_cost_rate=Decimal(item["estimated_cost_rate"]),
            recommended_risk_percent=Decimal(item["recommended_risk_percent"]),
            fill_price=None if item["fill_price"] is None else Decimal(item["fill_price"]),
            activated_at=(None if item["activated_at"] is None else _time(item["activated_at"])),
            active_tracks=tuple(
                TrackState(
                    OutcomeVariant(track["variant"]),
                    Decimal(track["current_stop"]),
                    Decimal(track["remaining_fraction"]),
                    Decimal(track["realized_r"]),
                )
                for track in item["active_tracks"]
            ),
        )

    def activate_signal(
        self, signal_id: str, fill_price: Decimal, occurred_at: datetime, dedupe_key: str
    ) -> None:
        self._command(
            "activate_signal",
            signal_id=signal_id,
            fill_price=_decimal(fill_price),
            occurred_at=iso_utc(occurred_at),
            dedupe_key=dedupe_key,
        )

    def expire_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None:
        self._transition(signal_id, SignalStatus.EXPIRED, occurred_at, dedupe_key)

    def cancel_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None:
        self._transition(signal_id, SignalStatus.CANCELLED, occurred_at, dedupe_key)

    def _transition(
        self, signal_id: str, status: SignalStatus, occurred_at: datetime, dedupe_key: str
    ) -> None:
        self._command(
            "transition_pending",
            signal_id=signal_id,
            status=status.value,
            occurred_at=iso_utc(occurred_at),
            dedupe_key=dedupe_key,
        )

    def close_track(
        self,
        signal_id: str,
        variant: OutcomeVariant,
        result: OutcomeResult,
        result_r: Decimal,
        result_percent: Decimal,
        close_reason: str,
        close_event: TradeEventType,
        price: Decimal,
        occurred_at: datetime,
        dedupe_key: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        samples = self.list_outcome_samples()
        statistics_payload = calculate_statistics(
            (
                *samples,
                OutcomeSample(
                    signal_id=signal_id,
                    variant=variant,
                    result=result,
                    result_r=result_r,
                    closed_at=occurred_at,
                    strategy_version=self._get_signal_strategy(signal_id),
                ),
            ),
            occurred_at,
        ).as_payload()
        self._command(
            "close_track",
            signal_id=signal_id,
            variant=variant.value,
            result=result.value,
            result_r=_decimal(result_r),
            result_percent=_decimal(result_percent),
            close_reason=close_reason,
            close_event=close_event.value,
            price=_decimal(price),
            occurred_at=iso_utc(occurred_at),
            dedupe_key=dedupe_key,
            details=details or {},
            statistics_payload=statistics_payload,
        )

    def get_track_status(self, signal_id: str, variant: OutcomeVariant) -> TrackStatus:
        result = self._command("get_track_status", signal_id=signal_id, variant=variant.value)
        if result is None:
            raise RecordNotFoundError(f"Track {signal_id}/{variant.value} was not found")
        return TrackStatus(result)

    def get_checkpoint(self, checkpoint_key: str) -> datetime | None:
        result = self._command("get_checkpoint", checkpoint_key=checkpoint_key)
        return None if result is None else _time(result)

    def advance_checkpoint(
        self, checkpoint_key: str, processed_at: datetime, payload: dict[str, Any]
    ) -> None:
        self._command(
            "advance_checkpoint",
            checkpoint_key=checkpoint_key,
            processed_at=iso_utc(processed_at),
            payload=payload,
        )

    def apply_management_decision(self, decision: ManagementDecision) -> None:
        self._command(
            "apply_management_decision",
            decision={
                "signal_id": decision.signal_id,
                "decided_at": iso_utc(decision.decided_at),
                "action": decision.action.value,
                "current_price": _decimal(decision.current_price),
                "unrealized_percent": _decimal(decision.unrealized_percent),
                "unrealized_r": _decimal(decision.unrealized_r),
                "reason": decision.reason,
                "updated_stop": (
                    None if decision.updated_stop is None else _decimal(decision.updated_stop)
                ),
                "changes_managed_result": decision.changes_managed_result,
                "strategy_version": decision.strategy_version,
                "evidence": dict(decision.evidence),
                "dedupe_key": decision.dedupe_key,
                "remaining_fraction_after": (
                    None
                    if decision.remaining_fraction_after is None
                    else _decimal(decision.remaining_fraction_after)
                ),
                "realized_r_after": (
                    None
                    if decision.realized_r_after is None
                    else _decimal(decision.realized_r_after)
                ),
            },
        )

    def management_decision_exists(self, dedupe_key: str) -> bool:
        result = self._command("management_decision_exists", dedupe_key=dedupe_key)
        if not isinstance(result, bool):
            raise StateApiError("State repository returned an invalid existence result")
        return result

    def get_latest_statistics_snapshot(self) -> dict[str, Any] | None:
        result = self._command("get_latest_statistics_snapshot")
        return None if result is None else _mapping(result)

    def list_outcome_samples(
        self, start_at: datetime | None = None, end_at: datetime | None = None
    ) -> tuple[OutcomeSample, ...]:
        rows: list[object] = []
        cursor_closed_at: str | None = None
        cursor_id: str | None = None
        for _ in range(100):
            result = self._command(
                "list_outcome_samples",
                start_at=None if start_at is None else iso_utc(start_at),
                end_at=None if end_at is None else iso_utc(end_at),
                cursor_closed_at=cursor_closed_at,
                cursor_id=cursor_id,
            )
            page = _mapping(result)
            items = page.get("items")
            next_cursor = page.get("next_cursor")
            if not isinstance(items, list):
                raise StateApiError("State repository returned invalid outcomes")
            rows.extend(items)
            if next_cursor is None:
                break
            cursor = _mapping(next_cursor)
            if not isinstance(cursor.get("closed_at"), str) or not isinstance(
                cursor.get("outcome_id"), str
            ):
                raise StateApiError("State repository returned an invalid outcome cursor")
            cursor_closed_at = cursor["closed_at"]
            cursor_id = cursor["outcome_id"]
        else:
            raise StateApiError("State repository outcome pagination limit exceeded")
        return tuple(
            OutcomeSample(
                signal_id=item["signal_id"],
                variant=OutcomeVariant(item["variant"]),
                result=OutcomeResult(item["result"]),
                result_r=Decimal(item["result_r"]),
                closed_at=_time(item["closed_at"]),
                strategy_version=item["strategy_version"],
            )
            for raw in rows
            for item in (_mapping(raw),)
        )

    def list_report_signals(self, status: SignalStatus) -> tuple[ReportSignal, ...]:
        result = self._command("list_report_signals", status=status.value)
        if not isinstance(result, list):
            raise StateApiError("State repository returned invalid report signals")
        values: list[ReportSignal] = []
        for raw in result:
            item = _mapping(raw)
            values.append(
                ReportSignal(
                    signal_id=item["signal_id"],
                    status=SignalStatus(item["status"]),
                    side=Side(item["side"]),
                    regime=MarketRegime(item["regime"]),
                    setup_score=int(item["setup_score"]),
                    created_at=_time(item["created_at"]),
                    expires_at=_time(item["expires_at"]),
                    entry_low=Decimal(item["entry_low"]),
                    entry_high=Decimal(item["entry_high"]),
                    original_stop=Decimal(item["original_stop"]),
                    targets=tuple(
                        Target(int(target["ordinal"]), Decimal(target["price"]))
                        for target in item["targets"]
                    ),
                    strategy_version=item["strategy_version"],
                    fill_price=(
                        None if item["fill_price"] is None else Decimal(item["fill_price"])
                    ),
                    activated_at=(
                        None if item["activated_at"] is None else _time(item["activated_at"])
                    ),
                    managed_stop=(
                        None if item["managed_stop"] is None else Decimal(item["managed_stop"])
                    ),
                    fixed_track_active=bool(item["fixed_track_active"]),
                    managed_track_active=bool(item["managed_track_active"]),
                )
            )
        return tuple(values)

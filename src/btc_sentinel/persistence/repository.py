"""Persistence port shared by local SQLite and the future signed D1 API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from btc_sentinel.domain.enums import (
    OutcomeResult,
    OutcomeVariant,
    SignalStatus,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.domain.models import Signal

if TYPE_CHECKING:
    from btc_sentinel.lifecycle.models import LifecycleSignal
    from btc_sentinel.management.models import ManagementDecision


class Repository(Protocol):
    def allocate_signal_id(self, business_date: date) -> str: ...

    def create_signal(self, signal: Signal) -> None: ...

    def get_signal_status(self, signal_id: str) -> SignalStatus: ...

    def get_lifecycle_signal(self, signal_id: str) -> LifecycleSignal: ...

    def activate_signal(
        self,
        signal_id: str,
        fill_price: Decimal,
        occurred_at: datetime,
        dedupe_key: str,
    ) -> None: ...

    def expire_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None: ...

    def cancel_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None: ...

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
    ) -> None: ...

    def get_track_status(self, signal_id: str, variant: OutcomeVariant) -> TrackStatus: ...

    def get_checkpoint(self, checkpoint_key: str) -> datetime | None: ...

    def advance_checkpoint(
        self, checkpoint_key: str, processed_at: datetime, payload: dict[str, Any]
    ) -> None: ...

    def apply_management_decision(self, decision: ManagementDecision) -> None: ...

    def management_decision_exists(self, dedupe_key: str) -> bool: ...

    def get_latest_statistics_snapshot(self) -> dict[str, Any] | None: ...

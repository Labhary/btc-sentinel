"""Immutable Phase 8 management observations and decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from btc_sentinel.domain.enums import ManagementAction
from btc_sentinel.domain.models import as_decimal, as_utc
from btc_sentinel.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class ManagementDecision:
    signal_id: str
    decided_at: datetime
    action: ManagementAction
    current_price: Decimal
    unrealized_r: Decimal
    unrealized_percent: Decimal
    reason: str
    updated_stop: Decimal | None
    changes_managed_result: bool
    strategy_version: str
    evidence: dict[str, Any]
    dedupe_key: str
    remaining_fraction_after: Decimal | None = None
    realized_r_after: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decided_at", as_utc(self.decided_at, "decided_at"))
        for name in ("current_price", "unrealized_r", "unrealized_percent"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name))
        for name in ("updated_stop", "remaining_fraction_after", "realized_r_after"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_decimal(value, name))
        object.__setattr__(self, "evidence", dict(self.evidence))
        if not self.signal_id or not self.reason.strip() or not self.strategy_version.strip():
            raise DomainValidationError("Management decision identity and explanation are required")
        if self.current_price <= 0 or not self.dedupe_key.strip():
            raise DomainValidationError("Management decision price and dedupe key are invalid")
        if self.updated_stop is not None and self.updated_stop <= 0:
            raise DomainValidationError("Updated stop must be positive")
        fraction = self.remaining_fraction_after
        if fraction is not None and not Decimal("0") < fraction <= Decimal("1"):
            raise DomainValidationError("Remaining managed fraction must be between zero and one")
        if (fraction is None) is not (self.realized_r_after is None):
            raise DomainValidationError("Partial accounting fields must be supplied together")
        if self.action is ManagementAction.HOLD and (
            self.changes_managed_result or self.updated_stop is not None or fraction is not None
        ):
            raise DomainValidationError("HOLD cannot change managed-track state")
        if self.action is ManagementAction.MOVE_STOP_TO_BREAK_EVEN and (
            not self.changes_managed_result or self.updated_stop is None
        ):
            raise DomainValidationError("Break-even action requires a changed stop")
        if self.action is ManagementAction.TAKE_PARTIAL_PROFIT and (
            not self.changes_managed_result or fraction is None
        ):
            raise DomainValidationError("Partial action requires durable fraction accounting")


@dataclass(frozen=True, slots=True)
class ManagementReplayResult:
    signal_id: str
    processed_candles: int
    checkpoint: datetime | None
    decisions: tuple[ManagementDecision, ...]

    def __post_init__(self) -> None:
        if self.checkpoint is not None:
            object.__setattr__(self, "checkpoint", as_utc(self.checkpoint, "checkpoint"))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        if not self.signal_id or self.processed_candles < 0:
            raise DomainValidationError("Management replay result is invalid")

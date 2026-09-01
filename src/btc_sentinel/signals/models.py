"""Immutable inputs and outputs for conservative signal admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from btc_sentinel.analysis.models import AnalysisResult
from btc_sentinel.domain.models import Signal, as_utc
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.news.models import RiskAssessment


class SignalDecision(StrEnum):
    CREATED = "CREATED"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass(frozen=True, slots=True)
class SignalHistory:
    last_signal_at: datetime | None = None
    active_managed_signal: bool = False

    def __post_init__(self) -> None:
        if self.last_signal_at is not None:
            object.__setattr__(
                self,
                "last_signal_at",
                as_utc(self.last_signal_at, "last_signal_at"),
            )


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    evaluated_at: datetime
    decision: SignalDecision
    analysis: AnalysisResult
    news_risk: RiskAssessment
    signal: Signal | None
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluated_at", as_utc(self.evaluated_at, "evaluated_at"))
        object.__setattr__(self, "rejection_reasons", tuple(self.rejection_reasons))
        if self.decision is SignalDecision.CREATED:
            if self.signal is None or self.rejection_reasons:
                raise DomainValidationError(
                    "A created signal requires terms and cannot contain rejection reasons"
                )
        elif self.signal is not None or not self.rejection_reasons:
            raise DomainValidationError("NO_SIGNAL requires at least one auditable rejection")

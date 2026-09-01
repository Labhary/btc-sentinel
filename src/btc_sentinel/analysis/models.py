"""Immutable public records produced by the Phase 4 analysis engine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from btc_sentinel.market_data.enums import MarketInterval


class Direction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class MarketRegime(StrEnum):
    BULLISH_TREND = "BULLISH_TREND"
    BEARISH_TREND = "BEARISH_TREND"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    ABNORMALLY_VOLATILE = "ABNORMALLY_VOLATILE"
    NO_RELIABLE_REGIME = "NO_RELIABLE_REGIME"


class AnalysisStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    ema_20: Decimal
    ema_50: Decimal
    ema_100: Decimal | None
    ema_200: Decimal | None
    rsi_14: Decimal
    macd: Decimal
    macd_signal: Decimal
    macd_histogram: Decimal
    adx_14: Decimal
    atr_14: Decimal
    normalized_atr: Decimal
    bollinger_upper: Decimal
    bollinger_middle: Decimal
    bollinger_lower: Decimal
    rolling_vwap: Decimal
    volume_ratio: Decimal
    abnormal_volatility: bool


@dataclass(frozen=True, slots=True)
class PriceZone:
    lower: Decimal
    upper: Decimal
    touches: int

    def __post_init__(self) -> None:
        if self.lower <= 0 or self.upper < self.lower or self.touches < 1:
            raise ValueError("Invalid deterministic price zone")


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    direction: Direction
    higher_highs: bool
    higher_lows: bool
    lower_highs: bool
    lower_lows: bool
    break_of_structure: Direction
    change_of_character: Direction
    support_zones: tuple[PriceZone, ...]
    resistance_zones: tuple[PriceZone, ...]


@dataclass(frozen=True, slots=True)
class TimeframeAnalysis:
    interval: MarketInterval
    direction: Direction
    regime: MarketRegime
    indicators: IndicatorSnapshot
    structure: StructureSnapshot
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    name: str
    direction: Direction
    agreement: Decimal
    weight: int
    available: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not Decimal("0") <= self.agreement <= Decimal("1"):
            raise ValueError("Evidence agreement must be between zero and one")
        if not 0 <= self.weight <= 100:
            raise ValueError("Evidence weight must be between zero and 100")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    status: AnalysisStatus
    directional_bias: Direction
    regime: MarketRegime
    setup_quality_score: int
    score_is_probability: bool
    timeframes: tuple[TimeframeAnalysis, ...]
    evidence_groups: tuple[EvidenceGroup, ...]
    no_trade_reasons: tuple[str, ...]
    issues: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.setup_quality_score <= 100:
            raise ValueError("Setup-quality score must be between zero and 100")
        if self.score_is_probability:
            raise ValueError("An analysis score must never be presented as a probability")
        if self.status is AnalysisStatus.REJECTED and not self.issues:
            raise ValueError("Rejected analysis requires an issue")

    @property
    def reliable(self) -> bool:
        return self.status is not AnalysisStatus.REJECTED and not self.no_trade_reasons

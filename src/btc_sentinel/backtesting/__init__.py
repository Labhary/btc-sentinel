"""Conservative Phase 11 simulation and walk-forward evaluation."""

from btc_sentinel.backtesting.dataset import (
    ArchiveSpec,
    HistoricalDataError,
    HistoricalDataset,
    HistoricalDatasetLoader,
    HistoricalDatasetManifest,
    HistoricalDatasetSummary,
    TimestampUnit,
    parse_manifest,
)
from btc_sentinel.backtesting.engine import BacktestEngine
from btc_sentinel.backtesting.historical_runner import (
    FailClosedHistoricalRiskProvider,
    HistoricalReplayRun,
    HistoricalReplayRunner,
    HistoricalRiskProvider,
)
from btc_sentinel.backtesting.models import (
    BacktestComparisonReport,
    BacktestOutcome,
    BacktestReport,
    BacktestRunSpec,
    BacktestTrade,
    BacktestVerdict,
    CostStressResult,
    HistoricalSignalCase,
    SensitivityResult,
    WalkForwardFold,
    WalkForwardPolicy,
)
from btc_sentinel.backtesting.replay import (
    HistoricalImportSummary,
    HistoricalMarketView,
    HistoricalReplayStore,
)
from btc_sentinel.backtesting.simulator import simulate_fixed_case, simulate_managed_case

__all__ = [
    "ArchiveSpec",
    "BacktestComparisonReport",
    "BacktestEngine",
    "BacktestOutcome",
    "BacktestReport",
    "BacktestRunSpec",
    "BacktestTrade",
    "BacktestVerdict",
    "CostStressResult",
    "FailClosedHistoricalRiskProvider",
    "HistoricalDataError",
    "HistoricalDataset",
    "HistoricalDatasetLoader",
    "HistoricalDatasetManifest",
    "HistoricalDatasetSummary",
    "HistoricalImportSummary",
    "HistoricalMarketView",
    "HistoricalReplayRun",
    "HistoricalReplayRunner",
    "HistoricalReplayStore",
    "HistoricalRiskProvider",
    "HistoricalSignalCase",
    "SensitivityResult",
    "TimestampUnit",
    "WalkForwardFold",
    "WalkForwardPolicy",
    "parse_manifest",
    "simulate_fixed_case",
    "simulate_managed_case",
]

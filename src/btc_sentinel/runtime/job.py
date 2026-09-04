"""Fail-closed executable assembly for the disabled paper-engine workflow."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from btc_sentinel.config import PublicSettings, SecretValue
from btc_sentinel.errors import ConfigurationError
from btc_sentinel.market_data import BinancePublicClient, MarketDataCollector
from btc_sentinel.market_data.transport import RetryingJsonTransport
from btc_sentinel.news.collector import NewsCollector
from btc_sentinel.news.transport import PublicNewsTransport
from btc_sentinel.persistence.state_api_repository import StateApiRepository
from btc_sentinel.runtime.orchestrator import PaperEngineOrchestrator, RunStatus, RunSummary
from btc_sentinel.runtime.state_api import StateApiClient
from btc_sentinel.runtime.state_bridge import StateApiRuntimeBridge
from btc_sentinel.time_utils import ensure_utc, iso_utc, utc_now

_IDENTITY = re.compile(r"^[A-Za-z0-9_.:+-]{1,100}$")
_PLACEHOLDER_MARKERS = ("SET_IN_", "REPLACE", "CHANGEME", "NOT_HERE", "SET_AFTER_")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value or any(marker in value.upper() for marker in _PLACEHOLDER_MARKERS):
        raise ConfigurationError(f"{name} is required in the secret or variable store")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeJobConfig:
    """The minimum configuration needed by the Python paper runtime."""

    dispatch_key: str
    state_api_base_url: str
    state_api_hmac_secret: SecretValue
    public: PublicSettings

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> RuntimeJobConfig:
        env = os.environ if source is None else source
        if env.get("PAPER_ENGINE_ENABLED", "false") != "true":
            raise ConfigurationError("PAPER_ENGINE_ENABLED must be exactly true")

        dispatch_key = _required(env, "DISPATCH_KEY")
        if not _IDENTITY.fullmatch(dispatch_key):
            raise ConfigurationError("DISPATCH_KEY has an invalid format")

        base_url = _required(env, "STATE_API_BASE_URL")
        secret = _required(env, "STATE_API_HMAC_SECRET")
        if len(secret) < 32:
            raise ConfigurationError("STATE_API_HMAC_SECRET must contain at least 32 characters")

        public = PublicSettings.from_env(env)
        if public.app_env != "production":
            raise ConfigurationError("The paper runtime requires APP_ENV=production")
        if (
            public.minimum_planned_rr != Decimal("2")
            or public.default_risk_percent != Decimal("0.50")
            or public.maximum_risk_percent != Decimal("1.00")
        ):
            raise ConfigurationError("Runtime strategy settings must match the frozen baseline")

        return cls(dispatch_key, base_url, SecretValue(secret), public)


RuntimeBuilder = Callable[[RuntimeJobConfig, Callable[[], datetime]], PaperEngineOrchestrator]


def build_orchestrator(
    config: RuntimeJobConfig,
    clock: Callable[[], datetime] = utc_now,
) -> PaperEngineOrchestrator:
    """Construct every production adapter without performing network I/O."""

    state_client = StateApiClient(
        config.state_api_base_url,
        config.state_api_hmac_secret,
        clock=clock,
    )
    state_bridge = StateApiRuntimeBridge(state_client, clock=clock)
    market_client = BinancePublicClient(RetryingJsonTransport())
    return PaperEngineOrchestrator(
        repository=StateApiRepository(state_client),
        market_collector=MarketDataCollector(market_client, local_clock=clock),
        news_collector=NewsCollector(PublicNewsTransport()),
        state_provider=state_bridge,
        notification_sink=state_bridge,
        health_sink=state_bridge,
    )


def execute(
    config: RuntimeJobConfig,
    *,
    clock: Callable[[], datetime] = utc_now,
    builder: RuntimeBuilder = build_orchestrator,
) -> int:
    now = ensure_utc(clock())
    summary = builder(config, clock).run(config.dispatch_key, now)
    print(_summary_json(summary))
    return 1 if summary.status is RunStatus.FAILED else 0


def _summary_json(summary: RunSummary) -> str:
    return json.dumps(
        {
            "event": "paper_engine_completed",
            "run_id": summary.run_id,
            "finished_at": iso_utc(summary.finished_at),
            "status": summary.status.value,
            "data_fresh": summary.data_fresh,
            "monitored_signals": summary.monitored_signals,
            "processed_candles": summary.processed_candles,
            "signal_created": summary.signal_created,
            "news_decision": summary.news_decision,
            "issues": list(summary.issues),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    try:
        return execute(RuntimeJobConfig.from_env())
    except Exception as exc:
        print(
            json.dumps(
                {"event": "paper_engine_refused", "error_name": type(exc).__name__},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

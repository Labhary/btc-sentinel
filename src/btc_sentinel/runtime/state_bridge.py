"""Adapters from the orchestrator ports to the authenticated Worker boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from btc_sentinel.runtime.orchestrator import (
    RunSummary,
    RuntimeNotification,
    RuntimeState,
)
from btc_sentinel.runtime.state_api import HealthRun, StateApiClient
from btc_sentinel.signals import SignalHistory
from btc_sentinel.time_utils import ensure_utc, utc_now


class StateApiRuntimeBridge:
    """Load runtime controls and write outbox/health through fixed API paths."""

    def __init__(
        self,
        client: StateApiClient,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.client = client
        self.clock = clock

    def load(self) -> RuntimeState:
        bootstrap = self.client.bootstrap()
        last_signal_at = (
            None
            if bootstrap.last_signal_at is None
            else ensure_utc(datetime.fromisoformat(bootstrap.last_signal_at.replace("Z", "+00:00")))
        )
        return RuntimeState(
            signal_generation_paused=bootstrap.signal_generation_paused,
            monitored_signal_ids=bootstrap.monitored_signal_ids,
            signal_history=SignalHistory(
                last_signal_at=last_signal_at,
                active_managed_signal=bootstrap.active_managed_signal,
            ),
        )

    def enqueue(self, notification: RuntimeNotification) -> None:
        self.client.enqueue_notification(
            message_type=notification.message_type,
            text=notification.text,
            dedupe_key=notification.dedupe_key,
            signal_id=notification.signal_id,
            created_at=ensure_utc(self.clock()),
        )

    def record(self, summary: RunSummary) -> None:
        self.client.record_health(
            HealthRun(
                run_id=summary.run_id,
                job_name="paper-engine",
                started_at=summary.started_at,
                finished_at=summary.finished_at,
                status=summary.status.value,
                data_fresh=summary.data_fresh,
                summary={
                    "monitored_signals": summary.monitored_signals,
                    "processed_candles": summary.processed_candles,
                    "signal_created": summary.signal_created,
                    "news_decision": summary.news_decision,
                    "issues": list(summary.issues),
                },
                dedupe_key=f"health:{summary.run_id}",
            )
        )

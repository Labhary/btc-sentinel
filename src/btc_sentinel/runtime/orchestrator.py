"""Fail-closed orchestration of one deterministic paper-engine run.

This module coordinates already-tested domain engines.  It deliberately owns no
network credentials and performs no delivery itself; durable state and outbox
writes are supplied through narrow ports.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from btc_sentinel.domain.enums import OutcomeVariant, SignalStatus
from btc_sentinel.domain.ids import format_signal_id
from btc_sentinel.domain.models import Signal
from btc_sentinel.lifecycle import LifecycleReplayEngine
from btc_sentinel.lifecycle.models import LifecycleAction
from btc_sentinel.management import PositionManagementEngine
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import CandleSeries, CollectionResult, CollectionStatus
from btc_sentinel.news import NewsRiskEngine
from btc_sentinel.news.models import NewsCollection, RiskAssessment
from btc_sentinel.persistence.repository import Repository
from btc_sentinel.reports import ReportEngine, ReportKind
from btc_sentinel.signals import SignalDecision, SignalEngine, SignalHistory
from btc_sentinel.time_utils import ensure_utc, iso_utc


class RunStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RuntimeState:
    signal_generation_paused: bool
    monitored_signal_ids: tuple[str, ...]
    signal_history: SignalHistory

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "monitored_signal_ids", tuple(dict.fromkeys(self.monitored_signal_ids))
        )


@dataclass(frozen=True, slots=True)
class RuntimeNotification:
    message_type: str
    text: str
    dedupe_key: str
    signal_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    started_at: datetime
    finished_at: datetime
    status: RunStatus
    data_fresh: bool
    monitored_signals: int
    processed_candles: int
    signal_created: str | None
    news_decision: str | None
    issues: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        object.__setattr__(self, "issues", tuple(self.issues))


class MarketCollector(Protocol):
    def collect(self) -> CollectionResult: ...


class PublicNewsCollector(Protocol):
    def collect(self, as_of: datetime) -> NewsCollection: ...


class StateProvider(Protocol):
    def load(self) -> RuntimeState: ...


class NotificationSink(Protocol):
    def enqueue(self, notification: RuntimeNotification) -> None: ...


class HealthSink(Protocol):
    def record(self, summary: RunSummary) -> None: ...


class PaperEngineOrchestrator:
    """Run market, risk, lifecycle, management, signals, reports, and health."""

    def __init__(
        self,
        *,
        repository: Repository,
        market_collector: MarketCollector,
        news_collector: PublicNewsCollector,
        state_provider: StateProvider,
        notification_sink: NotificationSink,
        health_sink: HealthSink,
        signal_engine: SignalEngine | None = None,
        news_engine: NewsRiskEngine | None = None,
    ) -> None:
        self.repository = repository
        self.market_collector = market_collector
        self.news_collector = news_collector
        self.state_provider = state_provider
        self.notification_sink = notification_sink
        self.health_sink = health_sink
        self.signal_engine = signal_engine or SignalEngine()
        self.news_engine = news_engine or NewsRiskEngine()

    def run(self, run_id: str, as_of: datetime) -> RunSummary:
        now = ensure_utc(as_of)
        try:
            summary = self._run(run_id, now)
        except Exception as exc:
            summary = RunSummary(
                run_id=run_id,
                started_at=now,
                finished_at=now,
                status=RunStatus.FAILED,
                data_fresh=False,
                monitored_signals=0,
                processed_candles=0,
                signal_created=None,
                news_decision=None,
                issues=(f"runtime:{type(exc).__name__}",),
            )
        self.health_sink.record(summary)
        return summary

    def _run(self, run_id: str, now: datetime) -> RunSummary:
        state = self.state_provider.load()
        collection = self.market_collector.collect()
        if collection.status is CollectionStatus.REJECTED or collection.snapshot is None:
            return self._finish(
                run_id,
                now,
                RunStatus.FAILED,
                False,
                state,
                0,
                None,
                None,
                tuple(issue.code for issue in collection.issues),
            )

        snapshot = collection.snapshot
        news_collection = self.news_collector.collect(now)
        risk = self.news_engine.evaluate(news_collection, now)
        issues = [issue.code for issue in collection.issues]
        issues.extend(f"news:{issue.source_id}" for issue in news_collection.issues)

        processed = 0
        active_managed = state.signal_history.active_managed_signal
        try:
            one_minute = snapshot.series_for(MarketVenue.SPOT, MarketInterval.ONE_MINUTE)
            for signal_id in state.monitored_signal_ids:
                count, is_active = self._monitor_signal(signal_id, one_minute, now)
                processed += count
                active_managed = active_managed or is_active
        except Exception as exc:
            # Durable health receives a bounded class name, never a response body or secret.
            issues.append(f"monitor:{type(exc).__name__}")
            return self._finish(
                run_id,
                now,
                RunStatus.FAILED,
                True,
                state,
                processed,
                None,
                risk,
                tuple(issues),
            )

        created: Signal | None = None
        if not state.signal_generation_paused:
            history = SignalHistory(
                last_signal_at=state.signal_history.last_signal_at,
                active_managed_signal=active_managed,
            )
            provisional = format_signal_id(now.date(), 1)
            evaluation = self.signal_engine.evaluate(provisional, snapshot, risk, now, history)
            if evaluation.decision is SignalDecision.CREATED and evaluation.signal is not None:
                signal_id = self.repository.allocate_signal_id(now.date())
                created = replace(evaluation.signal, signal_id=signal_id)
                self.repository.create_signal(created)
                self.notification_sink.enqueue(self._signal_notification(created))

        if not state.signal_generation_paused:
            self._enqueue_due_reports(now, risk)

        status = (
            RunStatus.DEGRADED
            if collection.status is CollectionStatus.DEGRADED or issues
            else RunStatus.OK
        )
        return self._finish(
            run_id,
            now,
            status,
            True,
            state,
            processed,
            created,
            risk,
            tuple(issues),
        )

    def _monitor_signal(
        self, signal_id: str, series: CandleSeries, now: datetime
    ) -> tuple[int, bool]:
        lifecycle = LifecycleReplayEngine(self.repository)
        management = PositionManagementEngine(self.repository)
        processed = 0
        for candle in series.candles:
            single = CandleSeries((candle,))
            replay = lifecycle.replay(signal_id, single, now)
            processed += replay.processed_candles
            for action in replay.actions:
                if action is not LifecycleAction.ACTIVATION_TARGET_DEFERRED:
                    self.notification_sink.enqueue(
                        RuntimeNotification(
                            "LIFECYCLE",
                            f"{signal_id}: {action.value}",
                            f"notify:{signal_id}:{iso_utc(candle.open_time)}:{action.value}",
                            signal_id,
                        )
                    )
            current = self.repository.get_lifecycle_signal(signal_id)
            managed_active = any(
                track.variant is OutcomeVariant.MANAGED for track in current.active_tracks
            )
            if current.status is SignalStatus.ACTIVE and managed_active:
                decision = management.replay(signal_id, single, now)
                for item in decision.decisions:
                    if item.changes_managed_result:
                        self.notification_sink.enqueue(
                            RuntimeNotification(
                                "MANAGEMENT",
                                f"{signal_id}: {item.action.value} — {item.reason}",
                                f"notify:{item.dedupe_key}",
                                signal_id,
                            )
                        )
        final = self.repository.get_lifecycle_signal(signal_id)
        return processed, any(
            track.variant is OutcomeVariant.MANAGED for track in final.active_tracks
        )

    def _enqueue_due_reports(self, now: datetime, risk: RiskAssessment) -> None:
        local = now.astimezone(ZoneInfo("Africa/Casablanca"))
        if local.hour != 0 or local.minute != 0:
            return
        engine = ReportEngine(self.repository)
        kinds = [ReportKind.DAILY]
        if local.weekday() == 0:
            kinds.append(ReportKind.WEEKLY)
        if local.day == 1:
            kinds.append(ReportKind.MONTHLY)
        documents = [engine.generate(kind, now) for kind in kinds]
        documents.append(engine.generate_news_risk(now, risk))
        for document in documents:
            self.notification_sink.enqueue(
                RuntimeNotification("REPORT", document.text, document.dedupe_key)
            )

    @staticmethod
    def _signal_notification(signal: Signal) -> RuntimeNotification:
        terms = signal.terms
        targets = ", ".join(format(target.price, "f") for target in terms.targets)
        text = "\n".join(
            (
                f"BTC Sentinel paper signal {signal.signal_id}",
                f"{terms.side.value} entry {terms.entry_low}-{terms.entry_high}",
                f"Stop {terms.original_stop} | Targets {targets}",
                f"Setup score {signal.setup_score}/100 (not a win probability)",
                "Paper analysis only — no order was placed.",
            )
        )
        return RuntimeNotification(
            "SIGNAL", text, f"notify:signal:{signal.signal_id}:created", signal.signal_id
        )

    def _finish(
        self,
        run_id: str,
        now: datetime,
        status: RunStatus,
        data_fresh: bool,
        state: RuntimeState,
        processed: int,
        created: Signal | None,
        risk: RiskAssessment | None,
        issues: tuple[str, ...],
    ) -> RunSummary:
        summary = RunSummary(
            run_id=run_id,
            started_at=now,
            finished_at=now,
            status=status,
            data_fresh=data_fresh,
            monitored_signals=len(state.monitored_signal_ids),
            processed_candles=processed,
            signal_created=None if created is None else created.signal_id,
            news_decision=None if risk is None else risk.decision.value,
            issues=issues,
        )
        return summary

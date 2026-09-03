import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from btc_sentinel.domain.enums import SignalStatus
from btc_sentinel.market_data.enums import MarketInterval
from btc_sentinel.market_data.models import CollectionResult, CollectionStatus, DataIssue
from btc_sentinel.news.models import NewsCollection
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from btc_sentinel.runtime.orchestrator import (
    PaperEngineOrchestrator,
    RunStatus,
    RuntimeNotification,
    RuntimeState,
)
from btc_sentinel.signals import SignalHistory
from tests.analysis_fixtures import ANALYSIS_NOW, analysis_snapshot
from tests.factories import long_signal
from tests.market_data_fixtures import make_series

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"


class StaticMarketCollector:
    def __init__(self, result: CollectionResult) -> None:
        self.result = result

    def collect(self) -> CollectionResult:
        return self.result


class EmptyNewsCollector:
    def collect(self, as_of):
        return NewsCollection(as_of, (), (), ())


class StaticStateProvider:
    def __init__(self, state: RuntimeState) -> None:
        self.state = state

    def load(self) -> RuntimeState:
        return self.state


class MemorySink:
    def __init__(self) -> None:
        self.notifications: list[RuntimeNotification] = []
        self.health = []

    def enqueue(self, notification: RuntimeNotification) -> None:
        self.notifications.append(notification)

    def record(self, summary) -> None:
        self.health.append(summary)


class FailingNotificationSink(MemorySink):
    def enqueue(self, notification: RuntimeNotification) -> None:
        raise RuntimeError("sensitive upstream detail")


def accepted_collection() -> CollectionResult:
    snapshot = analysis_snapshot()
    snapshot = replace(
        snapshot,
        spot_series=(
            *snapshot.spot_series,
            make_series(MarketInterval.ONE_MINUTE, count=3, as_of=ANALYSIS_NOW),
        ),
    )
    return CollectionResult(CollectionStatus.ACCEPTED, snapshot, ())


class RuntimeOrchestratorTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = SqliteRepository(
            Path(self.temporary_directory.name) / "runtime.sqlite3", MIGRATION
        )
        self.repository.migrate()
        self.sink = MemorySink()

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def orchestrator(self, state: RuntimeState, collection=None) -> PaperEngineOrchestrator:
        return PaperEngineOrchestrator(
            repository=self.repository,
            market_collector=StaticMarketCollector(collection or accepted_collection()),
            news_collector=EmptyNewsCollector(),
            state_provider=StaticStateProvider(state),
            notification_sink=self.sink,
            health_sink=self.sink,
        )

    def test_aligned_run_persists_one_signal_and_notification(self) -> None:
        state = RuntimeState(False, (), SignalHistory())
        summary = self.orchestrator(state).run("run-001", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.OK)
        self.assertEqual(summary.signal_created, "BTC-20260802-001")
        self.assertEqual(self.repository.get_signal_status(summary.signal_created).value, "PENDING")
        self.assertEqual([item.message_type for item in self.sink.notifications], ["SIGNAL"])
        self.assertIn("not a win probability", self.sink.notifications[0].text)
        self.assertEqual(self.sink.health, [summary])

    def test_pause_blocks_new_signals_but_run_remains_healthy(self) -> None:
        state = RuntimeState(True, (), SignalHistory())
        summary = self.orchestrator(state).run("run-002", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.OK)
        self.assertIsNone(summary.signal_created)
        self.assertEqual(self.repository.list_report_signals(SignalStatus.PENDING), ())
        self.assertEqual(self.sink.notifications, [])

    def test_required_market_failure_records_bounded_failed_health(self) -> None:
        rejected = CollectionResult(
            CollectionStatus.REJECTED,
            None,
            (DataIssue("STALE", "core", "upstream body must not be copied", True),),
        )
        state = RuntimeState(False, (), SignalHistory())
        summary = self.orchestrator(state, rejected).run("run-003", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.FAILED)
        self.assertFalse(summary.data_fresh)
        self.assertEqual(summary.issues, ("STALE",))
        self.assertNotIn("upstream body", repr(summary))
        self.assertEqual(self.sink.health, [summary])

    def test_same_run_uses_strict_cooldown_without_allocating_an_id(self) -> None:
        state = RuntimeState(
            False,
            (),
            SignalHistory(last_signal_at=ANALYSIS_NOW),
        )
        summary = self.orchestrator(state).run("run-004", ANALYSIS_NOW)

        self.assertIsNone(summary.signal_created)
        counter = self.repository._connection.execute(
            "SELECT COUNT(*) FROM signal_id_counters"
        ).fetchone()[0]
        self.assertEqual(counter, 0)

    def test_inconsistent_active_bootstrap_fails_closed_for_new_signals(self) -> None:
        state = RuntimeState(
            False,
            (),
            SignalHistory(active_managed_signal=True),
        )
        summary = self.orchestrator(state).run("run-004b", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.OK)
        self.assertIsNone(summary.signal_created)
        counter = self.repository._connection.execute(
            "SELECT COUNT(*) FROM signal_id_counters"
        ).fetchone()[0]
        self.assertEqual(counter, 0)

    def test_pending_signal_is_activated_and_managed_in_candle_order(self) -> None:
        original = long_signal()
        created = ANALYSIS_NOW.replace(hour=11, minute=57, second=0)
        terms = replace(
            original.terms,
            created_at=created,
            data_timestamp=created,
            expires_at=created.replace(hour=15),
        )
        signal = replace(original, terms=terms)
        self.repository.create_signal(signal)
        state = RuntimeState(
            False,
            (signal.signal_id,),
            SignalHistory(last_signal_at=created),
        )

        summary = self.orchestrator(state).run("run-005", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.OK)
        self.assertEqual(summary.processed_candles, 3)
        self.assertIsNone(summary.signal_created)
        lifecycle = self.repository.get_lifecycle_signal(signal.signal_id)
        self.assertIs(lifecycle.status, SignalStatus.ACTIVE)
        decisions = self.repository._connection.execute(
            "SELECT COUNT(*) FROM management_decisions WHERE signal_id = ?",
            (signal.signal_id,),
        ).fetchone()[0]
        self.assertEqual(decisions, 3)
        self.assertEqual(
            [item.text for item in self.sink.notifications],
            [f"{signal.signal_id}: ACTIVATED"],
        )

    def test_unexpected_runtime_failure_still_records_bounded_health(self) -> None:
        failing = FailingNotificationSink()
        engine = PaperEngineOrchestrator(
            repository=self.repository,
            market_collector=StaticMarketCollector(accepted_collection()),
            news_collector=EmptyNewsCollector(),
            state_provider=StaticStateProvider(RuntimeState(False, (), SignalHistory())),
            notification_sink=failing,
            health_sink=failing,
        )

        summary = engine.run("run-006", ANALYSIS_NOW)

        self.assertIs(summary.status, RunStatus.FAILED)
        self.assertEqual(summary.issues, ("runtime:RuntimeError",))
        self.assertNotIn("sensitive upstream detail", repr(summary))
        self.assertEqual(failing.health, [summary])

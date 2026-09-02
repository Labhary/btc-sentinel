import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from btc_sentinel.domain.enums import (
    MarketRegime,
    OutcomeResult,
    OutcomeVariant,
    Side,
    SignalStatus,
    TradeEventType,
)
from btc_sentinel.domain.models import Target
from btc_sentinel.news.engine import NewsRiskEngine
from btc_sentinel.news.models import NewsCollection
from btc_sentinel.persistence.sqlite_repository import SqliteRepository
from btc_sentinel.reports import ReportEngine, ReportKind, ReportSignal
from btc_sentinel.statistics import OutcomeSample
from tests.factories import long_signal
from tests.news_fixtures import NEWS_NOW, item

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_initial.sql"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class FakeReportRepository:
    def __init__(
        self,
        samples: tuple[OutcomeSample, ...] = (),
        active: tuple[ReportSignal, ...] = (),
        pending: tuple[ReportSignal, ...] = (),
    ) -> None:
        self.samples = samples
        self.active = active
        self.pending = pending
        self.last_window: tuple[datetime | None, datetime | None] | None = None

    def list_outcome_samples(
        self, start_at: datetime | None = None, end_at: datetime | None = None
    ) -> tuple[OutcomeSample, ...]:
        self.last_window = (start_at, end_at)
        return tuple(
            sample
            for sample in self.samples
            if (start_at is None or sample.closed_at >= start_at)
            and (end_at is None or sample.closed_at < end_at)
        )

    def list_report_signals(self, status: SignalStatus) -> tuple[ReportSignal, ...]:
        return self.active if status is SignalStatus.ACTIVE else self.pending


def sample(result: OutcomeResult, result_r: str, variant: OutcomeVariant) -> OutcomeSample:
    return OutcomeSample(
        signal_id="BTC-20260902-001",
        variant=variant,
        result=result,
        result_r=Decimal(result_r),
        closed_at=NOW - timedelta(minutes=1),
        strategy_version="rules-v0.6.0",
    )


def report_signal(status: SignalStatus) -> ReportSignal:
    return ReportSignal(
        signal_id="BTC-20260902-001",
        status=status,
        side=Side.LONG,
        regime=MarketRegime.BULLISH_TREND,
        setup_score=88,
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=3),
        entry_low=Decimal("100"),
        entry_high=Decimal("101"),
        original_stop=Decimal("95"),
        targets=(Target(1, Decimal("114")), Target(2, Decimal("120"))),
        strategy_version="rules-v0.6.0",
        fill_price=Decimal("101") if status is SignalStatus.ACTIVE else None,
        activated_at=NOW - timedelta(minutes=30) if status is SignalStatus.ACTIVE else None,
        managed_stop=Decimal("101.15") if status is SignalStatus.ACTIVE else None,
        fixed_track_active=status is SignalStatus.ACTIVE,
        managed_track_active=status is SignalStatus.ACTIVE,
    )


class ReportEngineTests(TestCase):
    def test_empty_period_does_not_report_zero_percent_as_evidence(self) -> None:
        document = ReportEngine(FakeReportRepository()).generate(ReportKind.DAILY, NOW)
        self.assertIn("no resolved outcomes (n=0); win rate unavailable", document.text)
        self.assertNotIn("strict wins 0.0%", document.text)

    def test_small_perfect_sample_displays_wide_wilson_interval(self) -> None:
        repository = FakeReportRepository(
            samples=(sample(OutcomeResult.WIN, "2", OutcomeVariant.MANAGED),)
        )
        document = ReportEngine(repository).generate(ReportKind.DAILY, NOW)
        self.assertIn("strict wins 100.0% (n=1", document.text)
        self.assertIn("20.7% to 100.0%", document.text)
        self.assertIn("not a forecast or win-rate guarantee", document.text)

    def test_break_even_and_early_exit_do_not_become_strict_wins(self) -> None:
        repository = FakeReportRepository(
            samples=(
                sample(OutcomeResult.BREAK_EVEN, "0", OutcomeVariant.MANAGED),
                sample(OutcomeResult.EARLY_EXIT, "0.5", OutcomeVariant.MANAGED),
            )
        )
        text = ReportEngine(repository).generate(ReportKind.WEEKLY, NOW).text
        self.assertIn("strict wins 0.0% (n=2", text)
        self.assertIn("W 0 / L 0 / BE 1 / early 1", text)

    def test_casablanca_daily_window_is_converted_back_to_utc(self) -> None:
        repository = FakeReportRepository()
        ReportEngine(repository).generate(ReportKind.DAILY, NOW)
        assert repository.last_window is not None
        start, end = repository.last_window
        self.assertEqual(start, datetime(2026, 9, 1, 23, 0, tzinfo=UTC))
        self.assertEqual(end, NOW)

    def test_week_starts_on_local_monday_and_month_on_local_first(self) -> None:
        repository = FakeReportRepository()
        ReportEngine(repository).generate(ReportKind.WEEKLY, NOW)
        assert repository.last_window is not None
        self.assertEqual(repository.last_window[0], datetime(2026, 8, 30, 23, 0, tzinfo=UTC))
        ReportEngine(repository).generate(ReportKind.MONTHLY, NOW)
        assert repository.last_window is not None
        self.assertEqual(repository.last_window[0], datetime(2026, 8, 31, 23, 0, tzinfo=UTC))

    def test_active_and_pending_reports_do_not_mix_state(self) -> None:
        repository = FakeReportRepository(
            active=(report_signal(SignalStatus.ACTIVE),),
            pending=(report_signal(SignalStatus.PENDING),),
        )
        active = ReportEngine(repository).generate(ReportKind.ACTIVE, NOW).text
        pending = ReportEngine(repository).generate(ReportKind.PENDING, NOW).text
        self.assertIn("fill 101", active)
        self.assertIn("active tracks FIXED/MANAGED", active)
        self.assertNotIn("expires", active)
        self.assertIn("expires", pending)
        self.assertNotIn("fill 101", pending)

    def test_news_missing_future_or_stale_never_renders_clear(self) -> None:
        engine = ReportEngine(FakeReportRepository())
        missing = engine.generate_news_risk(NOW, None).text
        assessment = NewsRiskEngine().evaluate(NewsCollection(NEWS_NOW, (), (), ()), NEWS_NOW)
        future = engine.generate_news_risk(NEWS_NOW - timedelta(minutes=1), assessment).text
        stale = engine.generate_news_risk(NEWS_NOW + timedelta(hours=1), assessment).text
        self.assertIn("Status: UNAVAILABLE", missing)
        self.assertIn("Status: UNAVAILABLE", future)
        self.assertIn("Status: STALE", stale)
        self.assertNotIn("Status: CLEAR", "\n".join((missing, future, stale)))

    def test_fresh_blocking_news_is_labeled_and_bounded(self) -> None:
        assessment = NewsRiskEngine().evaluate(
            NewsCollection(NEWS_NOW, (item("SEC approves spot Bitcoin ETF"),), (), ()),
            NEWS_NOW,
        )
        document = ReportEngine(FakeReportRepository()).generate_news_risk(NEWS_NOW, assessment)
        self.assertIn("Status: BLOCK", document.text)
        self.assertIn("Event: SEC approves spot Bitcoin ETF", document.text)
        self.assertLessEqual(len(document.text), 4096)

    def test_telegram_payload_has_no_chat_identity_or_send_side_effect(self) -> None:
        document = ReportEngine(FakeReportRepository()).generate(ReportKind.ACTIVE, NOW)
        self.assertEqual(
            document.as_telegram_payload(),
            {"text": document.text, "disable_web_page_preview": True},
        )
        self.assertNotIn("chat_id", document.as_telegram_payload())

    def test_generation_is_deterministic(self) -> None:
        engine = ReportEngine(FakeReportRepository())
        first = engine.generate(ReportKind.MONTHLY, NOW)
        second = engine.generate(ReportKind.MONTHLY, NOW)
        self.assertEqual(first, second)

    def test_period_retry_keeps_stable_dedupe_key(self) -> None:
        engine = ReportEngine(FakeReportRepository())
        first = engine.generate(ReportKind.DAILY, NOW)
        retry = engine.generate(ReportKind.DAILY, NOW + timedelta(minutes=5))
        self.assertEqual(first.dedupe_key, retry.dedupe_key)

    def test_long_active_report_is_safely_truncated_to_telegram_limit(self) -> None:
        signals = tuple(report_signal(SignalStatus.ACTIVE) for _ in range(100))
        document = ReportEngine(FakeReportRepository(active=signals)).generate(
            ReportKind.ACTIVE, NOW
        )
        self.assertLessEqual(len(document.text), 4096)
        self.assertTrue(document.text.endswith("[Report truncated safely]"))


class ReportPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "reports.sqlite3"
        self.repository = SqliteRepository(database, MIGRATION)
        self.repository.migrate()

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_repository_lists_pending_and_active_terms(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        pending = self.repository.list_report_signals(SignalStatus.PENDING)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].targets[0].price, Decimal("114"))
        self.repository.activate_signal(
            signal.signal_id,
            signal.terms.conservative_entry,
            signal.terms.created_at + timedelta(minutes=1),
            "report:activate",
        )
        self.assertEqual(self.repository.list_report_signals(SignalStatus.PENDING), ())
        active = self.repository.list_report_signals(SignalStatus.ACTIVE)
        self.assertEqual(active[0].fill_price, Decimal("101"))
        self.assertTrue(active[0].managed_track_active)

    def test_generating_reports_does_not_write_outbox_or_statistics(self) -> None:
        signal = long_signal()
        self.repository.create_signal(signal)
        before = self.repository._connection.total_changes
        ReportEngine(self.repository).generate(ReportKind.PENDING, NOW)
        after = self.repository._connection.total_changes
        self.assertEqual(before, after)
        outbox = self.repository._connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        snapshots = self.repository._connection.execute(
            "SELECT COUNT(*) FROM statistics_snapshots"
        ).fetchone()[0]
        self.assertEqual((outbox, snapshots), (0, 0))

    def test_outcome_window_is_half_open_and_returns_strict_samples(self) -> None:
        signal = long_signal()
        closed_at = signal.terms.created_at + timedelta(minutes=2)
        self.repository.create_signal(signal)
        self.repository.activate_signal(
            signal.signal_id,
            signal.terms.conservative_entry,
            signal.terms.created_at + timedelta(minutes=1),
            "report:window:activate",
        )
        self.repository.close_track(
            signal_id=signal.signal_id,
            variant=OutcomeVariant.MANAGED,
            result=OutcomeResult.WIN,
            result_r=Decimal("2"),
            result_percent=Decimal("1"),
            close_reason="Target reached.",
            close_event=TradeEventType.TP1_HIT,
            price=Decimal("114"),
            occurred_at=closed_at,
            dedupe_key="report:window:close",
        )
        included = self.repository.list_outcome_samples(closed_at, closed_at + timedelta(seconds=1))
        excluded = self.repository.list_outcome_samples(closed_at - timedelta(seconds=1), closed_at)
        self.assertEqual(len(included), 1)
        self.assertIs(included[0].result, OutcomeResult.WIN)
        self.assertEqual(excluded, ())

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import TestCase

from btc_sentinel.domain.enums import OutcomeResult, OutcomeVariant, SignalStatus, TradeEventType
from btc_sentinel.persistence import StateApiRepository
from tests.factories import long_signal

NOW = datetime(2026, 8, 2, 0, 2, tzinfo=UTC)


class FakeCommandClient:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.calls = []

    def repository_command(self, operation, arguments):
        self.calls.append((operation, arguments))
        return self.responses.pop(0) if self.responses else None


class StateApiRepositoryTests(TestCase):
    def test_allocate_and_create_use_decimal_strings_and_fixed_operation_names(self) -> None:
        client = FakeCommandClient(("BTC-20260802-007", None))
        repository = StateApiRepository(client)
        self.assertEqual(repository.allocate_signal_id(date(2026, 8, 2)), "BTC-20260802-007")
        repository.create_signal(long_signal("BTC-20260802-007"))

        self.assertEqual(client.calls[0], ("allocate_signal_id", {"business_date": "2026-08-02"}))
        operation, arguments = client.calls[1]
        self.assertEqual(operation, "create_signal")
        signal = arguments["signal"]
        self.assertEqual(signal["entry_low"], "100")
        self.assertEqual(signal["recommended_risk_percent"], "0.50")
        self.assertEqual([item["ordinal"] for item in signal["targets"]], [1, 2])
        notification = arguments["notification"]
        self.assertEqual(notification["message_type"], "SIGNAL")
        self.assertEqual(notification["signal_id"], "BTC-20260802-007")
        self.assertIn("not a win probability", notification["text"])

    def test_lifecycle_response_is_reconstructed_as_strict_domain_state(self) -> None:
        client = FakeCommandClient(
            (
                {
                    "signal_id": "BTC-20260802-001",
                    "status": "ACTIVE",
                    "side": "LONG",
                    "created_at": "2026-08-02T00:00:00Z",
                    "expires_at": "2026-08-02T04:00:00Z",
                    "entry_low": "100",
                    "entry_high": "101",
                    "original_stop": "95",
                    "estimated_cost_rate": "0.0015",
                    "recommended_risk_percent": "0.50",
                    "fill_price": "101",
                    "activated_at": "2026-08-02T00:01:00Z",
                    "targets": [{"ordinal": 1, "price": "114"}],
                    "active_tracks": [
                        {
                            "variant": "MANAGED",
                            "current_stop": "95",
                            "remaining_fraction": "1",
                            "realized_r": "0",
                        }
                    ],
                },
            )
        )
        state = StateApiRepository(client).get_lifecycle_signal("BTC-20260802-001")
        self.assertIs(state.status, SignalStatus.ACTIVE)
        self.assertEqual(state.fill_price, Decimal("101"))
        self.assertEqual(state.active_tracks[0].variant, OutcomeVariant.MANAGED)

    def test_close_includes_statistics_with_new_outcome(self) -> None:
        client = FakeCommandClient(({"items": [], "next_cursor": None}, "rules-v0.6.0", None))
        repository = StateApiRepository(client)
        repository.close_track(
            signal_id="BTC-20260802-001",
            variant=OutcomeVariant.MANAGED,
            result=OutcomeResult.WIN,
            result_r=Decimal("2.25"),
            result_percent=Decimal("1.125"),
            close_reason="Target reached.",
            close_event=TradeEventType.TP1_HIT,
            price=Decimal("114"),
            occurred_at=NOW,
            dedupe_key="close:test:1",
        )
        self.assertEqual(
            [call[0] for call in client.calls],
            [
                "list_outcome_samples",
                "get_signal_strategy",
                "close_track",
            ],
        )
        payload = client.calls[-1][1]["statistics_payload"]
        self.assertEqual(payload["managed"]["wins"], 1)
        self.assertEqual(payload["managed"]["strict_win_rate_percent"], "100")

    def test_outcomes_follow_bounded_cursors(self) -> None:
        first = {
            "items": [
                {
                    "signal_id": "BTC-20260802-001",
                    "variant": "FIXED",
                    "result": "WIN",
                    "result_r": "2",
                    "closed_at": "2026-08-02T01:00:00Z",
                    "strategy_version": "rules-v0.6.0",
                }
            ],
            "next_cursor": {
                "closed_at": "2026-08-02T01:00:00Z",
                "outcome_id": "outcome-1",
            },
        }
        second = {"items": [], "next_cursor": None}
        client = FakeCommandClient((first, second))
        outcomes = StateApiRepository(client).list_outcome_samples()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(client.calls[1][1]["cursor_id"], "outcome-1")

    def test_report_rows_keep_decimal_and_track_state(self) -> None:
        row = {
            "signal_id": "BTC-20260802-001",
            "status": "PENDING",
            "side": "LONG",
            "regime": "BULLISH_TREND",
            "setup_score": 88,
            "created_at": "2026-08-02T00:00:00Z",
            "expires_at": "2026-08-02T04:00:00Z",
            "entry_low": "100",
            "entry_high": "101",
            "original_stop": "95",
            "targets": [{"ordinal": 1, "price": "114"}],
            "strategy_version": "rules-v0.6.0",
            "fill_price": None,
            "activated_at": None,
            "managed_stop": None,
            "fixed_track_active": 0,
            "managed_track_active": 0,
        }
        repository = StateApiRepository(FakeCommandClient(([row],)))
        result = repository.list_report_signals(SignalStatus.PENDING)
        self.assertEqual(result[0].targets[0].price, Decimal("114"))
        self.assertFalse(result[0].managed_track_active)

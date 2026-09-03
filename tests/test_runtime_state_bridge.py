import json
from datetime import UTC, datetime
from unittest import TestCase

from btc_sentinel.config import SecretValue
from btc_sentinel.runtime import (
    RunStatus,
    RunSummary,
    RuntimeNotification,
    StateApiClient,
    StateApiRuntimeBridge,
)
from btc_sentinel.runtime.state_api import StateHttpResponse

NOW = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


class QueueAdapter:
    def __init__(self, payloads) -> None:
        self.payloads = list(payloads)
        self.calls = []

    def request(self, url, method, headers, body, timeout_seconds):
        self.calls.append((url, method, headers, body, timeout_seconds))
        status, payload = self.payloads.pop(0)
        return StateHttpResponse(status, json.dumps(payload).encode())


class StateApiRuntimeBridgeTests(TestCase):
    def bridge(self, adapter: QueueAdapter) -> StateApiRuntimeBridge:
        client = StateApiClient(
            "https://worker.example",
            SecretValue("s" * 40),
            adapter=adapter,
            clock=lambda: NOW,
            nonce_factory=lambda: "runtime_nonce_123456789",
        )
        return StateApiRuntimeBridge(client, clock=lambda: NOW)

    def test_load_maps_only_bounded_runtime_state(self) -> None:
        adapter = QueueAdapter(
            [
                (
                    200,
                    {
                        "schema_version": 1,
                        "symbol": "BTCUSDT",
                        "signal_generation_paused": False,
                        "latest_health_status": "OK",
                        "latest_health_at": "2026-09-02T23:55:00Z",
                        "monitored_signal_ids": ["BTC-20260902-001"],
                        "last_signal_at": "2026-09-02T23:00:00Z",
                        "active_managed_signal": True,
                    },
                )
            ]
        )
        state = self.bridge(adapter).load()
        self.assertEqual(state.monitored_signal_ids, ("BTC-20260902-001",))
        self.assertTrue(state.signal_history.active_managed_signal)
        self.assertEqual(state.signal_history.last_signal_at.hour, 23)

    def test_notification_and_health_use_separate_typed_paths(self) -> None:
        adapter = QueueAdapter(
            [
                (201, {"accepted": True, "duplicate": False}),
                (201, {"accepted": True, "duplicate": False}),
            ]
        )
        bridge = self.bridge(adapter)
        bridge.enqueue(
            RuntimeNotification("REPORT", "Daily paper report", "report:daily:2026-09-03:v0.10.0")
        )
        bridge.record(
            RunSummary(
                "run-20260903-001",
                NOW,
                NOW,
                RunStatus.OK,
                True,
                1,
                3,
                None,
                "CLEAR",
                (),
            )
        )
        self.assertEqual(
            [call[0].removeprefix("https://worker.example") for call in adapter.calls],
            ["/state/v1/notifications", "/state/v1/health"],
        )

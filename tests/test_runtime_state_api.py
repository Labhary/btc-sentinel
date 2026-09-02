import hashlib
import hmac
import json
import unittest
from datetime import UTC, datetime, timedelta

from btc_sentinel.config import SecretValue
from btc_sentinel.errors import ConfigurationError, DomainValidationError
from btc_sentinel.runtime import HealthRun, StateApiClient, StateApiError
from btc_sentinel.runtime.state_api import StateHttpResponse

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
SECRET_TEXT = "state-api-secret-abcdefghijklmnopqrstuvwxyz"


class FakeAdapter:
    def __init__(self, responses: list[StateHttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes, float]] = []

    def request(self, url, method, headers, body, timeout_seconds):
        self.calls.append((url, method, dict(headers), body, timeout_seconds))
        return self.responses.pop(0)


def response(status: int, payload: object) -> StateHttpResponse:
    return StateHttpResponse(status, json.dumps(payload).encode())


class StateApiClientTests(unittest.TestCase):
    def client(self, adapter: FakeAdapter) -> StateApiClient:
        return StateApiClient(
            "https://btc-sentinel.example",
            SecretValue(SECRET_TEXT),
            adapter=adapter,
            clock=lambda: NOW,
            nonce_factory=lambda: "fixed_nonce_123456789",
        )

    def test_bootstrap_signature_covers_method_path_time_nonce_and_body(self) -> None:
        adapter = FakeAdapter(
            [
                response(
                    200,
                    {
                        "schema_version": 1,
                        "symbol": "BTCUSDT",
                        "signal_generation_paused": True,
                        "latest_health_status": "DEGRADED",
                        "latest_health_at": "2026-09-02T11:55:00.000Z",
                    },
                )
            ]
        )
        bootstrap = self.client(adapter).bootstrap()
        self.assertTrue(bootstrap.signal_generation_paused)
        url, method, headers, body, timeout = adapter.calls[0]
        self.assertEqual(
            (url, method, body, timeout),
            ("https://btc-sentinel.example/state/v1/bootstrap", "GET", b"", 5),
        )
        canonical = "\n".join(
            (
                "GET",
                "/state/v1/bootstrap",
                str(int(NOW.timestamp())),
                "fixed_nonce_123456789",
                hashlib.sha256(b"").hexdigest(),
            )
        ).encode()
        expected = hmac.new(SECRET_TEXT.encode(), canonical, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-BTC-Signature"], expected)

    def test_health_payload_is_canonical_and_duplicate_is_visible(self) -> None:
        adapter = FakeAdapter([response(200, {"accepted": True, "duplicate": True})])
        inserted = self.client(adapter).record_health(
            HealthRun(
                run_id="run-1",
                job_name="paper-engine",
                started_at=NOW,
                finished_at=NOW + timedelta(minutes=1),
                status="DEGRADED",
                data_fresh=False,
                summary={"reason": "not_activated"},
                dedupe_key="health:run-1",
            )
        )
        self.assertFalse(inserted)
        body = json.loads(adapter.calls[0][3])
        self.assertEqual(body["status"], "DEGRADED")
        self.assertEqual(body["finished_at"], "2026-09-02T12:01:00Z")

    def test_rejects_untrusted_origin_bad_nonce_large_or_invalid_response(self) -> None:
        with self.assertRaises(ConfigurationError):
            StateApiClient("http://worker.example", SecretValue(SECRET_TEXT))
        with self.assertRaises(StateApiError):
            StateApiClient(
                "https://worker.example",
                SecretValue(SECRET_TEXT),
                adapter=FakeAdapter([]),
                nonce_factory=lambda: "short",
            ).bootstrap()
        with self.assertRaises(StateApiError):
            self.client(FakeAdapter([StateHttpResponse(200, b"x" * (64 * 1024 + 1))])).bootstrap()
        with self.assertRaises(StateApiError):
            self.client(FakeAdapter([StateHttpResponse(200, b"not-json")])).bootstrap()

    def test_health_model_rejects_future_inverted_or_unknown_values(self) -> None:
        with self.assertRaises(DomainValidationError):
            HealthRun("run", "job", NOW, NOW - timedelta(seconds=1), "OK", True, {}, "health:run")
        with self.assertRaises(DomainValidationError):
            HealthRun("run", "job", NOW, NOW, "RUNNING", True, {}, "health:run")
        with self.assertRaises(DomainValidationError):
            HealthRun("run", "job", NOW, NOW, "OK", True, {"bad": object()}, "health:run")
        with self.assertRaises(DomainValidationError):
            HealthRun(
                "run",
                "job",
                NOW,
                NOW,
                "OK",
                True,
                {"padding": "x" * (17 * 1024)},
                "health:run",
            )


if __name__ == "__main__":
    unittest.main()

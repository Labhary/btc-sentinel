import unittest
from collections.abc import Mapping

from btc_sentinel.market_data.errors import (
    MarketDataHttpError,
    MarketDataRateLimitError,
    MarketDataResponseTooLargeError,
    MarketDataTransportError,
    MarketDataValidationError,
)
from btc_sentinel.market_data.transport import (
    SPOT_ORIGIN,
    RawHttpResponse,
    RetryingJsonTransport,
    RetryPolicy,
)


class FakeAdapter:
    def __init__(self, responses: list[RawHttpResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout_seconds: float) -> RawHttpResponse:
        self.calls.append((url, timeout_seconds))
        if not self.responses:
            raise AssertionError("No fake response remains")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(
    status: int,
    body: bytes,
    headers: Mapping[str, str] | None = None,
) -> RawHttpResponse:
    return RawHttpResponse(status, headers or {}, body)


class RetryingJsonTransportTests(unittest.TestCase):
    def make_transport(
        self,
        adapter: FakeAdapter,
        *,
        sleeps: list[float] | None = None,
        maximum_response_bytes: int = 1000,
        cache_ttl_seconds: float = 0,
        clock: list[float] | None = None,
    ) -> RetryingJsonTransport:
        sleep_values = [] if sleeps is None else sleeps
        clock_values = [0.0] if clock is None else clock
        return RetryingJsonTransport(
            adapter,
            retry_policy=RetryPolicy(maximum_attempts=3, base_delay_seconds=0.25),
            timeout_seconds=4,
            maximum_response_bytes=maximum_response_bytes,
            cache_ttl_seconds=cache_ttl_seconds,
            sleeper=sleep_values.append,
            monotonic_clock=lambda: clock_values[0],
            jitter_source=lambda: 0.5,
        )

    def test_builds_only_an_allowlisted_get_url(self) -> None:
        adapter = FakeAdapter([response(200, b'{"ok":true}')])
        transport = self.make_transport(adapter)

        payload = transport.get_json(
            SPOT_ORIGIN,
            "/api/v3/klines",
            {"symbol": "BTCUSDT", "limit": 2, "interval": "1m"},
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(
            adapter.calls[0],
            (
                "https://api.binance.com/api/v3/klines?interval=1m&limit=2&symbol=BTCUSDT",
                4,
            ),
        )

    def test_retries_network_and_server_failures_with_backoff(self) -> None:
        sleeps: list[float] = []
        adapter = FakeAdapter(
            [
                MarketDataTransportError("network failed"),
                response(503, b"unavailable"),
                response(200, b"[]"),
            ]
        )
        transport = self.make_transport(adapter, sleeps=sleeps)

        self.assertEqual(transport.get_json(SPOT_ORIGIN, "/api/v3/time", {}), [])
        self.assertEqual(sleeps, [0.25, 0.5])
        self.assertEqual(len(adapter.calls), 3)

    def test_honors_bounded_retry_after_for_429(self) -> None:
        sleeps: list[float] = []
        adapter = FakeAdapter(
            [
                response(429, b"{}", {"Retry-After": "2"}),
                response(200, b'{"serverTime":1}'),
            ]
        )
        transport = self.make_transport(adapter, sleeps=sleeps)

        self.assertEqual(
            transport.get_json(SPOT_ORIGIN, "/api/v3/time", {}),
            {"serverTime": 1},
        )
        self.assertEqual(sleeps, [2.0])

    def test_does_not_retry_before_an_excessive_retry_after(self) -> None:
        sleeps: list[float] = []
        adapter = FakeAdapter(
            [
                response(429, b"{}", {"Retry-After": "31"}),
                response(200, b'{"serverTime":1}'),
            ]
        )
        transport = self.make_transport(adapter, sleeps=sleeps)

        with self.assertRaises(MarketDataRateLimitError) as context:
            transport.get_json(SPOT_ORIGIN, "/api/v3/time", {})
        self.assertEqual(context.exception.retry_after_seconds, 31.0)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(adapter.calls), 1)

    def test_does_not_retry_a_temporary_ip_ban(self) -> None:
        adapter = FakeAdapter([response(418, b"{}", {"Retry-After": "60"})])
        transport = self.make_transport(adapter)

        with self.assertRaises(MarketDataRateLimitError) as context:
            transport.get_json(SPOT_ORIGIN, "/api/v3/time", {})
        self.assertEqual(context.exception.status_code, 418)
        self.assertEqual(len(adapter.calls), 1)

    def test_does_not_retry_an_ordinary_client_error(self) -> None:
        adapter = FakeAdapter([response(400, b"{}")])
        transport = self.make_transport(adapter)

        with self.assertRaises(MarketDataHttpError) as context:
            transport.get_json(SPOT_ORIGIN, "/api/v3/time", {})
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(len(adapter.calls), 1)

    def test_rejects_invalid_json_and_oversized_responses(self) -> None:
        malformed = self.make_transport(FakeAdapter([response(200, b"not-json")]))
        with self.assertRaises(MarketDataValidationError):
            malformed.get_json(SPOT_ORIGIN, "/api/v3/time", {})

        oversized = self.make_transport(
            FakeAdapter([response(200, b"123456")]),
            maximum_response_bytes=5,
        )
        with self.assertRaises(MarketDataResponseTooLargeError):
            oversized.get_json(SPOT_ORIGIN, "/api/v3/time", {})

    def test_rejects_untrusted_origins_paths_and_parameters(self) -> None:
        transport = self.make_transport(FakeAdapter([]))
        invalid_requests = [
            ("http://api.binance.com", "/api/v3/time", {}),
            (SPOT_ORIGIN, "//other.example/path", {}),
            (SPOT_ORIGIN, "/api/../secret", {}),
            (SPOT_ORIGIN, "/api/v3/time", {"bad key": "value"}),
        ]
        for origin, path, params in invalid_requests:
            with (
                self.subTest(origin=origin, path=path, params=params),
                self.assertRaises(MarketDataValidationError),
            ):
                transport.get_json(origin, path, params)

    def test_short_cache_coalesces_calls_and_returns_independent_values(self) -> None:
        clock = [10.0]
        adapter = FakeAdapter(
            [
                response(200, b'{"items":[1]}'),
                response(200, b'{"items":[2]}'),
            ]
        )
        transport = self.make_transport(adapter, cache_ttl_seconds=1, clock=clock)

        first = transport.get_json(SPOT_ORIGIN, "/api/v3/time", {})
        assert isinstance(first, dict)
        first["items"].append(99)
        second = transport.get_json(SPOT_ORIGIN, "/api/v3/time", {})
        self.assertEqual(second, {"items": [1]})
        self.assertEqual(len(adapter.calls), 1)

        clock[0] = 12.0
        self.assertEqual(
            transport.get_json(SPOT_ORIGIN, "/api/v3/time", {}),
            {"items": [2]},
        )
        self.assertEqual(len(adapter.calls), 2)


if __name__ == "__main__":
    unittest.main()

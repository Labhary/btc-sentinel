"""Expected, secret-safe failures at the public market-data boundary."""

from __future__ import annotations

from btc_sentinel.errors import BtcSentinelError


class MarketDataError(BtcSentinelError):
    """Base class for a market-data failure that can safely reach health logs."""


class MarketDataValidationError(MarketDataError):
    """An upstream payload is malformed, contradictory, incomplete, or stale."""


class MarketDataTransportError(MarketDataError):
    """A public endpoint could not be reached reliably."""


class MarketDataRateLimitError(MarketDataTransportError):
    """Binance refused a request because of a rate limit or temporary IP ban."""

    def __init__(self, *, status_code: int, retry_after_seconds: float | None) -> None:
        super().__init__("Public market-data rate limit reached")
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class MarketDataHttpError(MarketDataTransportError):
    """A public endpoint returned a non-retryable HTTP response."""

    def __init__(self, *, status_code: int) -> None:
        super().__init__(f"Public market-data endpoint returned HTTP {status_code}")
        self.status_code = status_code


class MarketDataResponseTooLargeError(MarketDataTransportError):
    """An upstream response exceeded the configured safety limit."""

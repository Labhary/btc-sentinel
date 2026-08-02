"""Bounded public HTTP transport with retries, backoff, and an in-run cache."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from btc_sentinel.market_data.errors import (
    MarketDataHttpError,
    MarketDataRateLimitError,
    MarketDataResponseTooLargeError,
    MarketDataTransportError,
    MarketDataValidationError,
)

SPOT_ORIGIN = "https://api.binance.com"
FUTURES_ORIGIN = "https://fapi.binance.com"
ALLOWED_PUBLIC_ORIGINS = frozenset({SPOT_ORIGIN, FUTURES_ORIGIN})


@dataclass(frozen=True, slots=True)
class RawHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpAdapter(Protocol):
    def get(self, url: str, timeout_seconds: float) -> RawHttpResponse: ...


class JsonTransport(Protocol):
    def get_json(
        self,
        origin: str,
        path: str,
        params: Mapping[str, str | int],
    ) -> object: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


class UrllibHttpAdapter:
    """Small standard-library adapter; it never accepts headers from callers."""

    def __init__(self, maximum_response_bytes: int = 5_000_000) -> None:
        if maximum_response_bytes < 1:
            raise ValueError("maximum_response_bytes must be positive")
        self.maximum_response_bytes = maximum_response_bytes
        self._opener = build_opener(_NoRedirectHandler())

    def get(self, url: str, timeout_seconds: float) -> RawHttpResponse:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "btc-sentinel/market-data",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(self.maximum_response_bytes + 1)
                return RawHttpResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except HTTPError as exc:
            body = exc.read(self.maximum_response_bytes + 1)
            return RawHttpResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=body,
            )
        except (TimeoutError, URLError, OSError) as exc:
            raise MarketDataTransportError("Public market-data request failed") from exc


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 3
    base_delay_seconds: float = 0.25
    maximum_delay_seconds: float = 5.0
    maximum_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_attempts <= 6:
            raise ValueError("maximum_attempts must be between 1 and 6")
        if self.base_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("Retry delays cannot be negative")
        if self.maximum_retry_after_seconds < 0:
            raise ValueError("maximum_retry_after_seconds cannot be negative")


class RetryingJsonTransport:
    """GET-only transport for two fixed Binance public origins."""

    def __init__(
        self,
        adapter: HttpAdapter | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float = 5.0,
        maximum_response_bytes: int = 5_000_000,
        cache_ttl_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic_clock: Callable[[], float] = time.monotonic,
        jitter_source: Callable[[], float] = random.random,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if maximum_response_bytes < 1:
            raise ValueError("maximum_response_bytes must be positive")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds cannot be negative")
        self.adapter = adapter or UrllibHttpAdapter(maximum_response_bytes)
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_seconds = timeout_seconds
        self.maximum_response_bytes = maximum_response_bytes
        self.cache_ttl_seconds = cache_ttl_seconds
        self.sleeper = sleeper
        self.monotonic_clock = monotonic_clock
        self.jitter_source = jitter_source
        self._cache: dict[
            tuple[str, str, tuple[tuple[str, str | int], ...]],
            tuple[float, object],
        ] = {}

    def get_json(
        self,
        origin: str,
        path: str,
        params: Mapping[str, str | int],
    ) -> object:
        normalized_params = self._normalize_params(params)
        cache_key = (origin, path, normalized_params)
        now = self.monotonic_clock()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] >= now:
            return deepcopy(cached[1])

        url = self._build_url(origin, path, normalized_params)
        result = self._request_with_retries(url)
        if self.cache_ttl_seconds > 0:
            self._cache[cache_key] = (now + self.cache_ttl_seconds, deepcopy(result))
        return result

    def _request_with_retries(self, url: str) -> object:
        final_error: MarketDataTransportError | None = None
        for attempt in range(1, self.retry_policy.maximum_attempts + 1):
            try:
                response = self.adapter.get(url, self.timeout_seconds)
            except MarketDataTransportError as exc:
                final_error = exc
                if attempt == self.retry_policy.maximum_attempts:
                    raise
                self.sleeper(self._backoff_delay(attempt))
                continue

            if len(response.body) > self.maximum_response_bytes:
                raise MarketDataResponseTooLargeError("Public market-data response was too large")
            if response.status_code == 200:
                return self._decode_json(response.body)
            if response.status_code == 418:
                raise MarketDataRateLimitError(
                    status_code=418,
                    retry_after_seconds=self._retry_after(response.headers),
                )
            if response.status_code == 429:
                retry_after = self._retry_after(response.headers)
                final_error = MarketDataRateLimitError(
                    status_code=429,
                    retry_after_seconds=retry_after,
                )
                if attempt == self.retry_policy.maximum_attempts:
                    raise final_error
                if (
                    retry_after is not None
                    and retry_after > self.retry_policy.maximum_retry_after_seconds
                ):
                    raise final_error
                delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
                self.sleeper(delay)
                continue
            if response.status_code in {408, 500, 502, 503, 504}:
                final_error = MarketDataTransportError("Public market-data endpoint is unavailable")
                if attempt == self.retry_policy.maximum_attempts:
                    raise final_error
                self.sleeper(self._backoff_delay(attempt))
                continue
            raise MarketDataHttpError(status_code=response.status_code)

        assert final_error is not None
        raise final_error

    @staticmethod
    def _decode_json(body: bytes) -> object:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarketDataValidationError(
                "Public market-data response was not valid JSON"
            ) from exc

    def _backoff_delay(self, attempt: int) -> float:
        jitter = self.jitter_source()
        if not 0 <= jitter <= 1:
            jitter = 0.5
        multiplier = 0.75 + (0.5 * jitter)
        raw = self.retry_policy.base_delay_seconds * (2 ** (attempt - 1)) * multiplier
        return min(raw, self.retry_policy.maximum_delay_seconds)

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        raw = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value >= 0 else None

    @staticmethod
    def _normalize_params(
        params: Mapping[str, str | int],
    ) -> tuple[tuple[str, str | int], ...]:
        normalized: list[tuple[str, str | int]] = []
        for key, value in params.items():
            if not key or not key.replace("_", "").isalnum():
                raise MarketDataValidationError("Invalid public endpoint query parameter name")
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise MarketDataValidationError("Invalid public endpoint query parameter value")
            normalized.append((key, value))
        return tuple(sorted(normalized))

    @staticmethod
    def _build_url(
        origin: str,
        path: str,
        params: tuple[tuple[str, str | int], ...],
    ) -> str:
        if origin not in ALLOWED_PUBLIC_ORIGINS:
            raise MarketDataValidationError("Public endpoint origin is not allowlisted")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or parsed.query or parsed.fragment or parsed.path:
            raise MarketDataValidationError("Public endpoint origin is invalid")
        if not path.startswith("/") or "//" in path or ".." in path or "?" in path or "#" in path:
            raise MarketDataValidationError("Public endpoint path is invalid")
        query = urlencode(params)
        url = f"{origin}{path}{'?' + query if query else ''}"
        if len(url) > 2048:
            raise MarketDataValidationError("Public endpoint URL is too long")
        return url

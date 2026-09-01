"""GET-only transport restricted to the fixed Phase 5 source catalog."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from btc_sentinel.news.errors import NewsTransportError
from btc_sentinel.news.models import SourceSpec
from btc_sentinel.news.sources import GDELT_DISCOVERY, OFFICIAL_FEEDS


@dataclass(frozen=True, slots=True)
class FeedResponse:
    status_code: int
    body: bytes


class FeedAdapter(Protocol):
    def get(self, url: str, timeout_seconds: float) -> FeedResponse: ...


class _NoRedirect(HTTPRedirectHandler):
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


class UrllibFeedAdapter:
    def __init__(self, maximum_bytes: int = 2_000_000) -> None:
        self.maximum_bytes = maximum_bytes
        self._opener = build_opener(_NoRedirect())

    def get(self, url: str, timeout_seconds: float) -> FeedResponse:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": (
                    "application/rss+xml, application/atom+xml, text/calendar, application/json"
                ),
                "User-Agent": "btc-sentinel/0.5 (public research; no authentication)",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                return FeedResponse(response.status, response.read(self.maximum_bytes + 1))
        except HTTPError as exc:
            return FeedResponse(exc.code, exc.read(self.maximum_bytes + 1))
        except (TimeoutError, URLError, OSError) as exc:
            raise NewsTransportError("Public news request failed") from exc


class PublicNewsTransport:
    def __init__(
        self,
        adapter: FeedAdapter | None = None,
        *,
        timeout_seconds: float = 8.0,
        maximum_bytes: int = 2_000_000,
        maximum_attempts: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < timeout_seconds <= 30 or not 1 <= maximum_attempts <= 3:
            raise ValueError("News transport timeout or attempts are unsafe")
        self.adapter = adapter or UrllibFeedAdapter(maximum_bytes)
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes
        self.maximum_attempts = maximum_attempts
        self.sleeper = sleeper
        self.allowed_urls = frozenset(source.url for source in (*OFFICIAL_FEEDS, GDELT_DISCOVERY))

    def fetch(self, source: SourceSpec) -> bytes:
        if source.url not in self.allowed_urls:
            raise NewsTransportError("News URL is not in the fixed public allowlist")
        url = self._request_url(source)
        for attempt in range(1, self.maximum_attempts + 1):
            response = self.adapter.get(url, self.timeout_seconds)
            if len(response.body) > self.maximum_bytes:
                raise NewsTransportError("Public news response was too large")
            if response.status_code == 200:
                return response.body
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise NewsTransportError(
                    f"Public news endpoint returned HTTP {response.status_code}"
                )
            if attempt < self.maximum_attempts:
                self.sleeper(0.5 * attempt)
        raise NewsTransportError("Public news endpoint remained unavailable")

    @staticmethod
    def _request_url(source: SourceSpec) -> str:
        if source.source_id != GDELT_DISCOVERY.source_id:
            return source.url
        query = urlencode(
            {
                "query": "(bitcoin OR BTC) sourcelang:english",
                "mode": "ArtList",
                "maxrecords": 50,
                "format": "json",
                "sort": "DateDesc",
                "timespan": "1h",
            }
        )
        return f"{source.url}?{query}"

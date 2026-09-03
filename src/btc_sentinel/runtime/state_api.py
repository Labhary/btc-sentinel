"""Bounded HMAC client for the fixed Worker state API."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from btc_sentinel.config import SecretValue
from btc_sentinel.errors import ConfigurationError, DomainValidationError
from btc_sentinel.time_utils import ensure_utc, iso_utc, utc_now

_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_REQUEST_BYTES = 32 * 1024


class StateApiError(RuntimeError):
    """The authenticated state boundary failed or returned invalid data."""


@dataclass(frozen=True, slots=True)
class StateHttpResponse:
    status_code: int
    body: bytes


class StateHttpAdapter(Protocol):
    def request(
        self,
        url: str,
        method: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> StateHttpResponse: ...


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


class UrllibStateHttpAdapter:
    def __init__(self, maximum_response_bytes: int = _MAX_RESPONSE_BYTES) -> None:
        self.maximum_response_bytes = maximum_response_bytes
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        url: str,
        method: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> StateHttpResponse:
        request = Request(
            url, data=body if method == "POST" else None, method=method, headers=headers
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(self.maximum_response_bytes + 1)
                return StateHttpResponse(response.status, payload)
        except HTTPError as exc:
            return StateHttpResponse(exc.code, exc.read(self.maximum_response_bytes + 1))
        except (TimeoutError, URLError, OSError) as exc:
            raise StateApiError("State API network request failed") from exc


@dataclass(frozen=True, slots=True)
class StateBootstrap:
    signal_generation_paused: bool
    latest_health_status: str | None
    latest_health_at: str | None
    monitored_signal_ids: tuple[str, ...] = ()
    last_signal_at: str | None = None
    active_managed_signal: bool = False


@dataclass(frozen=True, slots=True)
class HealthRun:
    run_id: str
    job_name: str
    started_at: datetime
    finished_at: datetime
    status: str
    data_fresh: bool
    summary: Mapping[str, object]
    dedupe_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        if self.finished_at < self.started_at:
            raise DomainValidationError("Health run finished before it started")
        if self.status not in {"OK", "DEGRADED", "FAILED"}:
            raise DomainValidationError("Health run status is invalid")
        for value in (self.run_id, self.job_name, self.dedupe_key):
            if (
                not value
                or len(value) > 128
                or not all(character.isalnum() or character in "_.:-" for character in value)
            ):
                raise DomainValidationError("Health run identity is invalid")
        try:
            encoded_summary = json.dumps(
                self.summary, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Health run summary must be JSON serializable") from exc
        if len(encoded_summary) > 16 * 1024:
            raise DomainValidationError("Health run summary is too large")

    def payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "started_at": iso_utc(self.started_at),
            "finished_at": iso_utc(self.finished_at),
            "status": self.status,
            "data_fresh": self.data_fresh,
            "summary": dict(self.summary),
            "dedupe_key": self.dedupe_key,
        }


class StateApiClient:
    def __init__(
        self,
        base_url: str,
        secret: SecretValue,
        *,
        adapter: StateHttpAdapter | None = None,
        timeout_seconds: float = 5,
        clock: Callable[[], datetime] = utc_now,
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError("STATE_API_BASE_URL must be an HTTPS origin")
        if not 0 < timeout_seconds <= 15:
            raise ConfigurationError("State API timeout must be between zero and 15 seconds")
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.adapter = adapter or UrllibStateHttpAdapter()
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.nonce_factory = nonce_factory

    def bootstrap(self) -> StateBootstrap:
        payload = self._request("GET", "/state/v1/bootstrap", None, {200})
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("symbol") != "BTCUSDT"
            or not isinstance(payload.get("signal_generation_paused"), bool)
        ):
            raise StateApiError("State bootstrap response is invalid")
        health_status = payload.get("latest_health_status")
        health_at = payload.get("latest_health_at")
        if health_status is not None and not isinstance(health_status, str):
            raise StateApiError("State bootstrap health status is invalid")
        if health_at is not None and not isinstance(health_at, str):
            raise StateApiError("State bootstrap health time is invalid")
        monitored = payload.get("monitored_signal_ids")
        last_signal_at = payload.get("last_signal_at")
        active_managed = payload.get("active_managed_signal")
        if (
            not isinstance(monitored, list)
            or len(monitored) > 100
            or any(not isinstance(value, str) for value in monitored)
            or len(monitored) != len(set(monitored))
        ):
            raise StateApiError("State bootstrap monitored signals are invalid")
        if last_signal_at is not None and not isinstance(last_signal_at, str):
            raise StateApiError("State bootstrap signal time is invalid")
        if not isinstance(active_managed, bool):
            raise StateApiError("State bootstrap active state is invalid")
        return StateBootstrap(
            payload["signal_generation_paused"],
            health_status,
            health_at,
            tuple(monitored),
            last_signal_at,
            active_managed,
        )

    def enqueue_notification(
        self,
        *,
        message_type: str,
        text: str,
        dedupe_key: str,
        signal_id: str | None,
        created_at: datetime,
    ) -> bool:
        payload = self._request(
            "POST",
            "/state/v1/notifications",
            {
                "message_type": message_type,
                "text": text,
                "dedupe_key": dedupe_key,
                "signal_id": signal_id,
                "created_at": iso_utc(created_at),
            },
            {200, 201},
        )
        if (
            not isinstance(payload, dict)
            or payload.get("accepted") is not True
            or not isinstance(payload.get("duplicate"), bool)
        ):
            raise StateApiError("State notification response is invalid")
        return not payload["duplicate"]

    def record_health(self, run: HealthRun) -> bool:
        payload = self._request("POST", "/state/v1/health", run.payload(), {200, 201})
        if (
            not isinstance(payload, dict)
            or payload.get("accepted") is not True
            or not isinstance(payload.get("duplicate"), bool)
        ):
            raise StateApiError("State health response is invalid")
        return not payload["duplicate"]

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None,
        accepted_statuses: set[int],
    ) -> object:
        body = (
            b""
            if payload is None
            else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if len(body) > _MAX_REQUEST_BYTES:
            raise StateApiError("State API request body is too large")
        now = ensure_utc(self.clock())
        timestamp = str(int(now.timestamp()))
        nonce = self.nonce_factory()
        if not 16 <= len(nonce) <= 128 or not all(
            character.isalnum() or character in "_-" for character in nonce
        ):
            raise StateApiError("State API nonce factory returned an invalid nonce")
        body_digest = hashlib.sha256(body).hexdigest()
        canonical = "\n".join((method, path, timestamp, nonce, body_digest)).encode("utf-8")
        signature = hmac.new(
            self.secret.reveal().encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "btc-sentinel/state-client",
            "X-BTC-Timestamp": timestamp,
            "X-BTC-Nonce": nonce,
            "X-BTC-Signature": signature,
        }
        response = self.adapter.request(
            f"{self.base_url}{path}", method, headers, body, self.timeout_seconds
        )
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise StateApiError("State API response is too large")
        if response.status_code not in accepted_statuses:
            raise StateApiError(f"State API returned HTTP {response.status_code}")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateApiError("State API response is not valid JSON") from exc

"""Best-effort log redaction; secret-safe logging still starts at the caller."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "bot_token",
    "chat_id",
    "password",
    "secret",
    "signature",
    "telegram_admin_user_id",
    "telegram_bot_token",
    "token",
    "webhook_secret",
}
_TELEGRAM_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])\d{6,12}:[A-Za-z0-9_-]{20,}")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_ENV_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|SIGNATURE)[A-Z0-9_]*)\s*=\s*([^\s]+)"
)


def _redact_string(value: str) -> str:
    value = _TELEGRAM_TOKEN.sub("<redacted-telegram-token>", value)
    value = _BEARER.sub("Bearer <redacted>", value)
    return _ENV_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def redact(value: Any) -> Any:
    """Return a structurally similar value with common secrets removed."""
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            cleaned[key] = "<redacted>" if normalized in _SENSITIVE_KEYS else redact(child)
        return cleaned
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, tuple):
        return tuple(redact(child) for child in value)
    return value

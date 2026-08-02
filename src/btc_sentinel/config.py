"""Validated public configuration and deliberately opaque secret values."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from btc_sentinel.errors import ConfigurationError

_TELEGRAM_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")
_PLACEHOLDER_MARKERS = ("SET_IN_", "REPLACE", "CHANGEME", "NOT_HERE")


class SecretValue:
    """A secret that cannot be exposed accidentally through str/repr."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ConfigurationError("A required secret is empty")
        self.__value = value

    def reveal(self) -> str:
        """Return the value only at the exact outbound integration boundary."""
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return self.__value == other.__value


def _decimal_setting(
    env: Mapping[str, str], name: str, default: str, minimum: str, maximum: str
) -> Decimal:
    raw = env.get(name, default)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} must be a decimal number") from exc
    if not Decimal(minimum) <= value <= Decimal(maximum):
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class PublicSettings:
    """Non-secret settings safe to include in diagnostics."""

    app_env: str = "development"
    display_timezone: str = "Africa/Casablanca"
    trading_symbol: str = "BTCUSDT"
    minimum_planned_rr: Decimal = Decimal("2")
    default_risk_percent: Decimal = Decimal("0.50")
    maximum_risk_percent: Decimal = Decimal("1.00")
    maximum_active_trades: int = 1

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> PublicSettings:
        env = os.environ if source is None else source
        app_env = env.get("APP_ENV", "development").strip().lower()
        if app_env not in {"development", "test", "production"}:
            raise ConfigurationError("APP_ENV must be development, test, or production")

        timezone_name = env.get("DISPLAY_TIMEZONE", "Africa/Casablanca").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError("DISPLAY_TIMEZONE is not a valid IANA timezone") from exc
        if timezone_name != "Africa/Casablanca":
            raise ConfigurationError("Version 1 display timezone is fixed to Africa/Casablanca")

        symbol = env.get("TRADING_SYMBOL", "BTCUSDT").strip().upper()
        if symbol != "BTCUSDT":
            raise ConfigurationError("Version 1 is BTCUSDT-only")

        min_rr = _decimal_setting(env, "MIN_PLANNED_RR", "2", "2", "10")
        default_risk = _decimal_setting(env, "DEFAULT_RISK_PERCENT", "0.50", "0.01", "1.00")
        maximum_risk = _decimal_setting(env, "MAX_RISK_PERCENT", "1.00", "0.01", "1.00")
        if default_risk > maximum_risk:
            raise ConfigurationError("DEFAULT_RISK_PERCENT cannot exceed MAX_RISK_PERCENT")

        return cls(
            app_env=app_env,
            display_timezone=timezone_name,
            trading_symbol=symbol,
            minimum_planned_rr=min_rr,
            default_risk_percent=default_risk,
            maximum_risk_percent=maximum_risk,
        )


@dataclass(frozen=True, slots=True)
class DeploymentSecrets:
    """Production secrets loaded only at a deployment boundary."""

    telegram_bot_token: SecretValue
    telegram_admin_user_id: int
    telegram_webhook_secret: SecretValue
    state_api_hmac_secret: SecretValue

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> DeploymentSecrets:
        env = os.environ if source is None else source

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value or any(marker in value.upper() for marker in _PLACEHOLDER_MARKERS):
                raise ConfigurationError(f"{name} is required in the secret store")
            return value

        token = required("TELEGRAM_BOT_TOKEN")
        if not _TELEGRAM_TOKEN.fullmatch(token):
            raise ConfigurationError("TELEGRAM_BOT_TOKEN has an invalid format")

        raw_admin = required("TELEGRAM_ADMIN_USER_ID")
        try:
            admin_id = int(raw_admin)
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_ADMIN_USER_ID must be an integer") from exc
        if admin_id <= 0:
            raise ConfigurationError("TELEGRAM_ADMIN_USER_ID must be positive")

        webhook_secret = required("TELEGRAM_WEBHOOK_SECRET")
        hmac_secret = required("STATE_API_HMAC_SECRET")
        if len(webhook_secret) < 32:
            raise ConfigurationError("TELEGRAM_WEBHOOK_SECRET must contain at least 32 characters")
        if len(hmac_secret) < 32:
            raise ConfigurationError("STATE_API_HMAC_SECRET must contain at least 32 characters")
        if webhook_secret == hmac_secret:
            raise ConfigurationError("Webhook and state-API secrets must be different")

        return cls(
            telegram_bot_token=SecretValue(token),
            telegram_admin_user_id=admin_id,
            telegram_webhook_secret=SecretValue(webhook_secret),
            state_api_hmac_secret=SecretValue(hmac_secret),
        )

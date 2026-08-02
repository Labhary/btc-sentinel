"""UTC storage and Casablanca display conversion."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from btc_sentinel.errors import DomainValidationError

CASABLANCA = ZoneInfo("Africa/Casablanca")


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


def to_casablanca(value: datetime) -> datetime:
    return ensure_utc(value).astimezone(CASABLANCA)


def iso_utc(value: datetime) -> str:
    return ensure_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_casablanca(value: datetime) -> str:
    local = to_casablanca(value)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z (Africa/Casablanca)")

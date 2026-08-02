"""Opaque, stable identifiers shown to the owner and stored in D1."""

import re
from datetime import date

from btc_sentinel.errors import DomainValidationError

_SIGNAL_ID = re.compile(r"^BTC-(\d{8})-(\d{3,})$")


def format_signal_id(local_date: date, sequence: int) -> str:
    if sequence < 1:
        raise DomainValidationError("Signal sequence must be positive")
    return f"BTC-{local_date:%Y%m%d}-{sequence:03d}"


def validate_signal_id(value: str) -> str:
    match = _SIGNAL_ID.fullmatch(value)
    if not match:
        raise DomainValidationError("Signal ID must match BTC-YYYYMMDD-NNN")
    try:
        date.fromisoformat(f"{match.group(1)[0:4]}-{match.group(1)[4:6]}-{match.group(1)[6:8]}")
    except ValueError as exc:
        raise DomainValidationError("Signal ID contains an invalid calendar date") from exc
    if int(match.group(2)) < 1:
        raise DomainValidationError("Signal sequence must be positive")
    return value

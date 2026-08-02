"""Typed project errors that are safe to handle at system boundaries."""


class BtcSentinelError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(BtcSentinelError):
    """Configuration is missing or violates a safety invariant."""


class DomainValidationError(BtcSentinelError):
    """A domain record is internally inconsistent."""


class InvalidTransitionError(BtcSentinelError):
    """A lifecycle transition is not permitted."""


class RecordNotFoundError(BtcSentinelError):
    """A requested durable record does not exist."""


class DuplicateRecordError(BtcSentinelError):
    """An idempotency or uniqueness key already exists."""


class ConcurrencyError(BtcSentinelError):
    """A record changed after it was read."""


class SecurityError(BtcSentinelError):
    """A request or setting violates a security boundary."""

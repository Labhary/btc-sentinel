"""Durable repository implementations."""

from typing import TYPE_CHECKING, Any

from btc_sentinel.persistence.sqlite_repository import SqliteRepository

if TYPE_CHECKING:
    from btc_sentinel.persistence.state_api_repository import StateApiRepository

__all__ = ["SqliteRepository", "StateApiRepository"]


def __getattr__(name: str) -> Any:
    if name == "StateApiRepository":
        from btc_sentinel.persistence.state_api_repository import StateApiRepository

        return StateApiRepository
    raise AttributeError(name)

"""Explicit lifecycle transitions; target hits are events, not states."""

from btc_sentinel.domain.enums import SignalStatus, TradeEventType
from btc_sentinel.errors import InvalidTransitionError

_TRANSITIONS: dict[SignalStatus, frozenset[SignalStatus]] = {
    SignalStatus.PENDING: frozenset(
        {SignalStatus.ACTIVE, SignalStatus.EXPIRED, SignalStatus.CANCELLED}
    ),
    SignalStatus.ACTIVE: frozenset({SignalStatus.CLOSED}),
    SignalStatus.EXPIRED: frozenset(),
    SignalStatus.CANCELLED: frozenset(),
    SignalStatus.CLOSED: frozenset(),
}

_TRANSITION_EVENTS: dict[tuple[SignalStatus, SignalStatus], TradeEventType] = {
    (SignalStatus.PENDING, SignalStatus.ACTIVE): TradeEventType.ENTRY_ACTIVATED,
    (SignalStatus.PENDING, SignalStatus.EXPIRED): TradeEventType.ENTRY_EXPIRED,
    (SignalStatus.PENDING, SignalStatus.CANCELLED): TradeEventType.SIGNAL_CANCELLED,
    (SignalStatus.ACTIVE, SignalStatus.CLOSED): TradeEventType.CLOSED,
}


def assert_transition(current: SignalStatus, new: SignalStatus) -> None:
    if new not in _TRANSITIONS[current]:
        raise InvalidTransitionError(f"Signal cannot transition from {current} to {new}")


def transition_event(current: SignalStatus, new: SignalStatus) -> TradeEventType:
    assert_transition(current, new)
    return _TRANSITION_EVENTS[(current, new)]

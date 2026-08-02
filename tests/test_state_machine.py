import unittest

from btc_sentinel.domain.enums import SignalStatus, TradeEventType
from btc_sentinel.domain.state_machine import assert_transition, transition_event
from btc_sentinel.errors import InvalidTransitionError


class StateMachineTests(unittest.TestCase):
    def test_pending_can_activate(self) -> None:
        assert_transition(SignalStatus.PENDING, SignalStatus.ACTIVE)
        self.assertIs(
            transition_event(SignalStatus.PENDING, SignalStatus.ACTIVE),
            TradeEventType.ENTRY_ACTIVATED,
        )

    def test_active_can_close(self) -> None:
        assert_transition(SignalStatus.ACTIVE, SignalStatus.CLOSED)

    def test_terminal_signal_cannot_reopen(self) -> None:
        with self.assertRaises(InvalidTransitionError):
            assert_transition(SignalStatus.CLOSED, SignalStatus.ACTIVE)

    def test_pending_cannot_be_marked_closed_without_activation(self) -> None:
        with self.assertRaises(InvalidTransitionError):
            assert_transition(SignalStatus.PENDING, SignalStatus.CLOSED)


if __name__ == "__main__":
    unittest.main()

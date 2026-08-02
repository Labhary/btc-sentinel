import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from btc_sentinel.domain.enums import Side
from btc_sentinel.domain.ids import format_signal_id, validate_signal_id
from btc_sentinel.domain.models import SignalTerms, Target
from btc_sentinel.errors import DomainValidationError
from tests.factories import long_signal, short_signal


class IdentifierTests(unittest.TestCase):
    def test_formats_signal_id(self) -> None:
        self.assertEqual(format_signal_id(date(2026, 8, 2), 1), "BTC-20260802-001")

    def test_rejects_invalid_calendar_date(self) -> None:
        with self.assertRaises(DomainValidationError):
            validate_signal_id("BTC-20260231-001")


class SignalTermTests(unittest.TestCase):
    def test_valid_long_has_net_two_r_at_tp1(self) -> None:
        signal = long_signal()
        self.assertGreaterEqual(signal.terms.planned_r_for(signal.terms.targets[0]), Decimal("2"))

    def test_valid_short_has_net_two_r_at_tp1(self) -> None:
        signal = short_signal()
        self.assertIs(signal.terms.side, Side.SHORT)
        self.assertGreaterEqual(signal.terms.planned_r_for(signal.terms.targets[0]), Decimal("2"))

    def test_rejects_float_prices(self) -> None:
        signal = long_signal()
        with self.assertRaises(DomainValidationError):
            replace(signal.terms, entry_low=100.0)

    def test_rejects_tp1_below_net_two_r(self) -> None:
        signal = long_signal()
        with self.assertRaises(DomainValidationError):
            replace(
                signal.terms,
                targets=(Target(1, Decimal("110")), Target(2, Decimal("120"))),
            )

    def test_rejects_naive_timestamp(self) -> None:
        signal = long_signal()
        with self.assertRaises(DomainValidationError):
            replace(signal.terms, created_at=datetime(2026, 8, 2, 0, 0))

    def test_rejects_non_btc_symbol(self) -> None:
        signal = long_signal()
        with self.assertRaises(DomainValidationError):
            replace(signal.terms, symbol="ETHUSDT")

    def test_requires_expiry_after_creation(self) -> None:
        signal = long_signal()
        with self.assertRaises(DomainValidationError):
            replace(signal.terms, expires_at=signal.terms.created_at - timedelta(minutes=1))

    def test_constructs_terms_from_decimal_strings(self) -> None:
        created = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
        terms = SignalTerms(
            side=Side.LONG,
            entry_low="100",
            entry_high="101",
            original_stop="95",
            targets=(Target(1, "114"), Target(2, "120")),
            created_at=created,
            data_timestamp=created,
            expires_at=created + timedelta(hours=1),
            invalidation_condition="Structure fails.",
            expiration_condition="One hour passes.",
            recommended_risk_percent="0.5",
        )
        self.assertEqual(terms.entry_high, Decimal("101"))


if __name__ == "__main__":
    unittest.main()

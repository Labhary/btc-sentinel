import unittest
from datetime import UTC, datetime

from btc_sentinel.errors import DomainValidationError
from btc_sentinel.time_utils import format_casablanca, iso_utc, to_casablanca


class TimeTests(unittest.TestCase):
    def test_august_utc_is_displayed_in_casablanca(self) -> None:
        value = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
        local = to_casablanca(value)
        self.assertEqual(local.hour, 1)
        self.assertEqual(local.utcoffset().total_seconds(), 3600)
        self.assertIn("Africa/Casablanca", format_casablanca(value))

    def test_utc_serialization_is_stable(self) -> None:
        value = datetime(2026, 8, 2, 0, 0, 5, tzinfo=UTC)
        self.assertEqual(iso_utc(value), "2026-08-02T00:00:05Z")

    def test_naive_time_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            to_casablanca(datetime(2026, 8, 2, 0, 0))


if __name__ == "__main__":
    unittest.main()

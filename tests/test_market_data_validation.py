import unittest
from datetime import timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.validation import (
    coherent_reference_time,
    ensure_observation_fresh,
    ensure_price_coherence,
    ensure_series_usable,
)
from tests.market_data_fixtures import NOW, make_series


class MarketDataValidationTests(unittest.TestCase):
    def test_accepts_fresh_closed_series_with_enough_history(self) -> None:
        series = make_series(MarketInterval.ONE_MINUTE, count=3)
        ensure_series_usable(series, as_of=NOW, minimum_closed_candles=3)

    def test_rejects_short_or_stale_series(self) -> None:
        short = make_series(MarketInterval.ONE_MINUTE, count=2)
        with self.assertRaises(MarketDataValidationError):
            ensure_series_usable(short, as_of=NOW, minimum_closed_candles=3)

        stale = make_series(
            MarketInterval.ONE_MINUTE,
            MarketVenue.SPOT,
            count=3,
            as_of=NOW - timedelta(hours=1),
        )
        with self.assertRaises(MarketDataValidationError):
            ensure_series_usable(stale, as_of=NOW, minimum_closed_candles=3)

    def test_uses_the_earlier_coherent_exchange_clock(self) -> None:
        reference = coherent_reference_time(
            spot_server_time=NOW,
            futures_server_time=NOW + timedelta(seconds=2),
            local_time=NOW + timedelta(seconds=1),
        )
        self.assertEqual(reference, NOW)

    def test_rejects_exchange_or_runner_clock_skew(self) -> None:
        with self.assertRaises(MarketDataValidationError):
            coherent_reference_time(
                spot_server_time=NOW,
                futures_server_time=NOW + timedelta(seconds=6),
                local_time=NOW,
            )
        with self.assertRaises(MarketDataValidationError):
            coherent_reference_time(
                spot_server_time=NOW,
                futures_server_time=NOW,
                local_time=NOW + timedelta(minutes=2),
            )

    def test_rejects_severe_spot_futures_dislocation(self) -> None:
        ensure_price_coherence(Decimal("100"), Decimal("102.9"))
        with self.assertRaises(MarketDataValidationError):
            ensure_price_coherence(Decimal("100"), Decimal("103.1"))

    def test_rejects_stale_or_future_observations(self) -> None:
        ensure_observation_fresh(
            NOW - timedelta(minutes=4),
            as_of=NOW,
            maximum_age=timedelta(minutes=5),
            name="sample",
        )
        with self.assertRaises(MarketDataValidationError):
            ensure_observation_fresh(
                NOW - timedelta(minutes=6),
                as_of=NOW,
                maximum_age=timedelta(minutes=5),
                name="sample",
            )
        with self.assertRaises(MarketDataValidationError):
            ensure_observation_fresh(
                NOW + timedelta(seconds=6),
                as_of=NOW,
                maximum_age=timedelta(minutes=5),
                name="sample",
            )


if __name__ == "__main__":
    unittest.main()

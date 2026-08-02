import unittest
from datetime import timedelta
from decimal import Decimal

from btc_sentinel.market_data.collector import (
    MarketDataCollector,
    MarketDataPolicy,
    SeriesRequirement,
)
from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataTransportError
from btc_sentinel.market_data.models import (
    CollectionStatus,
    FundingRatePoint,
    FundingSnapshot,
    OpenInterestPoint,
    OrderBookLevel,
    OrderBookSnapshot,
    TakerVolumePoint,
)
from tests.market_data_fixtures import NOW, make_series

TEST_POLICY = MarketDataPolicy(
    requirements=(
        SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_MINUTE, 2, 3),
        SeriesRequirement(MarketVenue.FUTURES, MarketInterval.ONE_MINUTE, 2, 3),
    ),
    optional_history_limit=10,
    order_book_limit=5,
)


class StubBinanceClient:
    def __init__(self) -> None:
        self.spot_time = NOW
        self.futures_time = NOW + timedelta(seconds=1)
        self.spot = make_series(MarketInterval.ONE_MINUTE, count=2)
        self.futures = make_series(
            MarketInterval.ONE_MINUTE,
            MarketVenue.FUTURES,
            count=2,
            close="100.5",
        )
        self.mark_price = Decimal("100.5")
        self.failures: set[str] = set()

    def fail_if_requested(self, name: str) -> None:
        if name in self.failures:
            raise MarketDataTransportError(f"{name} unavailable")

    def spot_server_time(self):
        self.fail_if_requested("spot_server_time")
        return self.spot_time

    def futures_server_time(self):
        self.fail_if_requested("futures_server_time")
        return self.futures_time

    def spot_candles(self, interval, *, limit, as_of=None, start_at=None, end_at=None):
        self.fail_if_requested("spot_candles")
        return self.spot

    def futures_candles(self, interval, *, limit, as_of=None, start_at=None, end_at=None):
        self.fail_if_requested("futures_candles")
        return self.futures

    def funding_snapshot(self):
        self.fail_if_requested("funding_snapshot")
        return FundingSnapshot(
            mark_price=self.mark_price,
            index_price=Decimal("100"),
            last_funding_rate=Decimal("0.0001"),
            next_funding_time=NOW + timedelta(hours=4),
            observed_at=NOW,
        )

    def open_interest(self):
        self.fail_if_requested("open_interest")
        return OpenInterestPoint(Decimal("1000"), NOW)

    def funding_history(self, *, limit=30, start_at=None, end_at=None):
        self.fail_if_requested("funding_history")
        return (
            FundingRatePoint(
                Decimal("0.0001"),
                NOW - timedelta(hours=8),
                Decimal("100"),
            ),
        )

    def open_interest_history(
        self,
        period: DerivativesPeriod,
        *,
        limit=100,
        start_at=None,
        end_at=None,
    ):
        self.fail_if_requested("open_interest_history")
        return (
            OpenInterestPoint(
                Decimal("1000"),
                NOW - timedelta(minutes=5),
                Decimal("100000"),
            ),
        )

    def taker_volume(
        self,
        period: DerivativesPeriod,
        *,
        limit=100,
        start_at=None,
        end_at=None,
    ):
        self.fail_if_requested("taker_volume")
        return (
            TakerVolumePoint(
                period,
                Decimal("20"),
                Decimal("10"),
                Decimal("2"),
                NOW - timedelta(minutes=5),
            ),
        )

    def spot_order_book(self, *, observed_at, limit=100):
        self.fail_if_requested("spot_order_book")
        return OrderBookSnapshot(
            1,
            (OrderBookLevel(Decimal("99"), Decimal("6")),),
            (OrderBookLevel(Decimal("101"), Decimal("4")),),
            observed_at,
        )


class MarketDataCollectorTests(unittest.TestCase):
    def collect(self, client: StubBinanceClient):
        return MarketDataCollector(
            client,
            policy=TEST_POLICY,
            local_clock=lambda: NOW,
        ).collect()

    def test_accepts_a_complete_coherent_snapshot(self) -> None:
        result = self.collect(StubBinanceClient())

        self.assertIs(result.status, CollectionStatus.ACCEPTED)
        self.assertIsNotNone(result.snapshot)
        assert result.snapshot is not None
        self.assertEqual(result.snapshot.captured_at, NOW)
        self.assertEqual(result.snapshot.order_book.best_bid, Decimal("99"))
        self.assertEqual(result.issues, ())

    def test_degrades_when_an_optional_endpoint_fails(self) -> None:
        client = StubBinanceClient()
        client.failures.add("taker_volume")

        result = self.collect(client)

        self.assertIs(result.status, CollectionStatus.DEGRADED)
        self.assertIsNotNone(result.snapshot)
        assert result.snapshot is not None
        self.assertEqual(result.snapshot.taker_volume, ())
        self.assertEqual(result.issues[0].source, "taker_volume")
        self.assertFalse(result.issues[0].required)

    def test_rejects_when_a_required_endpoint_fails(self) -> None:
        client = StubBinanceClient()
        client.failures.add("futures_candles")

        result = self.collect(client)

        self.assertIs(result.status, CollectionStatus.REJECTED)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.issues[0].required)

    def test_rejects_stale_core_candles(self) -> None:
        client = StubBinanceClient()
        client.spot = make_series(
            MarketInterval.ONE_MINUTE,
            count=2,
            as_of=NOW - timedelta(hours=1),
        )

        result = self.collect(client)
        self.assertIs(result.status, CollectionStatus.REJECTED)
        self.assertIn("stale", result.issues[0].detail)

    def test_rejects_a_series_for_the_wrong_venue(self) -> None:
        client = StubBinanceClient()
        client.spot = make_series(
            MarketInterval.ONE_MINUTE,
            MarketVenue.FUTURES,
            count=2,
        )

        result = self.collect(client)

        self.assertIs(result.status, CollectionStatus.REJECTED)
        self.assertIn("wrong venue or interval", result.issues[0].detail)

    def test_rejects_clock_disagreement_or_price_dislocation(self) -> None:
        bad_clock = StubBinanceClient()
        bad_clock.futures_time = NOW + timedelta(seconds=10)
        self.assertIs(self.collect(bad_clock).status, CollectionStatus.REJECTED)

        dislocated = StubBinanceClient()
        dislocated.mark_price = Decimal("104")
        self.assertIs(self.collect(dislocated).status, CollectionStatus.REJECTED)

    def test_policy_requires_spot_one_minute_and_futures(self) -> None:
        with self.assertRaises(ValueError):
            MarketDataPolicy(
                requirements=(
                    SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_HOUR, 2, 3),
                    SeriesRequirement(MarketVenue.FUTURES, MarketInterval.ONE_HOUR, 2, 3),
                )
            )
        with self.assertRaises(ValueError):
            MarketDataPolicy(
                requirements=(
                    SeriesRequirement(MarketVenue.SPOT, MarketInterval.ONE_MINUTE, 2, 3),
                )
            )


if __name__ == "__main__":
    unittest.main()

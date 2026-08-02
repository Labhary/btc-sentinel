import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.models import (
    CandleSeries,
    CollectionResult,
    CollectionStatus,
    DataIssue,
    OrderBookLevel,
    OrderBookSnapshot,
    TakerVolumePoint,
)
from tests.market_data_fixtures import NOW, current_open, make_candle, make_series


class CandleModelTests(unittest.TestCase):
    def test_accepts_a_contiguous_closed_btc_series(self) -> None:
        series = make_series(MarketInterval.FIFTEEN_MINUTES, count=3)

        self.assertEqual(len(series.candles), 3)
        self.assertEqual(series.symbol, "BTCUSDT")
        self.assertIs(series.venue, MarketVenue.SPOT)
        self.assertTrue(all(candle.is_closed_at(NOW) for candle in series.candles))

    def test_rejects_binary_float_prices_and_non_btc_symbols(self) -> None:
        candle = make_series(MarketInterval.ONE_MINUTE).latest
        with self.assertRaises(MarketDataValidationError):
            replace(candle, open=100.0)
        with self.assertRaises(MarketDataValidationError):
            replace(candle, symbol="ETHUSDT")

    def test_rejects_contradictory_ohlc_or_volume(self) -> None:
        candle = make_series(MarketInterval.ONE_MINUTE).latest
        with self.assertRaises(MarketDataValidationError):
            replace(candle, high=Decimal("99.5"))
        with self.assertRaises(MarketDataValidationError):
            replace(candle, taker_buy_base_volume=Decimal("11"))

    def test_rejects_misaligned_or_wrong_duration_candles(self) -> None:
        candle = make_series(MarketInterval.FIFTEEN_MINUTES).latest
        with self.assertRaises(MarketDataValidationError):
            replace(candle, open_time=candle.open_time + timedelta(minutes=1))
        with self.assertRaises(MarketDataValidationError):
            replace(candle, close_time=candle.close_time + timedelta(milliseconds=1))

    def test_rejects_duplicate_and_missing_candles(self) -> None:
        series = make_series(MarketInterval.ONE_MINUTE, count=3)
        with self.assertRaises(MarketDataValidationError):
            CandleSeries((series.candles[0], series.candles[0]))
        with self.assertRaises(MarketDataValidationError):
            CandleSeries((series.candles[0], series.candles[2]))

    def test_filters_the_current_open_candle(self) -> None:
        closed = make_series(MarketInterval.ONE_MINUTE, count=2)
        open_candle = make_candle(
            current_open(MarketInterval.ONE_MINUTE),
            MarketInterval.ONE_MINUTE,
        )
        mixed = CandleSeries((*closed.candles, open_candle))

        filtered = mixed.closed_at(NOW)
        self.assertEqual(filtered.candles, closed.candles)

    def test_monthly_boundaries_are_calendar_aware(self) -> None:
        series = make_series(MarketInterval.ONE_MONTH, count=3)
        self.assertEqual(series.candles[-1].open_time.month, 7)
        self.assertEqual(series.candles[-1].close_time.month, 7)
        self.assertEqual(series.candles[-1].close_time.day, 31)


class DerivativesAndBookModelTests(unittest.TestCase):
    def test_rejects_a_taker_ratio_that_contradicts_volumes(self) -> None:
        with self.assertRaises(MarketDataValidationError):
            TakerVolumePoint(
                period=DerivativesPeriod.FIVE_MINUTES,
                buy_volume=Decimal("20"),
                sell_volume=Decimal("10"),
                buy_sell_ratio=Decimal("1"),
                period_start=NOW,
            )

    def test_calculates_order_book_spread_and_imbalance(self) -> None:
        book = OrderBookSnapshot(
            last_update_id=7,
            bids=(OrderBookLevel(Decimal("99"), Decimal("6")),),
            asks=(OrderBookLevel(Decimal("101"), Decimal("4")),),
            observed_at=NOW,
        )

        self.assertEqual(book.mid_price, Decimal("100"))
        self.assertEqual(book.spread_basis_points, Decimal("200"))
        self.assertEqual(book.quantity_imbalance, Decimal("0.2"))

    def test_rejects_a_crossed_or_unsorted_order_book(self) -> None:
        with self.assertRaises(MarketDataValidationError):
            OrderBookSnapshot(
                last_update_id=7,
                bids=(OrderBookLevel(Decimal("101"), Decimal("1")),),
                asks=(OrderBookLevel(Decimal("100"), Decimal("1")),),
                observed_at=NOW,
            )


class CollectionResultTests(unittest.TestCase):
    def test_rejected_result_requires_a_required_issue(self) -> None:
        issue = DataIssue("STALE", "spot", "data is stale", required=True)
        result = CollectionResult(CollectionStatus.REJECTED, None, (issue,))
        self.assertIs(result.status, CollectionStatus.REJECTED)

        with self.assertRaises(MarketDataValidationError):
            CollectionResult(CollectionStatus.REJECTED, None, ())


if __name__ == "__main__":
    unittest.main()

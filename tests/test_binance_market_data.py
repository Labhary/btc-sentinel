import unittest
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from btc_sentinel.market_data.binance import BinancePublicClient
from btc_sentinel.market_data.enums import DerivativesPeriod, MarketInterval, MarketVenue
from btc_sentinel.market_data.errors import MarketDataValidationError
from btc_sentinel.market_data.transport import FUTURES_ORIGIN, SPOT_ORIGIN
from tests.market_data_fixtures import (
    NOW,
    candle_row,
    current_open,
    make_candle,
    make_series,
    milliseconds,
)


class StubJsonTransport:
    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str | int]]] = []

    def get_json(
        self,
        origin: str,
        path: str,
        params: dict[str, str | int],
    ) -> object:
        self.calls.append((origin, path, dict(params)))
        key = (origin, path)
        if key not in self.responses:
            raise AssertionError(f"No response configured for {key}")
        return deepcopy(self.responses[key])


class BinanceCandleClientTests(unittest.TestCase):
    def test_reads_server_times_as_utc(self) -> None:
        transport = StubJsonTransport(
            {
                (SPOT_ORIGIN, "/api/v3/time"): {"serverTime": milliseconds(NOW)},
                (FUTURES_ORIGIN, "/fapi/v1/time"): {
                    "serverTime": milliseconds(NOW + timedelta(seconds=1))
                },
            }
        )
        client = BinancePublicClient(transport)

        self.assertEqual(client.spot_server_time(), NOW)
        self.assertEqual(client.futures_server_time(), NOW + timedelta(seconds=1))

    def test_parses_spot_candles_and_drops_the_open_candle(self) -> None:
        closed = make_series(MarketInterval.ONE_MINUTE, count=2)
        current = make_candle(current_open(MarketInterval.ONE_MINUTE), MarketInterval.ONE_MINUTE)
        transport = StubJsonTransport(
            {
                (SPOT_ORIGIN, "/api/v3/klines"): [
                    *(candle_row(c) for c in closed.candles),
                    candle_row(current),
                ]
            }
        )
        client = BinancePublicClient(transport)

        result = client.spot_candles(MarketInterval.ONE_MINUTE, limit=3, as_of=NOW)

        self.assertEqual(result.candles, closed.candles)
        self.assertEqual(
            transport.calls[0],
            (
                SPOT_ORIGIN,
                "/api/v3/klines",
                {"symbol": "BTCUSDT", "interval": "1m", "limit": 3},
            ),
        )

    def test_parses_futures_candles_without_credentials(self) -> None:
        futures = make_series(
            MarketInterval.FIFTEEN_MINUTES,
            MarketVenue.FUTURES,
            count=2,
        )
        transport = StubJsonTransport(
            {(FUTURES_ORIGIN, "/fapi/v1/klines"): [candle_row(c) for c in futures.candles]}
        )
        client = BinancePublicClient(transport)

        result = client.futures_candles(
            MarketInterval.FIFTEEN_MINUTES,
            limit=2,
            as_of=NOW,
        )

        self.assertEqual(result.venue, MarketVenue.FUTURES)
        self.assertNotIn("apiKey", transport.calls[0][2])
        self.assertNotIn("signature", transport.calls[0][2])

    def test_rejects_malformed_or_gapped_candle_rows(self) -> None:
        malformed = BinancePublicClient(
            StubJsonTransport({(SPOT_ORIGIN, "/api/v3/klines"): [[1, "2"]]})
        )
        with self.assertRaises(MarketDataValidationError):
            malformed.spot_candles(MarketInterval.ONE_MINUTE, limit=1, as_of=NOW)

        series = make_series(MarketInterval.ONE_MINUTE, count=3)
        gapped = BinancePublicClient(
            StubJsonTransport(
                {
                    (SPOT_ORIGIN, "/api/v3/klines"): [
                        candle_row(series.candles[0]),
                        candle_row(series.candles[2]),
                    ]
                }
            )
        )
        with self.assertRaises(MarketDataValidationError):
            gapped.spot_candles(MarketInterval.ONE_MINUTE, limit=2, as_of=NOW)

    def test_adds_bounded_utc_time_range_parameters(self) -> None:
        candle = make_series(MarketInterval.ONE_HOUR, count=1).latest
        transport = StubJsonTransport(
            {(SPOT_ORIGIN, "/api/v3/klines"): [candle_row(candle)]}
        )
        client = BinancePublicClient(transport)
        start = NOW - timedelta(hours=2)
        end = NOW - timedelta(hours=1)

        client.spot_candles(
            MarketInterval.ONE_HOUR,
            limit=10,
            as_of=NOW,
            start_at=start,
            end_at=end,
        )

        self.assertEqual(transport.calls[0][2]["startTime"], milliseconds(start))
        self.assertEqual(transport.calls[0][2]["endTime"], milliseconds(end))
        with self.assertRaises(MarketDataValidationError):
            client.spot_candles(
                MarketInterval.ONE_HOUR,
                limit=10,
                as_of=NOW,
                start_at=end,
                end_at=start,
            )


class BinanceDerivativesClientTests(unittest.TestCase):
    def test_parses_mark_funding_and_current_open_interest(self) -> None:
        transport = StubJsonTransport(
            {
                (FUTURES_ORIGIN, "/fapi/v1/premiumIndex"): {
                    "symbol": "BTCUSDT",
                    "markPrice": "100.2",
                    "indexPrice": "100",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": milliseconds(NOW + timedelta(hours=4)),
                    "time": milliseconds(NOW),
                },
                (FUTURES_ORIGIN, "/fapi/v1/openInterest"): {
                    "symbol": "BTCUSDT",
                    "openInterest": "12345.67",
                    "time": milliseconds(NOW),
                },
            }
        )
        client = BinancePublicClient(transport)

        funding = client.funding_snapshot()
        interest = client.open_interest()

        self.assertEqual(funding.mark_price, Decimal("100.2"))
        self.assertEqual(funding.last_funding_rate, Decimal("0.0001"))
        self.assertEqual(interest.open_interest, Decimal("12345.67"))

    def test_parses_ordered_derivatives_histories(self) -> None:
        first = NOW - timedelta(minutes=10)
        second = NOW - timedelta(minutes=5)
        transport = StubJsonTransport(
            {
                (FUTURES_ORIGIN, "/fapi/v1/fundingRate"): [
                    {
                        "symbol": "BTCUSDT",
                        "fundingRate": "0.0001",
                        "fundingTime": milliseconds(NOW - timedelta(hours=8)),
                        "markPrice": "100",
                    }
                ],
                (FUTURES_ORIGIN, "/futures/data/openInterestHist"): [
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterest": "1000",
                        "sumOpenInterestValue": "100000",
                        "timestamp": milliseconds(first),
                    },
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterest": "1100",
                        "sumOpenInterestValue": "110000",
                        "timestamp": milliseconds(second),
                    },
                ],
                (FUTURES_ORIGIN, "/futures/data/takerlongshortRatio"): [
                    {
                        "buySellRatio": "2",
                        "buyVol": "20",
                        "sellVol": "10",
                        "timestamp": milliseconds(second),
                    }
                ],
            }
        )
        client = BinancePublicClient(transport)

        funding = client.funding_history(limit=1)
        interest = client.open_interest_history(DerivativesPeriod.FIVE_MINUTES, limit=2)
        taker = client.taker_volume(DerivativesPeriod.FIVE_MINUTES, limit=1)

        self.assertEqual(funding[0].funding_rate, Decimal("0.0001"))
        self.assertEqual(interest[-1].open_interest_value, Decimal("110000"))
        self.assertEqual(taker[0].buy_sell_ratio, Decimal("2"))

    def test_rejects_duplicate_history_timestamps(self) -> None:
        row = {
            "symbol": "BTCUSDT",
            "sumOpenInterest": "1000",
            "sumOpenInterestValue": "100000",
            "timestamp": milliseconds(NOW - timedelta(minutes=5)),
        }
        client = BinancePublicClient(
            StubJsonTransport(
                {(FUTURES_ORIGIN, "/futures/data/openInterestHist"): [row, row]}
            )
        )
        with self.assertRaises(MarketDataValidationError):
            client.open_interest_history(DerivativesPeriod.FIVE_MINUTES, limit=2)

    def test_parses_a_low_weight_spot_order_book(self) -> None:
        client = BinancePublicClient(
            StubJsonTransport(
                {
                    (SPOT_ORIGIN, "/api/v3/depth"): {
                        "lastUpdateId": 123,
                        "bids": [["99", "6"], ["98", "3"]],
                        "asks": [["101", "4"], ["102", "2"]],
                    }
                }
            )
        )

        book = client.spot_order_book(observed_at=NOW, limit=100)

        self.assertEqual(book.best_bid, Decimal("99"))
        self.assertEqual(book.best_ask, Decimal("101"))


if __name__ == "__main__":
    unittest.main()

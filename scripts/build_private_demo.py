"""Generate scripted QA results with the real lifecycle simulators; no network."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_sentinel.backtesting import (
    HistoricalSignalCase,
    simulate_fixed_case,
    simulate_managed_case,
)
from btc_sentinel.domain.enums import Bias, MarketRegime, Side
from btc_sentinel.domain.models import Signal, SignalTerms, Target, TimeframeBiases
from btc_sentinel.market_data.enums import MarketInterval, MarketVenue
from btc_sentinel.market_data.models import Candle, CandleSeries


def build_demo() -> dict:
    records = []
    for index in range(24):
        start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
        terms = SignalTerms(
            side=Side.LONG,
            entry_low=Decimal("100"),
            entry_high=Decimal("101"),
            original_stop=Decimal("95"),
            targets=(Target(1, Decimal("114")), Target(2, Decimal("120"))),
            created_at=start,
            data_timestamp=start - timedelta(seconds=1),
            expires_at=start + timedelta(hours=4),
            invalidation_condition="Scripted QA scenario only",
            expiration_condition="Four-hour expiry",
            recommended_risk_percent=Decimal("0.25"),
        )
        signal = Signal(
            signal_id=f"BTC-{start:%Y%m%d}-001",
            terms=terms,
            setup_score=88,
            regime=MarketRegime.BULLISH_TREND,
            biases=TimeframeBiases(*([Bias.BULLISH] * 6)),
            reasons=("Synthetic QA fixture, not a market recommendation",),
            risks=("Artificial prices and predetermined scenarios",),
            strategy_version="demo-fixture-v1",
        )
        prices = [(102, 103, 100, 102)]
        prices += [(110, 115, 109, 114)] if index % 2 == 0 else [(97, 98, 94, 95)]
        candles = []
        for minute, values in enumerate(prices):
            opened = start + timedelta(minutes=minute)
            candles.append(
                Candle(
                    venue=MarketVenue.SPOT,
                    interval=MarketInterval.ONE_MINUTE,
                    open_time=opened,
                    close_time=MarketInterval.ONE_MINUTE.expected_close_time(opened),
                    open=Decimal(values[0]),
                    high=Decimal(values[1]),
                    low=Decimal(values[2]),
                    close=Decimal(values[3]),
                    volume=Decimal("10"),
                    quote_volume=Decimal("1000"),
                    trade_count=10,
                    taker_buy_base_volume=Decimal("5"),
                    taker_buy_quote_volume=Decimal("500"),
                )
            )
        case = HistoricalSignalCase(
            signal, CandleSeries(tuple(candles)), start + timedelta(minutes=2)
        )
        fixed, managed = simulate_fixed_case(case), simulate_managed_case(case)
        records.append(
            {
                "id": signal.signal_id,
                "fixed": fixed.outcome.value,
                "managed": managed.outcome.value,
                "fixed_r": str(fixed.result_r),
                "managed_r": str(managed.result_r),
                "planned_rr": str(fixed.planned_rr),
            }
        )
    return {
        "mode": "SCRIPTED_DEMO",
        "performance_evidence": False,
        "closed_trades": len(records),
        "records": records,
    }


if __name__ == "__main__":
    print(json.dumps(build_demo(), indent=2))

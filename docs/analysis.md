# Multi-timeframe analysis policy

Phase 4 converts one validated Phase 3 snapshot into transparent analysis
context. It does not create a trade, entry, stop, target, or Telegram alert.
Phase 6 consumes this context under the separate [signal policy](signals.md).

## Completed-candle hierarchy

The required spot timeframes are monthly, weekly, daily, four-hour, one-hour,
and 15-minute, in that order. Monthly, weekly, and daily define the directional
bias. Daily and four-hour describe operational structure. One-hour and
15-minute provide execution context for the later signal phase.

Every latest candle must be closed at the snapshot time. A missing timeframe,
an incomplete candle, or fewer than 50 completed candles rejects the analysis.

## Indicators and structure

The engine uses Decimal arithmetic and calculates EMA 20/50/100/200, RSI 14,
MACD 12/26/9, ADX 14, ATR 14, 20-period Bollinger Bands, 20-period rolling VWAP,
and current-to-average volume. EMA 100 or 200 is absent when the exchange does
not have enough history. In particular, BTC cannot honestly supply 200 monthly
candles; monthly analysis uses the longest available EMA rather than inventing
history.

Swing highs and lows use a fixed two-candle radius. The latest confirmed swings
produce higher-high/higher-low or lower-high/lower-low structure. A completed
close beyond the latest confirmed swing marks break context. Zones group the
last 12 confirmed swings within an ATR-aware distance and expose at most the
three nearest support and resistance areas.

## Regimes and volatility

Each timeframe is classified as bullish trend, bearish trend, range,
transition, abnormally volatile, or no reliable regime. Trend classification
requires indicator/structure agreement and ADX of at least 23. A range requires
ADX below 18 and Bollinger width below 12 percent. Other disagreement becomes
transition or no reliable regime.

Abnormal volatility is relative, not a fixed BTC-dollar threshold: normalized
ATR must exceed 2.5 times its recent median. This blocks later signal creation.

## Evidence score

The 0–100 setup-quality score measures agreement, never probability:

| Evidence group | Weight |
|---|---:|
| Higher-timeframe bias | 40 |
| Operational structure | 25 |
| Execution confirmation | 20 |
| Optional derivatives context | 10 |
| Volatility quality | 5 |

Only a group aligned with the higher-timeframe bias contributes its directional
weight. Trend indicators within one timeframe are deliberately combined before
group scoring, so EMA, MACD, RSI, and structure cannot each masquerade as an
independent vote. The score is normalized across weights that are actually
available, so an unavailable optional derivatives group does not silently turn
80/100 into an effective 88.9% agreement requirement. Missing optional
derivatives context still degrades the result and later reduces suggested paper
risk; available neutral or conflicting derivatives evidence remains in the
denominator and therefore lowers the score.
Major-timeframe conflict, abnormal volatility, or unreliable higher-timeframe
bias creates an explicit no-trade reason and caps quality below 60.

# Paper-signal lifecycle policy

Phase 7 turns a persisted Phase 6 signal into a reproducible paper outcome. It
uses completed Binance Spot BTCUSDT one-minute candles only. It does not place
orders, send signals, manage a live position, or run on a production schedule.

## Replay contract

- Replay begins at the first whole minute that cannot contain time before the
  signal was created.
- Every later candle must be contiguous with the durable checkpoint. A gap
  fails closed instead of silently skipping unknown price action.
- Each processed candle advances a signal-specific monotonic checkpoint.
- Event deduplication keys include the signal, candle open time, and action.
- The durable activation timestamp preserves activation-candle rules across a
  crash that occurs before the checkpoint is written.

## Conservative event ordering

| Observed completed candle | Recorded result |
|---|---|
| Entry zone touched before expiry | Activate at the adverse zone edge |
| Candle overlaps the exact expiry instant | Expire before crediting an entry |
| Stop touched | Close each active track at the stop |
| Active trade opens beyond its stop | Close at the worse candle open |
| TP1 and stop both touched | Stop first; record a loss and ambiguity flag |
| TP1 first appears on activation candle | Do not credit it; wait for a later candle |
| TP1 appears on a later candle | Close each active baseline track at TP1 |

For a long, the adverse entry is the upper edge of the entry zone; for a short,
it is the lower edge. A gap-stop exception does not apply on the activation
candle because the trade did not exist at that candle's open.

Phase 7 treats TP1 as the terminal baseline target. Additional planned targets
remain immutable reference terms. Phase 8 applies its separate
[managed-position policy](position-management.md) only after a lifecycle candle
has been resolved. Fixed and managed tracks start with identical terms, so
management can be compared against an untouched baseline without hindsight.

## Result accounting

Outcome R includes the signal's modeled round-trip cost:

`net R = (directional exit P&L - modeled cost) / (initial price risk + modeled cost)`

This policy deliberately biases uncertain observations against the strategy.
It prevents one-minute candle ambiguity, process restarts, and favorable gap
assumptions from inflating the reported win rate.

The requested goal of more than 60% wins with at least 1:2 risk/reward is an
evaluation target, not a guarantee. Phase 11 must test it out of sample with
costs, walk-forward splits, regime coverage, and sensitivity checks; Phase 13
must then confirm behavior through paper observation before any real-money use
is considered.

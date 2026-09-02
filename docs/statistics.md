# Strict paper-trading statistics

Phase 9 recalculates a complete append-only statistics snapshot whenever either
the fixed or managed virtual track closes. Calculation and snapshot insertion
are part of the same transaction as the outcome: a contradictory result or a
snapshot failure rolls back the close instead of leaving statistics stale.

## Outcome integrity

The four durable categories are not interchangeable:

| Category | Required R sign | Counts as a win |
|---|---:|---:|
| `WIN` | Positive | Yes |
| `LOSS` | Negative | No |
| `BREAK_EVEN` | Exactly zero | No |
| `EARLY_EXIT` | Positive, negative, or zero | No |

An early exit can make money, so return-by-sign counts it as positive. It still
is not a target win. This prevents break-even protection or selective early
exits from cosmetically increasing the strategy's win rate.

## Rates

- **Strict win rate:** `WIN / every resolved outcome`.
- **Decisive win rate:** `WIN / (WIN + LOSS)`. This excludes break-even and
  early-exit outcomes and is always labeled as such.
- **Positive-return rate:** `result_r > 0 / every resolved outcome`.
- **95% interval:** Wilson score interval around strict win rate.

An empty denominator is stored as `null`, never zero. A displayed 60% strict win
rate is only the observed sample rate. Its interval can remain extremely wide
when few trades exist, so it is not evidence that the true rate exceeds 60%.

## R-based performance

Each variant includes resolved count, net R, average R, median R, profit factor,
and maximum chronological drawdown in R. Profit factor is `null` when there is
no negative R rather than being reported as infinity. These are paper-model
results that already inherit the lifecycle cost and gap assumptions; they are
not account equity, currency profit, or proof of executable fills.

Fixed and managed statistics are independent. Completed pairs report which
variant did better and the average `managed R - fixed R`. If one track remains
open, the snapshot reports it as unresolved and excludes it from paired deltas.
Strategy-version outcome counts remain visible so later rule versions cannot be
silently blended without disclosure.

Phase 9 is descriptive, not a backtest. Phase 11 must add out-of-sample splits,
regime coverage, sensitivity tests, and minimum sample requirements before any
claim about the requested 60%+ win rate at 1:2 risk/reward is credible.

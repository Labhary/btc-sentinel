# Managed paper-position policy

Phase 8 is a versioned experiment applied only to the `MANAGED` virtual track.
The `FIXED` track always keeps the original Phase 6 entry, stop, and TP1 so later
statistics can show whether management improved or damaged expectancy.

## Ordering and evidence

For each completed Spot BTCUSDT one-minute candle:

1. Phase 7 resolves the entry, the currently effective stop, and TP1.
2. If the managed track remains active, Phase 8 evaluates the candle close.
3. The immutable decision and its evidence are stored.
4. A changed stop or fraction becomes effective from the next candle only.
5. The management checkpoint advances after the durable decision.

This order prohibits using a candle's high, low, or close to change an exit that
would be credited inside that same candle. Decision keys contain the signal,
candle, and management strategy version. If a process stops after committing a
decision but before advancing its checkpoint, replay recognizes the same key
and advances without deciding again.

## Default policy: `management-v0.8.0`

| Condition at completed candle close | Decision |
|---|---|
| Activation candle | `HOLD` |
| Managed open profit below 1.5R | `HOLD` |
| At least 1.5R and stop is still original | Move to cost-adjusted break-even |
| Stop already protected | `HOLD` |

Cost-adjusted break-even includes the signal's modeled round-trip cost. A long
stop is above the fill by that cost; a short stop is below it. If reached on a
later candle with no partial profit, the managed result is exactly 0R and is
classified `BREAK_EVEN`, not `WIN`.

The default policy does not trail, exit early, or take partial profit. Those
actions are not automatically better. In particular, closing half before a
2.25R TP1 lowers the total R of a fully successful managed trade below the
original target. The engine supports audited partial accounting only behind an
explicit non-default policy value so Phase 11 can test the trade-off without
rewriting historical results.

## Partial accounting

When an explicitly versioned policy enables a partial, the closed fraction's R
is added to `realized_r` and the open fraction is reduced. Final managed R is:

`realized partial R + remaining fraction × full-position R at final exit`

The fixed result continues independently even if the managed track closes.
Break-even results remain separate from wins when Phase 9 calculates statistics.

No current rule proves a 60% win rate. Stop protection can reduce losses while
also converting future winners into break-even exits. Only out-of-sample
fixed-versus-managed evidence can determine whether the net effect is positive.

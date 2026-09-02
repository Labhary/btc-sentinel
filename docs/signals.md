# Deterministic signal policy

Phase 6 converts a validated snapshot into either one immutable pending paper
setup or an auditable `NO_SIGNAL`. It does not persist, activate, send, or trade
the setup. Phase 7 defines the separate durable [lifecycle replay](lifecycle.md)
policy; automated orchestration and delivery remain disabled.

## Admission gates

The engine recomputes Phase 4 analysis from the supplied Phase 3 snapshot. It
does not accept a score or direction supplied by a caller. A setup is rejected
when any of these conditions is true:

- the required monthly, weekly, daily, four-hour, one-hour, and 15-minute
  hierarchy is incomplete or unordered;
- analysis is rejected, has a no-trade reason, or scores below 80/100;
- the overall regime is not a bullish or bearish trend aligned with the
  higher-timeframe direction;
- one-hour or 15-minute execution direction disagrees with that direction;
- the market snapshot is older than five minutes or the news assessment is
  older than 15 minutes;
- Phase 5 returns `BLOCK`;
- another managed BTC signal is active;
- fewer than four hours have elapsed since the previous signal;
- no suitable 15-minute structure zone exists, or the zone is farther than
  2.5 ATR from current price; or
- one-hour support/resistance obstructs the first target.

Optional derivatives degradation and Phase 5 `CAUTION` do not fabricate a
hard failure. They reduce suggested paper risk from 0.50% to 0.25% and remain
visible in the record. News never selects `LONG` or `SHORT` and never changes
entry, stop, or target prices.

## Price construction

For a long setup, the nearest 15-minute support zone is the entry range. The
conservative assumed fill is its upper edge; the stop is half an ATR below its
lower edge. A short setup mirrors this at the nearest resistance zone, using
the lower edge as the conservative fill and a stop half an ATR above the zone.

Prices are represented with `Decimal` and conservatively rounded to two decimal
places for paper records. The cost model reserves 0.15% of the conservative
entry for round-trip fees and slippage. TP1 and TP2 are solved from total risk
so their net planned returns are at least 2.25R and 3.25R after that cost. This
is a deterministic modeling assumption, not a claim about future execution.

Every admitted setup expires after four hours if its entry zone is not touched.
The immutable record contains its original entry range, stop, targets,
invalidation text, expiration text, evidence score, timeframe biases, risks,
and strategy version `rules-v0.6.0`.

## Honest limitations

The 80-point threshold, ATR buffers, expiry, cooldown, cost rate, and risk
figures are conservative versioned starting rules. They are not optimized proof
of profitability. In particular, the setup-quality score is not a win
probability, and the project does not claim a 70% win rate. Phase 11 must test
these rules with costs, walk-forward evaluation, regime coverage, and
out-of-sample evidence before any performance claim is considered.

Phase 6 creates only an in-memory `PENDING` object. No GitHub schedule,
Cloudflare deployment, Telegram send, exchange order endpoint, private Binance
credential, or real-money execution is enabled.

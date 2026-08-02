# Requirements review and corrections

The product goal is sound, but several original assumptions needed correction
before implementation.

## 1. “Live” cannot mean tick-perfect on free scheduled runners

The usable free design is candle-based paper monitoring. GitHub Actions can be
late or skip a scheduled run. The engine therefore replays one-minute candles
from a durable checkpoint. It can account for what happened; it cannot promise
an immediate intrabar notification.

## 2. A high setup score is not a win probability

The score measures agreement among independent evidence groups. Correlated
indicators are grouped so EMA, MACD, and trend structure cannot each receive
full independent weight for measuring the same momentum. No message will map a
score such as 87/100 to an 87% chance of winning.

## 3. The requested “states” mixed three different concepts

`PENDING`, `ACTIVE`, `EXPIRED`, `CANCELLED`, and `CLOSED` are lifecycle states.
`TP1_HIT`, `STOP_LOSS_HIT`, and `EARLY_EXIT` are immutable events or close
reasons. `FIXED` and `MANAGED` are parallel virtual tracks.

Keeping all of them in one mutable status would lose information—for example,
a trade cannot simultaneously remain active after TP1 and have `TP1_HIT` as its
only state. The normalized model preserves every required term without that
contradiction.

## 4. Fixed and managed outcomes do not always finish together

If the managed path exits early, the unchanged fixed path must keep running
virtually until its original rules resolve. The managed trade can close and
allow a new setup while the fixed comparison continues in the background.
Statistics clearly label unresolved fixed tracks instead of inventing a result.

## 5. Telegram cannot guarantee exactly-once delivery

No local database transaction can atomically include Telegram's external
`sendMessage` call. The design uses stable deduplication keys, an outbox lease,
the returned Telegram message ID, and an `UNKNOWN` state for the narrow crash
window. Claiming perfect exactly-once messaging would be technically false.

## 6. Free liquidation history is not a dependable core input

Binance exposes real-time liquidation streams, but a five-minute scheduled job
does not continuously capture them, and dependable long historical liquidation
data is not available from the same public interface. Liquidations are an
optional live context input only when observed reliably. Their absence cannot
be silently filled or optimized in backtests.

## 7. Order-book imbalance is noisy and not historical truth

A periodic depth snapshot is easy to manipulate and cannot reconstruct all
orders between jobs. It may be a small confirmation or risk flag, never a
standalone trigger. Version 1 must work safely when it is unavailable.

## 8. News coverage cannot be both complete and free

Official feeds and GDELT provide useful coverage but not a guaranteed complete,
low-latency global news terminal. News primarily blocks or delays trades. One
headline never creates a position. Rumors remain `UNCONFIRMED` and carry no
directional authority.

## 9. Dynamic management is an experiment, not an automatic improvement

The default is the original stop and targets. Break-even, partial exits, and
early exits are versioned decisions made from data available at that moment.
They are compared against the fixed path out of sample. If management reduces
expectancy, it will be simplified or disabled.

## 10. “Completely free forever” cannot be guaranteed

The selected services have free tiers today, but providers can change limits or
terms. The bot will expose usage health and fail closed before a quota failure
can corrupt its record. No code can guarantee that a third party remains free
forever.

## 11. One active trade means one managed BTC trade

Only one managed BTC/USDT trade may be `ACTIVE`. A fixed comparison track from a
previous managed early exit may continue virtually; it does not block the next
real paper setup.

## 12. Backtest results will not include unavailable future knowledge

Live-only features are either excluded from the historical rule or marked
missing. They are never reconstructed from current data or assigned a favorable
value. Strategy versions, fees, slippage, fill policy, and source coverage are
stored with every run.


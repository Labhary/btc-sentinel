# Read-only paper reports

Phase 10 turns durable paper state into bounded, deterministic Telegram-ready
text. It does not send messages, enqueue outbox rows, enable a schedule, or
activate deployment. A future production job must explicitly provide the chat
identity and perform delivery.

## Report types

- Daily, weekly, and monthly reports summarize outcomes closed inside a
  Casablanca calendar window. Stored boundaries and queries remain UTC.
- Active reports show fills, original targets, the current managed stop, and
  which fixed or managed tracks remain open.
- Pending reports show the immutable entry zone, stop, targets, score, and local
  expiry time.
- News-risk reports accept the current Phase 5 assessment. Missing,
  future-dated, or more-than-30-minute-old assessments are `UNAVAILABLE` or
  `STALE`, never `CLEAR`.

## Statistical honesty

Period reports preserve the Phase 9 definitions. `WIN / all resolved outcomes`
is the strict win rate; break-even and early-exit outcomes do not become wins.
Every observed rate is displayed with its sample size and 95% Wilson interval.
With one win, for example, the observed rate is 100% but the interval is very
wide. Empty samples display `unavailable`, not a fabricated 0% or 100%.

Fixed and managed outcomes remain separate. Completed pairs, unresolved tracks,
net R, average R, and maximum drawdown in R are labeled explicitly. These are
paper-model observations, not a forecast, fill guarantee, or proof that the
requested 60%+ win rate can be sustained.

## Safety and determinism

All text is plain, control characters are removed, untrusted titles are bounded,
and every prepared payload stays within Telegram's 4096-character text limit.
The payload deliberately contains no chat ID. Stable report keys include the
report type, UTC window or generation time, and report policy version.

Generating a report is read-only: it does not change signals, outcomes,
statistics, checkpoints, or the outbox. Production scheduling and delivery stay
disabled until the deployment phase.

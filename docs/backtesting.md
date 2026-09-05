# Backtesting policy

Phase 11 supplies a deterministic backtesting framework. It does **not** ship a
representative historical dataset, and no real performance verdict has been
earned yet. The current >60% strict win-rate objective at 2R or better is
therefore **unproven**, not passed.

## Dataset contract

A performance run must declare an immutable dataset identifier, UTC coverage
range, strategy version, included sources, excluded features, and fill-policy
version. It must include completed Binance Spot BTCUSDT one-minute candles and
must exhaustively evaluate every candidate decision time in the declared
period. Hand-picked signals are rejected by policy.

Inputs must be information that was available at each historical decision
time. Features without reliable point-in-time history, such as snapshots of
the current order book or liquidation feed, must be excluded and named in the
run metadata; they must never be reconstructed from later information.

The repository tests use synthetic cases only to prove mechanics and failure
handling. Synthetic test results are not evidence of profitability.

## Historical archive preflight

Version `0.12.4` adds a streaming validator for Binance Vision Spot
`BTCUSDT` one-minute ZIP archives. It verifies an immutable JSON manifest,
SHA-256 for every archive, one expected CSV member per ZIP, exact 12-field
klines, decimal values, row counts, UTC coverage, and minute-by-minute
continuity. ZIP traversal, encryption, unsafe compression ratios, oversized
inputs, duplicate JSON fields, unknown manifest fields, and checksum changes
are rejected.

Run the preflight without loading the multi-year dataset into memory:

```bash
btc-sentinel-validate-history path/to/manifest.json
```

The manifest records `schema_version`, `dataset_id`, fixed Spot `BTCUSDT`/`1m`
identity, exclusive UTC coverage, `https://data.binance.vision` as the source
origin, `exhaustive_candidate_scan: true`, excluded features, and ordered
archives. Each archive records its safe relative path, a fixed Binance Vision
Spot URL with the same filename, lowercase SHA-256, timestamp unit, exclusive
UTC coverage, and exact row count. Timestamp units are explicit because
Binance documents microsecond Spot timestamps from 2025-01-01 onward; the
validator never guesses the unit.

A successful preflight reports `performance_verdict: NOT_RUN`. Data integrity
is not strategy evidence.

Version `0.12.9` can create this market-data input directly from the fixed
official monthly archive path. The end month is exclusive, the output directory
must not already exist, and the command downloads no private or authenticated
data:

```bash
btc-sentinel-fetch-history \
  2022-01 \
  2026-01 \
  ./btc-history-2022-2025 \
  --dataset-id binance-vision-btcusdt-2022-2025-v1
```

Downloads stream to partial files under a byte limit, reject redirects, and use
bounded retries. The manifest is published only after the complete downloaded
range passes the same checksum, ZIP, row, timestamp, and continuity validator.
Interrupted or invalid builds never produce a final manifest and never
overwrite an existing dataset directory.

## Point-in-time candle index

Version `0.12.5` streams a validated manifest into a disk-backed SQLite replay
index. It preserves the source one-minute candles and creates exact completed
15-minute, one-hour, four-hour, daily, weekly, and monthly Spot views. Weeks
start Monday at 00:00 UTC; months start on their first calendar day at 00:00
UTC. An aggregate is discarded unless the source covers both the exact start
and exact end of that interval, so partial archive boundaries cannot be
mistaken for complete candles.

Every query is evaluated at an explicit historical `as_of` time and returns
only candles whose close is strictly earlier. Exhaustive candidate times come
from completed 15-minute boundaries. Imports are transactional: a late hash,
continuity, coverage, or row failure rolls back every inserted candle and all
metadata.

The index supplies historical Spot candle views only. It does not invent
unavailable point-in-time order books, derivatives history, news, or macro
events. The runner below consumes those views, but verified point-in-time risk
coverage and the policy gates must still pass before any performance conclusion
is possible.

## Exhaustive signal and lifecycle runner

Version `0.12.6` evaluates the Phase 6 signal engine at every completed
15-minute boundary in a declared replay-store range. Each decision receives
only its point-in-time candle view and a risk assessment evaluated at that exact
instant. News published later is rejected. When no historical news/macro
provider is supplied, the built-in provider returns `BLOCK`; it never silently
substitutes `CLEAR` or permits a performance verdict.

Created signals feed two incremental one-minute simulations: the unchanged
fixed track and the managed break-even track. Candles stream from SQLite, so a
long-lived trade does not require loading the remaining multi-year dataset into
memory. New setups remain blocked while the managed track is active and during
the four-hour cooldown. An older fixed virtual comparison may remain open, as
documented by the lifecycle policy, without blocking a later managed setup.

Eligible runs connect directly to the existing purged walk-forward comparison.
A fixed-track result that resolves after a test window begins is excluded from
that fold's training evidence, preventing delayed virtual outcomes from leaking
future knowledge. The runner is performance-ineligible until a verified
point-in-time risk provider is supplied; therefore no representative verdict
has been produced by this version.

## Point-in-time news and macro timeline

Version `0.12.7` adds the disk-backed risk-provider format required by the
runner. Its manifest fixes an exclusive UTC range, one point at every 15-minute
boundary, the derivation version, required official-source coverage, explicit
exclusions, a safe JSONL path, exact row count, and SHA-256. The current
required catalog is the Federal Reserve monetary-policy feed, SEC press
releases, and the BLS release calendar.

Every point records its decision, reasons, coverage issues, cited source IDs,
and the observation times of its evidence. Evidence observed after the
decision is rejected. A required coverage gap must produce `BLOCK`; it cannot
be encoded as `CLEAR` or `CAUTION`. Imports retain the audit fields in SQLite
and roll back completely on any late validation or checksum failure.

Validate a prepared timeline without running the strategy:

```bash
btc-sentinel-validate-risk-history path/to/risk-manifest.json
```

This format proves internal continuity, immutability, and time-causality of the
supplied records. It does not prove that a third party created truthful source
archives, and the repository does not bundle a representative timeline. Source
archive acquisition and provenance review are therefore still required before
an eligible performance run.

Version `0.12.10` defines the normalized evidence input used to build that
timeline. The evidence manifest declares the same exact 15-minute UTC coverage
for every included source. Each source record file has a safe JSONL path,
SHA-256, and exact record count. Required coverage is fixed to Federal Reserve
monetary releases, SEC press releases, and the BLS calendar. News records carry
title, official-domain URL, publication time, and observation time; calendar
records carry stable identity, supported release title, scheduled time,
observation time, and optional official URL.

The builder rejects news claimed to be observed before publication and never
shows any record to an earlier decision. It binds the evidence-manifest hash
into the derivation version, evaluates the unchanged Phase 5 policy at every
boundary, and validates the completed output before publishing its manifest:

```bash
btc-sentinel-build-risk-history \
  path/to/evidence-manifest.json \
  ./derived-risk-history \
  --dataset-id official-risk-2022-2025-v1
```

Version `0.12.11` removes that unauthenticated assembly step. It downloads only
fixed HTTPS paths on the Federal Reserve, SEC, and BLS official sites, rejects
redirects and unexpected content, and stores every raw HTML response with its
URL, retrieval time, and SHA-256. Every normalized record cites the raw
artifact or artifacts that produced it. The SEC's exact UTC archive timestamps
and the Federal Reserve's stated Eastern release times become observation
times without rounding them earlier.

BLS event times are interpreted in `America/New_York`, including historical
daylight-saving rules. A schedule becomes observable only at the conservative
end of its official `Last Modified Date`. If that date falls after the start of
the affected month, the earlier interval is emitted as a required coverage gap;
the derived risk timeline must encode every such point as `BLOCK`.

The first 24 hours are also blocked because the preceding news lookback is
outside the requested archive range. The first and final two hours are blocked
for the equivalent scheduled-event window. These edge guards keep the
year-exclusive input contract without inventing adjacent-year knowledge.

Build a representative official evidence set, where the end year is exclusive:

```bash
btc-sentinel-fetch-risk-history \
  --start-year 2022 \
  --end-year 2026 \
  --output ./official-risk-evidence-2022-2025 \
  --dataset-id official-risk-evidence-2022-2025-v1

btc-sentinel-build-risk-history \
  ./official-risk-evidence-2022-2025/evidence-manifest.json \
  ./official-risk-history-2022-2025 \
  --dataset-id official-risk-2022-2025-v1
```

The downloader has bounded page, response-size, timeout, and retry limits and
never overwrites an output directory. A changed or incomplete official page
aborts before the final evidence manifest is published. The raw pages are
present-day official archives, not cryptographic snapshots captured on their
original dates; the adverse observation and gap rules prevent this limitation
from being converted into invented historical availability.

## Executable historical run

Version `0.12.8` joins both immutable inputs to the exhaustive runner and the
walk-forward evaluator:

```bash
btc-sentinel-run-history \
  path/to/market-manifest.json \
  path/to/risk-manifest.json \
  2022-01-01T00:00:00Z \
  2026-01-01T00:00:00Z
```

The command imports both inputs transactionally into temporary disk-backed
indexes, checks risk coverage contains the requested period, evaluates every
completed 15-minute candidate boundary, and emits one JSON record with separate
fixed and managed verdicts. `--work-directory` may retain the indexes for audit,
but existing database paths are never overwritten. A successful command means
the run completed; the JSON verdict may still be `FAILED` or `INCONCLUSIVE`.

## Conservative simulation

- Only completed, continuous Spot BTCUSDT one-minute candles are accepted.
- Replay begins at the first full minute that could safely follow signal
  creation; look-ahead candles are rejected.
- Entry is modeled at the adverse edge of the entry zone.
- If stop and target are both touched in one candle, the stop wins the
  ambiguity. A target first seen in the activation candle is deferred because
  intraminute order is unknown.
- A gap through a stop fills at the worse candle open.
- Expired, unfilled signals remain `NO_FILL`; trades still open at the dataset
  boundary remain `UNRESOLVED`. Neither is fabricated into a win or loss.
- Round-trip costs are included in R results and are stressed at 1x, 1.5x, and
  2x the configured estimate.

The fixed track preserves the original stop and target. The managed track
replays the default Phase 8 rule: after a completed non-activation candle closes
at or above 1.5R, a cost-adjusted break-even stop becomes effective on the next
candle. Both variants use the same candidate cases and walk-forward windows.
Completed pairs report their managed-minus-fixed R delta; unresolved outcomes
remain explicitly counted.

## Walk-forward evaluation

The default chronological policy uses 120 candidate cases for training, a
four-case purge gap, and 40 cases for each non-overlapping test window. The
primary score threshold is fixed in advance at 80. Thresholds 75, 80, and 85
are reported as sensitivity checks rather than chosen after seeing test data.

A run is `INCONCLUSIVE` unless it contains at least:

- three complete walk-forward folds;
- 30 resolved training trades in each fold;
- 100 resolved out-of-sample trades at the primary threshold; and
- 20 out-of-sample trades in each bullish and bearish trend regime.

After those minimums are met, a variant is `PASSED` only when all of these hold:

- observed strict win rate is greater than 60%;
- the 95% Wilson lower confidence bound is also greater than 60%;
- out-of-sample average R and net R are positive;
- every selected setup planned at least 2R;
- score-threshold sensitivity does not show negative expectancy where it has
  enough observations; and
- average expectancy remains positive at every configured cost multiplier.

Otherwise the result is `FAILED`, with reasons recorded. Break-even and early
exit results remain part of the strict win-rate denominator.

## Change control

The out-of-sample windows are evidence, not a tuning surface. Changing a
threshold, indicator, fill rule, management rule, or cost assumption after
examining those results requires a new strategy or policy version and a new
untouched evaluation period. Phase 13 paper observation remains necessary even
after a passing historical run; neither stage authorizes real trading.

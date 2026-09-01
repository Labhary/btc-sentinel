# News and macro risk contract

Phase 5 converts public news and scheduled economic releases into an auditable
risk gate. It does not generate trades. Market direction, entry, stop, and
targets must come from later deterministic market-data rules; news may only
return `CLEAR`, `CAUTION`, or `BLOCK`.

## Fixed public sources

| Input | Endpoint | Role | Failure policy |
|---|---|---|---|
| Federal Reserve monetary releases | `https://www.federalreserve.gov/feeds/press_monetary.xml` | Required official central-bank evidence | Block |
| Federal Reserve speeches | `https://www.federalreserve.gov/feeds/speeches.xml` | Optional official context | Caution |
| SEC press releases | `https://www.sec.gov/news/pressreleases.rss` | Required official regulatory evidence | Block |
| SEC speeches and statements | `https://www.sec.gov/news/speeches-statements.rss` | Optional official context | Caution |
| BLS release calendar | `https://www.bls.gov/schedule/news_release/bls.ics` | Required timed macro calendar | Block |
| Coinbase incidents | `https://status.coinbase.com/history.rss` | Optional official exchange-status context | Caution |
| GDELT DOC API | `https://api.gdeltproject.org/api/v2/doc/doc` | Optional discovery only | Caution |

Every request is an unauthenticated `GET` to the fixed HTTPS allowlist. The
transport follows no redirects, caps timeouts, attempts, and response bytes,
and never adds exchange credentials. GDELT is queried only for recent English
Bitcoin/BTC coverage and is not considered confirmation by itself.

## Evidence policy

Items outside the 24-hour lookback, more than five minutes in the future, or
unrelated to the explicit BTC/macro risk vocabulary are discarded. Canonical
URLs and normalized title-token similarity group duplicate reports.

| Confirmation | Reliability | Meaning |
|---|---:|---|
| `OFFICIAL_CONFIRMED` | 100 | At least one item came directly from a fixed official source |
| `CORROBORATED` | 75 | Matching reports came from at least two distinct publisher domains |
| `UNCONFIRMED` | 35 | Only one non-official report exists |

The score is a source-confidence weight, not a probability that the headline
is true and never a win-rate estimate. A single unconfirmed high-impact story
can produce `CAUTION`, but cannot impose a verified-news block by itself.

## Risk windows

- High-impact scheduled releases block from 60 minutes before until 45 minutes
  after their published time.
- Extreme scheduled events block from two hours before until two hours after.
- Confirmed high-impact unexpected news requires 60 minutes of market
  confirmation after the newest confirming evidence.
- Confirmed extreme news requires three hours.
- Directionally contradictory confirmed high-impact events block for another
  60 minutes so the later signal engine cannot select the convenient headline.
- A missing required source blocks without inventing an expiry. An optional
  failure produces `CAUTION` and an auditable coverage issue.

The current BLS parser treats CPI, PPI, and the Employment Situation as high
impact; JOLTS and the Employment Cost Index are medium. FOMC entries are
supported as extreme when supplied by a configured calendar. Thresholds are
fixed policy values and must be changed through reviewed, versioned code.

## Safety and limitations

XML entity/doctype declarations are rejected, feeds are bounded to 200 items,
timestamps must carry a timezone, and all item URLs must use HTTPS. The engine
is keyword-based and deterministic; it does not claim semantic understanding.
GDELT is discovery rather than authoritative evidence, and a five-minute batch
runner cannot maintain Binance's persistent announcement WebSocket. Those
limitations are handled conservatively rather than hidden.

Nothing in Phase 5 schedules a production run, sends Telegram messages, writes
orders, requests Binance private keys, or enables real or paper signal
generation.

Official references:

- [Federal Reserve RSS feeds](https://www.federalreserve.gov/feeds/feeds.htm)
- [BLS release calendar and iCalendar help](https://www.bls.gov/help/hlpical.htm)
- [SEC RSS feeds](https://www.sec.gov/about/rss-feeds)
- [Coinbase status history](https://status.coinbase.com/history)
- [GDELT Project](https://www.gdeltproject.org/)

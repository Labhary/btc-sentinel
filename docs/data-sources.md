# Public market-data contract

Phase 3 uses unauthenticated Binance public endpoints for `BTCUSDT` only. The
client has two fixed HTTPS origins, sends no API key or signature, follows no
redirects, and cannot construct order or account requests.

## Endpoint policy

| Input | Public endpoint | Snapshot policy | Failure policy |
|---|---|---|---|
| Spot clock | `GET /api/v3/time` | One exchange reference time per collection | Required: reject |
| USD-M futures clock | `GET /fapi/v1/time` | Must agree with Spot within five seconds | Required: reject |
| Spot candles | `GET /api/v3/klines` | `1M`, `1w`, `1d`, `4h`, `1h`, `15m`, and `1m`; `5m` is available for later optional refinement | Required: reject |
| USD-M futures candles | `GET /fapi/v1/klines` | `1d`, `4h`, `1h`, `15m`, and `1m` | Required: reject |
| Mark/index price and funding | `GET /fapi/v1/premiumIndex` | Current coherent futures reference | Required: reject |
| Current open interest | `GET /fapi/v1/openInterest` | Current futures positioning input | Required: reject |
| Funding history | `GET /fapi/v1/fundingRate` | Bounded optional context | Optional: degrade |
| Open-interest history | `GET /futures/data/openInterestHist` | Five-minute optional context | Optional: degrade |
| Taker buy/sell volume | `GET /futures/data/takerlongshortRatio` | Five-minute optional context | Optional: degrade |
| Spot order book | `GET /api/v3/depth` | Low-weight snapshot, at most 100 levels per side | Optional: degrade |

`1M` above means Binance's calendar-month interval; `1m` means one minute.
Every candle row is validated as an exact 12-field record. Only completed,
contiguous, UTC-aligned candles can enter analysis. Decimal strings are parsed
as `Decimal`, never binary floats.

## Coherence and freshness

A collection is rejected and must lead to `NO TRADE` when any required input is
missing, malformed, stale, too short, gapped, duplicated, future-dated, or
contradictory. The runner clock must be close to both Binance server clocks.
Spot, index, mark, and the latest futures candle must remain within a bounded
coherence range. A collection that takes more than two minutes is discarded.

Historical funding, open interest, taker volume, and the order-book snapshot
are context rather than permission to trade. If one is unavailable or stale,
the snapshot is marked `DEGRADED` and that input is omitted; it is never
fabricated.

## Honest limitations

- Binance exposes only the latest month of open-interest statistics and the
  latest 30 days of taker buy/sell statistics through these public endpoints.
  These feeds cannot provide a complete long-horizon backtest by themselves.
- A REST order-book response is a point-in-time snapshot. It is not historical
  order-book truth and is not written to D1 as raw history.
- Binance's public liquidation stream is live, sampled stream data rather than
  a trustworthy historical liquidation dataset. A five-minute batch job cannot
  reconstruct missed events, so liquidation data is deliberately excluded from
  deterministic Phase 3 evidence. It may later be added only as optional live
  context with explicit gap tracking.
- Public endpoint availability, weights, and retention windows can change.
  They must be rechecked before deployment.

Official references:

- [Binance Spot REST market data](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
- [Binance USD-M Futures REST market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Binance USD-M liquidation streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams)

Phase 5 news and macro inputs have a separate fixed-source, trust, and failure
contract in [news-risk.md](news-risk.md). They can only restrict signal
admission; they cannot supply price levels or create a directional setup.

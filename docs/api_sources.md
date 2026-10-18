# Official API references

Checked 2026-10-18. REST fields and request signing were also cross-checked
against Kalshi's published `kalshi-python-sync` 3.34.0 distribution and its
OpenAPI model descriptions. The SDK is not a runtime dependency.

| Capability | Official source | Implementation |
|---|---|---|
| Kalshi public REST | https://docs.kalshi.com/getting_started/quick_start_market_data | Series, paginated open/unopened markets, fractional-dollar books, public trades |
| Kalshi connection/auth | https://docs.kalshi.com/websockets/websocket-connection | Required handshake authentication; RSA-PSS/SHA256 or Ed25519 signing; no trading subscriptions |
| Kalshi books | https://docs.kalshi.com/websockets/orderbook-updates | Optional empty sides, `yes_dollars_fp`/`no_dollars_fp`, `price_dollars`/`delta_fp`, per-subscription sequence checks |
| Kalshi ticker | https://docs.kalshi.com/websockets/market-ticker | Dollar quotes, fractional volume/open interest, millisecond timestamps |
| Kalshi public trades | https://docs.kalshi.com/websockets/public-trades | Native taker outcome/book side preserved separately; not relabeled as a simple buy/sell |
| Kalshi lifecycle | https://docs.kalshi.com/websockets/market-and-event-lifecycle | Unfiltered `market_lifecycle_v2`; selected market metadata invalidates and refreshes |
| CF index stream | https://docs.kalshi.com/websockets/cfbenchmarks-value | Authenticated `cfbenchmarks_value`, explicit index IDs, raw upstream tick, trailing/final-window metadata |
| Event chart REST | https://docs.kalshi.com/api-reference/live-data/get-event-live-data | Investigated, not periodically downloaded or treated as live settlement data |
| Kraken Spot trade | https://docs.kraken.com/api/docs/websocket-v2/trade | BTC/USD, ETH/USD, SOL/USD; aggressor side, quantity, timestamps, duplicate IDs |
| Kraken Spot ticker | https://docs.kraken.com/api/docs/websocket-v2/ticker | BBO-triggered updates with last, bid, ask |
| Kraken L2 | https://docs.kraken.com/api/docs/websocket-v2/book | Depth 10, absolute quantities, zero deletes, trim before checksum |
| Kraken CRC32 | https://docs.kraken.com/api/docs/guides/spot-ws-book-v2 | Top ten asks then bids, original decimal precision preserved |

Kalshi shared REST at `api.elections.kalshi.com` remains explicitly documented
as supported. The WebSocket documentation names `external-api-ws.kalshi.com`.
Every Kalshi WebSocket connection needs authentication, even for public channels.
No Kraken authentication or L3 subscription is required for Phase 1.

## Contract findings from actual REST responses

BTC, ETH, and SOL 15-minute contracts compare the simple average of 60 CF
reference observations before the cutoff with the opening 60-second average.
The observed comparison is **at least** (`greater_or_equal`): equality resolves
YES. Direction comes from these rules and comparison metadata, not a ticker.
Actual outcome subtitles can be `Target Price: ...` / `Target price: TBD`.

Contract text uses `BRTI`, `ETHUSDRTI`, and `SOLUSDRTI`; the feed IDs are `BRTI`,
`ETHUSD_RTI`, and `SOLUSD_RTI`. Rounding comes from each contract's
`custom_strike.round_digits`, never a global two-decimal assumption (SOL observed
four, BTC two). Full rule text and contract URLs remain visible for review.

`avg_60s_data` excludes the current tick, counts prior ticks, and falls back to
the current value if empty. The final-minute field is present only in
`(quarter_close - 60 seconds, quarter_close]`, excludes the start tick, and
includes the close tick. The implementation keeps this partial observed average
separate and checks its boundaries against the selected market. Only the
exchange's actual determination can establish the official settlement result.

## Acceptance limits

Live Kraken data and public Kalshi REST were observed for all three assets.
Authenticated Kalshi/CF and lifecycle delivery cannot be live-verified until
credentials are configured. The live checker fails explicitly for these gaps.
Local protocol tests do not establish account permissions or index availability.
Optional Kraken L3/futures and economic-event sources are deferred.

## Phase 2 additions

See [paper execution](paper_execution.md) for fee schedule, fractional rounding,
per-order rebates, event overrides, and scheduled-change references. Public live
metadata confirmed quadratic × 1 for all three 15-minute series. An actual public
settled BTC response used `status=finalized`, `result=yes`, and `settlement_ts`;
the reconciler accepts final exchange results instead of inferring settlement.
All exchange operations in this application remain GET requests/subscriptions.
Paper mutations affect local SQLite only.

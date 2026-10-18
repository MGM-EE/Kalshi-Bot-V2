# Phase 2 paper execution

Manual controls use `POST /api/paper/orders`, `DELETE /api/paper/orders/{id}`,
and `GET /api/paper/portfolio`. The backend accepts loopback hostnames and rejects
foreign-origin writes. No exchange order endpoint is called; automatic strategies
and real-money execution are absent. Credentials never enter the browser.

Orders require a unique `client_order_id`, asset/ticker, YES/NO outcome, action,
fractional quantity, limit, mode, and (for buys) fee-inclusive `max_cost` budget.
An identical retry returns the original order; a conflicting ID returns 409.
The UI retains requests for retry when a response is lost. Submission is not a fill.

## Execution model

- Accepted orders persist as pending and reserve cash or sell inventory. After
  latency, data, cutoff, prices, fees, and risk constraints are rechecked.
- Marketable IOC orders walk asks to buy and bids to sell. Slippage moves prices
  adversely onto the actual tick grid, respecting limits and maximum slippage.
  Usable depth is reduced by participation. Depth or budget can produce partial
  fills; the remainder is cancelled. Terminal states distinguish filled, partial,
  unfilled, rejected, cancelled, and expired.
- Persistent canonical YES-book consumption debt prevents reusing visible depth,
  including complementary NO trades and restarts. Debt is retained for the market;
  only displayed quantity above accumulated debt supports further taker fills.
  This deliberately underfills replenishing books.
- Passive mode is post-only; crossing orders are rejected. Queue ahead equals
  visible resting size times a multiplier plus a buffer. Only subsequent, fresh,
  non-block prints at the exact canonical limit with known opposing taker exposure
  reduce the queue. Participation reduces excess volume before filling. Touches,
  cancellations, duplicate/old/unknown/wrong-side trades cannot invent fills.
  One passive order per canonical market/side/price is supported. Queue priority
  remains an estimate, not an exchange guarantee.
- Cancellation releases only the unfilled reservation. Existing fills remain.

## Fees

Effective series fees include event overrides. Scheduled changes expire metadata
at their boundary; a refresh overlapping a fee change is rejected until refreshed.
Unknown fee structures and active unverified fee waivers block execution.

The quadratic taker formula is `0.07 × multiplier × quantity × p × (1-p)`.
Maker rates are zero for ordinary quadratic series, 0.0175 for
`quadratic_with_maker_fees`, and 0.035 for combo maker fees. Current BTC/ETH/SOL
15-minute series reported quadratic × 1.

Each fill applies the documented six-decimal trade-fee ceiling, signed-revenue
rounding, and persistent per-order accumulator/rebate. Cash precision defaults
to $0.01 (non-direct member); $0.0001 direct-member precision is configurable
for a new account. Existing account precision cannot change silently. Logs retain
model/rounding/rebate/net fees and raw/fill prices/slippage. External clearing-broker
surcharges are outside this exchange-fee model. Settlement fees are zero.

Official references checked 2026-10-18:

- https://kalshi.com/docs/kalshi-fee-schedule.pdf (effective July 7, 2026)
- https://docs.kalshi.com/getting_started/fee_rounding
- https://docs.kalshi.com/getting_started/fixed_point_migration
- https://docs.kalshi.com/api-reference/events/get-event
- https://docs.kalshi.com/api-reference/events/get-event-fee-changes
- https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes

## Accounting, risk, and settlement

SQLite uses WAL, FULL synchronization, decimal strings, and `BEGIN IMMEDIATE`.
Each fill atomically commits its fee accumulator, cash, position, counter, and
liquidity debt. Failed transactions roll back all changes. Concurrent connections
cannot reserve the same cash. Restarts preserve balances and committed fills.

Cost basis includes entry fees and is allocated proportionally for partial exits.
Asset/ticker/outcome accounting is separate. Entry count means buy orders with
their first fill; a round trip completes when an outcome position returns to zero,
including settlement. Open-position count is separate.

Hard limits include budgets, reserved/committed round/asset/portfolio exposure,
quantities, positions, entries/round trips, maximum potential position/round loss,
UTC daily equity drawdown, and consecutive losing completed positions. Current
limits are rechecked before filling. Protective sells bypass entry minimum size
and missing Kraken/CF data, while still requiring a fresh Kalshi book, verified
fees, open market, and unreserved inventory. Drawdown pauses new buys; adaptive
automatic exits belong to Phase 3. Portfolio marks use conservative executable
proceeds and value unavailable/unfillable quantities at zero, not last trade price.

Orders retain execution configuration, strategy version, submission rules/health,
and fills. Version intentional configuration changes. Existing orders retain their
execution settings, while current hard risk limits continue to apply.

After cutoff, positions wait for the REST market's final `finalized`/`settled`
YES/NO result and settlement timestamp. The historical endpoint is used on 404.
Settlement pays $1 per winning contract, $0 per loser, exactly once. Void/scalar
and conflicting results need separate reconciliation. Spot/index extrapolation
cannot establish settlement.

`config/paper.json` is validated at startup; `PAPER_CONFIG_PATH` and
`PAPER_DATABASE_PATH` optionally select user-managed files outside Git. Defaults:
$1,000 initial cash, 250 ms latency, 50% usable depth, $0.001 adverse adjustment
snapped to ticks. Starting balance only initializes a new database. Keep DB/WAL
together; use SQLite's backup API online or stop the app before copying them.

Focused tests: `python -m pytest tests/test_paper.py -q` in the virtual environment.
Real public-data checks verified fee metadata/depth and safe missing-CF rejection.
Authenticated live fills remain unverified until signing credentials are supplied.

# Project status

Updated: 2026-10-18.

**Current phase: 1 — implementation committed; live acceptance remains pending.**
The application is read-only. No paper broker, strategy execution, or real-money
order submission exists. Do not start Phase 2 without explicit user approval.

## Implemented

- Python/FastAPI asyncio backend and lightweight compiled TypeScript dashboard.
- Real Kraken Spot WebSocket v2 ticker, trades, and checksum-validated ten-level
  L2 books for BTC/USD, ETH/USD, and SOL/USD over one connection.
- Kalshi series discovery, paginated open/unopened markets, current/upcoming
  selection, rollover refresh, public fractional-dollar REST books and trades.
- Authenticated Kalshi WebSocket connector with RSA-PSS/Ed25519 signing,
  ticker/trade/book subscriptions, snapshot/delta reconstruction, sequence-gap
  invalidation, lifecycle notifications, and subscription refresh.
- CF Benchmarks feed adapter with correct explicit index IDs, upstream
  timestamps, trailing averages, and selected-round partial final-minute averages.
- Rule-based YES/NO direction mapping from recognized contract wording and
  comparison metadata, including tie semantics and contract-specific rounding.
- Distinct spot/opening strike/current index/partial average/settlement fields.
  Unknown data remains unavailable; no synthetic runtime data or invented forecast.
- Per-source receive/exchange ages, stale/clock-drift states, bounded histories,
  duplicate/out-of-order handling, reconnect backoff, and invalid-book reporting.
- Locked Python/npm installs, hash-verified pip export, setup documentation,
  Windows setup/launch/self-test scripts, and opt-in live acceptance checker.
- Original supplied project brief retained in `docs/project_brief.md`.

## Validation completed

- **29 deterministic tests passed**, including market selection/mapping,
  complementary executable asks, fractional depth, Kraken CRC32, sequence gaps,
  reconnect snapshots, duplicate/aggressor trades, signatures, stale data,
  CF average boundaries, lifecycle invalidation, read-only API, and local
  Kraken/Kalshi/dashboard WebSocket transport.
- Frontend build/typecheck and Python lint passed.
- Actual backend startup, dashboard HTML/compiled JavaScript, HTTP liveness,
  and dashboard WebSocket delivery passed on Linux.
- Saved cloud install commands were executed successfully, including frozen
  installation, `npm ci`, UI build, and focused tests.
- Actual live Kraken ticker/trade/book channels were observed for all assets.
  Checksums passed on real incoming books.
- Actual Kalshi REST markets, valid books, and 100 recent executions per asset
  were received. Observed outcome mappings were YES=UP / NO=DOWN for all three.
  Tests use separate deterministic fixtures; these live observations are not
  represented as authenticated-stream tests.
- Test runner emitted one upstream Starlette/httpx deprecation warning; all tests
  ran and passed. No assertions were suppressed.

## Phase 1 acceptance still outstanding

1. Securely provision `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in the
   launching process. No matching environment variables were present. The path
   must name the actual local signing PEM outside Git, supplied securely; a
   proxy placeholder cannot sign locally. Never send the private key in chat.
2. Restart and run `python scripts/live_check.py --seconds 30` using the virtual
   environment. Verify actual authenticated Kalshi snapshots/deltas, tickers,
   trades, CF ticks/averages, and lifecycle delivery. Validate rollover across
   a real cutoff. The latest live run exited **2**, correctly identifying
   missing authenticated Kalshi/CF data. REST polling is a partial fallback.
3. Confirm index permissions/availability and current contract rules on the
   authenticated connection. Local protocol tests cannot establish these.
4. Execute the supplied launcher/setup on Windows; only Linux was available here.

No remaining application defect was identified by the focused tests. Phase 1
is **not accepted as fully complete** while its authenticated live checks remain
unrun. Benchmark parsing is implemented but has not received a real authenticated
CF tick in this session.

## Environment configuration saved

Saved `install_script`, `start_skill`, additive runtime variable requirements for
the Kalshi key ID/local key path, and required API/documentation network hosts.
Existing package-manager presets were preserved. The setup/startup instructions
use the existing isolated checkout and keep execution disabled.

Draft persistence was confirmed. Review and save the configuration in environment
settings, then publish when appropriate. Saving a draft does not itself publish
the environment or provision the signing key. The Git commit is local; no push
was requested or performed. Fresh-task snapshot restoration is not verified.

## Remaining phases

| Phase | Remaining work |
|---|---|
| 2 | Exact fees, depth-based partial fills/slippage, risk limits, durable SQLite paper accounting, buy/sell/settlement verification |
| 3 | Immutable BASELINE V1, recover/document unavailable legacy filters, CUSTOM V2 conditions, adaptive deterministic exits, versioned settings and multiple round trips |
| 4 | Venue-specific whale flow, liquidity persistence, volatility, chop/swing scores and confirmation/hysteresis |
| 5 | Full decision strip/condition states, settings UI, portfolio views, confirmed-fill notifications |
| 6 | Compact event recording, deterministic replay, strategy comparisons, analytics and performance validation |

Next step: finish credential-dependent Phase 1 acceptance, summarize the result,
and wait for approval before Phase 2. Keep subsequent changes within the approved
phase, test them, update this file, and commit working changes.

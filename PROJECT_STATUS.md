# Project status

Updated: 2026-10-18.

**Current phase: 2 — paper engine implemented; authenticated live acceptance pending.**
Manual paper execution is available; real-money order submission is absent.
The user has authorized the remaining build phases, one at a time with focused
checks, status updates, and separate commits.

## Local credential setup checkpoint

The user subsequently approved continuation through the remaining build phases.
Phase 3 follows this credential integration; further phase checkpoints still get
focused tests, status updates, and separate Git commits.

- Native `scripts/configure_kalshi.bat` imports the ID and downloaded PEM into
  `%LOCALAPPDATA%\KalshiTerminalV2\credentials.dpapi` using current-user Windows
  DPAPI. The helper supports confirmed replacement/removal; no plaintext fallback.
- The backend loads that encrypted local copy when environment bindings are
  absent. Partial environment bindings fail explicitly. Signing uses memory,
  and the browser status endpoint exposes presence/source/scope only.
- PC and cloud credentials are separate; no key forwarding or PC listener exists.
  See `docs/local_credentials.md` for the setup steps and limitations.
- Four new encryption/store/signing/API tests pass with a test encryption
  provider. The real DPAPI test is Windows-only and skipped on this Linux host.
  Actual native dialog, Windows DPAPI, and authenticated feeds remain unverified.

## Phase 2 completed

- Durable paper-only broker and local API/dashboard controls for buy/sell,
  marketable IOC/passive post-only orders, cancellations, latency, and partial fills.
- Decimal depth walking, adverse tick-grid slippage, fee-inclusive budgets, and
  persistent canonical liquidity debt preventing reuse of consumed depth.
- Conservative passive queues driven by fresh, opposing public trade prints;
  no fills from touches, cancellations, duplicates, or unknown/block trades.
- Current series/event fee overrides and scheduled-change expiry, six-decimal
  fee rounding, per-order rebate accumulators, and explicit account precision.
- Atomic SQLite cash/inventory reservations, fills, fee/cost accounting, counters,
  restart idempotency, and official-result settlement with historical fallback.
- Hard allocation, exposure, entry/round-trip, loss, drawdown, and quantity limits
  rechecked before fills. Protective exits can use fresh Kalshi data when other
  feeds fail, and partial holdings can exit below the minimum entry size.
- Current paper configuration in `config/paper.json`; mechanics and limitations
  documented in `docs/paper_execution.md`. No automatic strategy logic was added.

## Phase 2 evidence and limitations

- **62 total deterministic tests passed** (33 focused paper tests plus 29 existing
  feed/application tests), including local API buys and broker buy/sell/settlement paths,
  partial exits, restart recovery, competing reservations, rollback injection,
  fee schedule expiry, queue behavior, stale/cutoff rejection, and request security.
- Python lint, TypeScript build/typecheck, and local server/dashboard/paper API
  startup checks passed. Windows execution still requires a Windows machine.
- Live BTC/ETH/SOL REST checks confirmed valid books, quadratic × 1 effective
  fees, and depth/fee previews. All three attempted buys correctly rejected
  missing CF references; the isolated test account stayed unchanged. These are
  not authenticated live fills. Actual finalized settlement metadata was also read.
- Signing credentials remain absent. Authenticated live paper buy/sell and CF
  acceptance cannot be claimed; earlier Phase 1 live checks below remain pending.
- High reasoning was used for financial invariants. The next operational step
  (secure credentials, connectivity, platform checks) can use Medium. Return to
  High for Phase 3 entry/exit precedence and configuration logic.

## Phase 1 foundation

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

## Phase 1 validation completed

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
Existing package-manager presets were preserved. Phase 2 startup instructions
retain the isolated checkout and database, start only local paper execution, and
keep automatic strategies/real-money execution disabled. `kalshi.com` was added
to the preserved custom allowlist to read the official fee schedule.

Draft persistence was confirmed. Review and save the configuration in environment
settings, then publish when appropriate. Saving a draft does not itself publish
the environment or provision the signing key. The Git commit is local; no push
was requested or performed. Fresh-task snapshot restoration is not verified.

## Remaining phases

| Phase | Remaining work |
|---|---|
| 2 | Implemented; authenticated live acceptance and Windows checks remain pending |
| 3 | Immutable BASELINE V1, recover/document unavailable legacy filters, CUSTOM V2 conditions, adaptive deterministic exits, versioned settings and multiple round trips |
| 4 | Venue-specific whale flow, liquidity persistence, volatility, chop/swing scores and confirmation/hysteresis |
| 5 | Full decision strip/condition states, settings UI, portfolio views, confirmed-fill notifications |
| 6 | Compact event recording, deterministic replay, strategy comparisons, analytics and performance validation |

Next step: securely configure Kalshi signing credentials and complete live
acceptance using Medium reasoning. Then obtain approval for Phase 3 and use High
for strategy logic. Keep changes within the approved phase, test them, update this
file, and commit working changes. Commits remain local unless a push is requested.

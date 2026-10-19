# Project status

Updated: 2026-10-18.

**Current phase: 6 — all six build phases implemented; external acceptance pending.**
Manual paper execution is available; real-money order submission is absent.
The user has authorized the remaining build phases, one at a time with focused
checks, status updates, and separate commits.

## Windows local accept failure checkpoint

- User traceback identified WinError 64 in Windows Proactor/IOCP while accepting
  browser connections on `127.0.0.1:8000`. Windows launches now pass the actual
  Selector event-loop factory to the child Uvicorn process; normal/recorded
  sessions share this path. Non-Windows launches retain their default loop.
- The startup banner confirms compatibility mode. Updating only
  `scripts/launch.py` activates it after the old backend is stopped and restarted.
  Accounts, credentials, strategy rules, dependencies and backend source are
  untouched; the recording code fingerprint is unchanged.
- **Eight focused tests passed**, including real child-server HTTP/WebSocket
  accept/disconnect cycles for normal and recorded accounts, graceful shutdown,
  and exact replay after recorded shutdown. Lint/diff checks passed.
- Remaining: verify on the user's Windows PC that compatibility mode starts and
  the reported IOCP accept error no longer recurs. This targets the reported
  local accept failure; exchange-specific outages still need source diagnostics.

## Windows recovery and plain-language status checkpoint

- Added a six-card **What's happening?** panel: app connection, local API setup,
  required price feeds, paper-bot activity, saved accounts and optional recording.
  Fresh backend updates no longer appear disconnected merely because market
  feeds are missing. Troubleshooting export excludes credentials and balances.
- Short stream gaps use read-only HTTP backup with response-order protection.
  Stale/display-error states remain explicit; dead sockets receive bounded
  retries. Server sessions consume disconnect events and expose snapshot errors.
- Added root `START_HERE`, `CONNECT_KALSHI`, `OPEN_TERMINAL` and
  `RECORD_PAPER_SESSION` batch launchers, automatic desktop shortcut creation,
  clear prerequisite/failure messages and safe repeated-open behavior.
- Added [Windows recovery guide](docs/windows_quick_start.md). Existing external
  Windows credentials may survive folder deletion; deleted local trade history
  requires a backup. Setup preserves existing accounts and credential stores.
- Validation: **135 Python tests passed; one Windows-only skipped**; lint and
  TypeScript build passed. Chromium connection, status/privacy/mobile and full
  dashboard/replay checks passed. A 47-second live-feed check used one socket,
  recovered from an intentionally withheld 8.5-second stream interval via two
  backup requests, and reported zero browser errors.
- Remaining: actual Windows install/shortcut/DPAPI and authenticated Kalshi/CF
  acceptance on the user's PC. Original Windows reset cause remains unproven;
  connection recovery and diagnostics address observed failure behavior.
  Next: download the recovery branch, install outside OneDrive, reconnect the
  existing key and inspect the status cards before enabling paper entries.

## Post-build hardening checkpoint

- Dashboard connection follow-up: initial connection gets 30 seconds; after frames
  arrive the five-second freshness cutoff remains. Rendering/JSON failures now
  report a visible display error and console details, retry the next frame, and
  no longer silently close the socket. Stale displays retain the offline state.
- Validation: TypeScript build, focused Chromium connection regression, existing
  dashboard Chromium acceptance, and six backend dashboard/API tests passed.
  A 45-second live-feed browser check received 87 frames on one socket without
  display errors. An earlier live check reconnected once; the user's Windows
  disconnect cause is not yet established. Next: apply UI update on the PC and
  collect the now-visible error if disconnects continue. Backend paper execution
  is independent of the browser connection; display warnings do not pause it.

- Added `scripts/create_desktop_shortcut.bat`: creates a Kalshi Terminal desktop
  shortcut using Windows' Desktop location (including OneDrive). It launches the
  existing app launcher from the project directory. Static review completed;
  Windows shortcut creation needs verification on a Windows PC. Next step:
  double-click the installer, then verify desktop launch on the user's PC.

- Reference-cross exits survive revalidation, cancellation and restart; a favorable
  recovery clears the trigger. Accumulated holdings exit in valid order sizes.
- Only confirmed partial-target fills consume the target for new position cycles.
  Manual reductions and cancelled orders do not; legacy flags remain compatible.
- Mandatory feed/fee readiness gates signals before submission and recovers cleanly.
  Unsupported fees on one asset no longer strand another asset's exits.
- Conservative marks exclude closed/paused markets, future/stale metadata and
  consumed depth. Settlement requires coherent finite close/settlement timestamps.
- **128 tests passed; one Windows-only skipped.** Lint, TypeScript build and
  Chromium checks passed. Six extended synthetic runs covered **3,600 actions,
  2,678 fills, 726 maker fills and 180 settlements** across three fee models/two
  precisions, with independent ledger audits and exact replay.
- Fresh real Kraken capture: **2,169 events**, exact replay, zero failed cycles.
  No authenticated CF or live paper fills claimed. Windows/key acceptance remains.

## Phase 6 completed

- Opt-in, fresh-account gzip recording with normalized patches, original source
  times, timer cycles, controls/config versions, account/indicator observations,
  bounded background writer, integrity footer and failure visibility.
- Isolated deterministic replay through the same features/indicators/strategy/
  broker classes; matching code and exact ledger/config/decision fingerprints.
  Corrupt, incomplete and out-of-order inputs fail. No replay connector exists.
- Matched strategy comparisons, dataset hashes, explicit research-period labels,
  cycle performance reports and JSON audit exports through CLI and dashboard.
- Full-ledger cycle analytics including partial exits/settlement, net expectancy,
  fees/slippage, observed/realized drawdown, asset/timing/price/whale/chop/exit
  breakdowns, durable decision transitions, rejections and observed blocked signals.
- Dashboard reads are pure: confirmation state and account peaks advance in the
  backend, with recorded mark cycles. Stale exchange books/expired fees cannot
  inflate conservative valuations.
- Trade audits now retain actual entry metrics, reference/line, indicator version,
  executable quotes, source ages, pinned criteria, fees/slippage and exit reasons.
- **99 Python tests passed, one Windows-only skipped**; lint/build passed. After
  final diagnostics and read-only UI fixes, the full suite passed again. Cases cover exact manual/automatic replay, partial targets/settlement,
  no future fill improvements, changed profile outcomes, restart settings, file
  corruption, rollover deletion, queue overflow and source invalidation.
- Real live recording: **4,690 events / 175,092 compressed bytes / 25.81 seconds**;
  exact replay in **0.18 seconds**, 214 cycles, zero failed cycles, cycle p95
  **0.30 ms**. Kraken trades/statistics arrived for BTC/ETH/SOL. Kalshi's public
  API returned no current open markets in this interval; no CF or fills were
  claimed. A final post-read-only-fix capture also reproduced **2,038 events**
  exactly (108 cycles, zero failures). Earlier real REST/book evidence remains
  recorded in prior checkpoints.
- Synthetic 10,000-event stress fixture: source-update pair p95 **0.043 ms**,
  all-asset snapshot p95 **20.23 ms**, 4.51s runtime. Separate allocation tracing
  measured **8.16 MiB** peak and increased timings substantially. These are
  host-specific measurements, not live acceptance or performance guarantees.
- Linux launcher exit/isolated recording replay passed. Chromium verified replay
  results and unchanged live cash/order count, compact/mobile UI and fill alerts,
  with zero browser exceptions. Windows shutdown remains unverified.
- See `docs/replay_analytics.md`; no sufficient profitability dataset was collected.

## Phase 5 completed

- Compact sticky decision strip with asset/outcome selection, real source modes,
  thresholds/actuals, separate buy/sell conditions and prioritized decisions.
- Expandable observed sparklines, venue-separated whale/risk/liquidity views,
  portfolio/positions/counters, daily realized and conservative all-time P&L,
  source ages and confirmed research events.
- Backend-schema field editors for profiles and indicator/execution/risk settings;
  immutable versions, pinned profile criteria and explicit open-position behavior.
  Execution settings persist across restarts and cannot reset balance/precision.
- Durable fill IDs drive brief buy/sell alerts once; initial history, repeated
  frames and signals do not flash. User toggle and reduced-motion support.
- **91 Python tests passed, one Windows-only skipped**; lint/build passed.
  Real Chromium verified a 160px strip at 1440px, phone-width overflow handling,
  field controls, baseline immutability, duplicate suppression and disabled flashes,
  with zero browser exceptions. Browser notification fixtures are synthetic.
- Actual live app/API and REST/Kraken UI startup passed with isolated database;
  missing CF/authentication remains explicit. See `docs/dashboard.md`.

## Phase 4 completed

- Separate Kraken USD/coin and Kalshi USD/contract large-trade detectors using
  fixed, prior-percentile, prior-median and spot burst definitions, bounded
  5/15/30/60/300-second windows, CVD/VWAP/flow acceleration and confirmation.
- Aggregated L2 persistence/replenishment with disconnect/gap resets, observed
  pressure and potential absorption; no claims about holdings or cancellations.
- Observed volatility/momentum/efficiency, CF crossings, transparent chop and
  outcome-specific swing scores, confirmation/hysteresis and versioned settings.
- Warmup, unknown direction, capacity loss and source transitions remain explicit.
  REST samples never qualify as complete Kalshi live flow. Required unknown
  strategy metrics block. Definitions: `docs/indicator_definitions.md`.
- **88 tests passed, one Windows-only skipped**; Python lint and frontend build
  passed. Focused cases cover no lookahead, units, deduplication, gaps, prior-mode
  contamination, bounded scores, persistence, hysteresis and config validation.
- A real 12-second check received 58/53/51 Kraken trades for BTC/ETH/SOL and
  Kalshi REST data. Flow correctly stayed incomplete for polling; spot statistics
  remained unavailable where six sufficiently continuous observed seconds were
  missing. Authenticated CF-derived live scores remain unverified.

## Phase 3 completed

- Immutable recovered BASELINE V1 entry rules, CUSTOM V2 and named research presets,
  validated/versioned JSON profiles, import/export editor, and local strategy controls.
- Strict/weighted conditions with explicit unknown/confirmation/warning states,
  source-aware CF approach and spot features, fee/depth sizing, conditional trend
  price caps, and actual-fill all-in cap enforcement.
- Deterministic adaptive exit priority, net P&L, partial profit taking, trailing
  protection, line/risk/timing rules, optional settlement holds, and pinned criteria.
- Durable batched signal state/intents, idempotent recovery, cooldown/reentry and
  multiple round trips. Emergency blocks all buys; pause preserves pinned exits.
- **82 tests passed, one Windows-only test skipped**; frontend build and Python
  lint passed. Strategy tests cover baseline boundaries, unknown weighted inputs,
  late revalidation, profile changes, partial targets, multiple cycles, and restarts.
- Advanced whale/chop/swing metrics were subsequently added in Phase 4.
  Original extra baseline filters/exits remain unrecoverable and are not invented.
  Live strategy fills still require authenticated Kalshi/CF access.

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
Existing package-manager presets were preserved. Updated phase 6 startup instructions
retain the checkout, accounts and recordings, inspect persisted strategy controls,
and explain local credentials, isolated replay and pending acceptance. Real-money
execution remains absent; new paper accounts start paused. `kalshi.com` was added
to the preserved custom allowlist to read the official fee schedule.

Draft persistence was confirmed. Review and save the configuration in environment
settings, then publish when appropriate. Saving a draft does not itself publish
the environment or provision the signing key. The Git commit is local; no push
was requested or performed. Fresh-task snapshot restoration is not verified.

## Remaining acceptance by phase

| Phase | Remaining work |
|---|---|
| 2 | Implemented; authenticated live acceptance and Windows checks remain pending |
| 3 | Implemented; original additional V1 filters/exits unavailable; live acceptance pending credentials |
| 4 | Implemented; authenticated CF/Kalshi flow acceptance remains pending |
| 5 | Implemented; Windows and authenticated live end-to-end acceptance remain pending |
| 6 | Implemented and tested; sufficient authenticated research/validation/out-of-sample datasets still need collection |

Next acceptance steps:

1. On the Windows PC, run `scripts/configure_kalshi.bat`, select the downloaded
   signing key and enter its API key ID; restart `scripts/start_windows.bat`.
   The app uses signed data authentication, not a login password or real orders.
   No key should be sent in chat. Cloud cannot read the PC's encrypted store.
2. Run Windows self-tests, then authenticated live feed acceptance across a real
   market/rollover. Confirm CF permissions, fresh references and paper fills on
   an intentionally isolated research account. Current live acceptance is pending.
3. Collect disjoint recorded development/validation/out-of-sample data before
   assessing strategy performance. Recover original unavailable baseline filters
   before claiming full equivalence to any earlier strategy.
4. Review/save the saved cloud configuration draft in environment settings, then
   publish when desired. Draft persistence is confirmed; publication/restoration
   is not. Local-only commit restoration remains unverified.

All phase commits remain local; no push or real-money action was requested or
performed. Medium is sufficient for routine credential/platform setup; High is
appropriate for later strategy, risk or replay changes.

# Rebuild audit: verification and limits

Prepared October 18, 2026 for `MGM-EE/Kalshi-Bot-V2` at `ce0f3d0`. No application source, paper configuration, account database, credentials, or deployment was changed by this audit. The repository additions are planning documents, audit reproductions/results, and a README link. Scratch checks initially ran from `work/rebuild-audit/`.

## Existing baseline

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 135 passed, 1 skipped, in 22.74 s |
| `npm run typecheck` | Passed |
| `.venv/bin/python scripts/benchmark.py --events 10000` | Completed; 3.54 s synthetic fixture; source-update pair p95 0.0287 ms; all-asset indicator snapshot p95 14.59 ms |
| `.venv/bin/python scripts/live_check.py --seconds 30` | Exit 2: incomplete feed acceptance |

The skipped check is the Windows-only credential test. The suite emitted one Starlette/httpx deprecation warning. The synthetic benchmark omits real network latency, full broker/strategy/account workload, IPC, durable commits, and browser rendering. It is evidence about the existing fixture only.

## Three reproduced recovery defects

### 1. A closing browser socket prevents replacement

Executed the existing `frontend/src/connection.ts`, transpiled with the project's TypeScript compiler, in a Node VM with a virtual clock and a WebSocket stub whose `close()` moves to CLOSING but never emits `onclose`.

At 31 simulated seconds, the startup watchdog called `close()`. For the next simulated hour, the tick callback kept running but no second WebSocket was created:

```json
{"simulated_seconds":3600,"sockets_created":1,"socket_state":"CLOSING","replacement_created":false}
```

The recovery condition requires `!ws`; CLOSING is excluded from further close handling, and the reference is cleared only by the missing callback. This is a deterministic lifecycle defect. It does not establish that this exact event occurred on the user's Windows browser. The existing browser regression stub immediately closes and calls `onclose`, so it does not exercise this state.

### 2. Fallback is disabled with no valid streamed book

Ran the existing Kalshi session against a local in-memory transport context. It accepted subscription sends and repeatedly supplied an unrelated `subscribed` packet. No book ACK/snapshot was supplied. Invoked the real `poll_asset` with a client that counted requests and would fail on any network call.

```json
{
  "stream_live": true,
  "socket_health": "live",
  "book_valid": false,
  "fallback_requests": 0,
  "session_still_running": true
}
```

The session sets its global flag before ACKs or snapshots. Any continuing JSON frame refreshes socket activity, while fallback checks only that global flag. A required channel can therefore remain invalid without its own deadline/repair. This check made no exchange requests.

### 3. A CF subscription error invalidates usable books

Established valid synthetic BTC and ETH snapshots through the real `Kalshi.handle`. Then ran the actual retry wrapper with a synthetic protocol error correlated to CF request ID 2. The generic error handler raised `BookError`; the wrapper invalidated both books:

```json
{"book_valid_after_cf_subscription_error":{"BTC":false,"ETH":false}}
```

This confirms the failure scope in code. It does not claim that the production CF channel returned that error during this session.

## Live observations

The 30-second read-only check observed valid Kalshi REST books and public recent trades for BTC, ETH, and SOL, plus live Kraken spot prices/books/trades. At the final sample, Kalshi book/trade source statuses were stale, all CF references were unavailable, Kalshi ticker channels were unavailable, and the SOL Kraken trade channel was stale while its book/ticker and transport were live.

Runtime configuration reported both Kalshi credential variables configured. The normal credential loader failed with `FileNotFoundError`; no credential values or paths were printed. Thus configured variables did not provide a usable local signing key in this cloud runtime. This is distinct from the user's Windows credential store. No authenticated Kalshi/CF success or failure rate can be inferred from this run.

The final stale labels alone do not establish the cause or duration of the Windows dropouts. In particular, time since the last SOL trade does not prove a transport failure in a quiet market. The live checker is a point-in-time acceptance check, not a reconnect endurance test. No real-money orders exist in this application, and this audit submitted no paper orders either.

## Additional code findings, not reproduced PC incidents

- No independent stale-subscription repair task in either connector.
- Background task exit is not supervised during application lifespan.
- Kalshi market revisions cause a whole-session reconnect.
- REST discovery/fees/fallback share a serial scheduler.
- Socket callbacks, indicator observers, strategy, SQL and UI snapshots share the asyncio process.
- Strategy evaluations have a 250 ms gate; UI updates are scheduled every 500 ms, plus processing/send time.

These are supported by code inspection; profiling and failure injection must establish their operational impact during the build.

The [Python reproduction](audit/reproduce.py), [browser transport reproduction](audit/reproduce-browser.cjs), [connector results](audit/reproductions.json), [browser result](audit/browser-reproduction.json), [benchmark output](audit/benchmark.json), and [live-check output](audit/live-check.json) are retained as historical baseline evidence. Run these scripts only against the audited V2 commit `ce0f3d0`, from that checkout's root:

```bash
.venv/bin/python docs/rebuild/audit/reproduce.py
node docs/rebuild/audit/reproduce-browser.cjs
```

The two reproduction scripts use synthetic transports and do not contact exchanges or open an account database. The browser reproduction runs the transport function in a Node VM; it is not a real browser/network endurance test. Assertions intentionally confirm the old defects and are expected to fail on corrected code. Current recovery assertions live in `tests/test_kalshi_recovery.py`, `tests/test_kraken_recovery.py`, `tests/test_runtime_recovery.py`, and `tests/ui_transport.cjs`; see the [connectivity update](../connectivity_update.md).

## What remains to validate in the rebuild

Authenticated Kalshi/CF subscriptions and index availability, actual book-repair protocol sequencing, Windows installation/DPAPI/sleep/network changes, live rollover behavior, process restart and database stalls, end-to-end latency, and the 72-hour unattended soak remain release gates. The targets in the blueprint are requirements to test, not results achieved by these checks.

## Official protocol sources checked

- [Kalshi connection and subscription commands](https://docs.kalshi.com/websockets/websocket-connection): required handshake authentication, subscription IDs, acknowledgements, error correlation, and subscription updates.
- [Kalshi orderbook updates](https://docs.kalshi.com/websockets/orderbook-updates): snapshot/delta workflow and targeted snapshot/market changes.
- [Kalshi CF feed](https://docs.kalshi.com/websockets/cfbenchmarks-value): index IDs, availability discovery, actual tick cadence, and averaging fields.
- [Kalshi keep-alive](https://docs.kalshi.com/websockets/connection-keep-alive): server control pings and pong responses.
- [Kraken heartbeat](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/heartbeat): automatic liveness messages, separate from market updates.
- [Kraken connection guidance](https://docs.kraken.com/exchange/guides/websockets/introduction): maintenance retry spacing and connection-rate limits.

Check these contracts again when implementing; the live API may evolve. The reviewed original product scope is in `docs/project_brief.md`; existing finance and replay invariants are documented in `docs/paper_execution.md`, `docs/strategy_rules.md`, and `docs/replay_analytics.md`.

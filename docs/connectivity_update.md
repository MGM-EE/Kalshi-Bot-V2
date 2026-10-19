# Connectivity recovery update

This update changes the running application, not just its build plan. It keeps
the existing paper account format, strategy settings, credential store, and
Windows launchers. Follow the [update steps](windows_quick_start.md) to install
the latest source ZIP and rebuild the browser files.

## What changed

- **Browser recovery:** a connection attempt has an owner/generation and a finite
  deadline. A socket stuck opening, open without updates, closing, or closed
  cannot prevent a replacement. HTTP fallback requests have their own deadlines
  and ownership, including when abort is ignored. Late frames/responses cannot
  rewind a newer view. Returning from sleep, a restored network, or browser cache
  restores an immediate attempt. Display errors remain visible.
- **Kalshi readiness:** subscription ACKs and book snapshots are checked separately
  from socket activity. A live socket no longer disables fallback for a missing
  book. Protocol errors are correlated to their request/channel, so an unavailable
  reference capability does not invalidate unrelated executable books.
- **Book repair:** Kalshi verifies quiet books with fresh snapshots and repairs a
  sequence gap within the affected subscription. Failed repair escalates with
  deadlines. Normal market rollover updates subscriptions while CF stays connected.
  REST snapshots have separate book objects and generation/version checks.
- **Consistent REST pulls:** discovery, fee refresh, book/trade fallback, and assets
  have independent workers with bounded request/concurrency budgets. Slow metadata
  or one slow asset no longer serializes every other asset's book polling.
  Fallback targets two seconds and backs off on failures or server throttling.
- **Kraken recovery:** each requested USD feed has ACK/initial snapshot deadlines.
  An independent watchdog checks missing/stalled books and tickers even while other
  messages/heartbeats arrive. Quiet trade streams alone do not cause reconnects.
  A broken checksum or session requires fresh books, with immediate invalidation
  before waiting for the close handshake.
- **Backend recovery:** unexpected connector/paper/settlement worker exits restart
  with sanitized diagnostics and bounded retries. The runtime endpoint reports
  worker state/restart counts. A normal API response still does not establish that
  all exchange data is ready.
- **Dashboard cost:** tabs share one precomputed/encoded snapshot per 250 ms
  interval. Slow browser sends time out rather than retaining sessions indefinitely.
  This reduces repeated account/indicator reads as tab count grows.

## What to expect

The browser can use HTTP backup while its stream repairs. Exchange books/reference
feeds report their own status separately. Repaired data must pass integrity and
freshness checks; reconnection does not bypass trading controls or enable a paused
strategy. Missing authenticated CF access still blocks dependent paper buys.
Protective paper sells retain the existing Kalshi book/fee checks.

The launcher also retains the newer Windows Selector event-loop fix for the
reported local WinError 64 accept failure. Stop the old backend to activate the
new launcher/backend; run setup to compile the new browser transport.

This is the first implementation stage of the [rebuild blueprint](rebuild/blueprint.md).
The larger feed/engine/API process split and a 72-hour target-machine endurance
test remain future work. Synchronous financial work still shares the asyncio
process; this update reduces repeated work and repairs failures but does not claim
complete workload isolation or guaranteed upstream availability.

## Verification

Checked on October 18, 2026: **181 Python tests passed, one Windows-only skipped**;
Ruff, TypeScript typecheck/build and the deterministic JavaScript transport suite
passed. Chromium connection, overview and full dashboard checks passed, including
an isolated fresh-recording replay and zero page errors.

A 30-second real-data sample received live Kraken book/ticker/trade data for all
three assets and fresh Kalshi REST book/trade polling. The checker exited 2 because
authenticated Kalshi/CF was unavailable: this cloud runtime's configured signing-key
file is missing. This does not describe the Windows-local encrypted store. A short
follow-up observed current fee metadata for all assets. These samples establish
basic feed operation, not unattended endurance or authenticated recovery.

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check backend scripts tests
npm run build
node tests/ui_transport.cjs
.venv/bin/python scripts/live_check.py --seconds 30
```

The Python recovery tests use local HTTP/WebSocket faults and fake time. The
transport JavaScript tests exercise missing close events, ignored HTTP aborts,
old responses/callbacks, sleep/network restoration, display errors, and cleanup.
The existing financial/replay tests verify paper invariants and restart behavior.
The opt-in live check exits 2 when streaming/reference acceptance is incomplete;
public REST fallback is not authenticated streaming acceptance.

Native Windows sleep/network/DPAPI behavior and authenticated Kalshi/CF recovery
must still be checked on the intended machine with a usable signing key. Keep the
old program version with historical recordings because backend changes alter the
replay fingerprint. Existing trade/account data remains readable.

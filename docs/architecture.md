# Architecture and phase boundaries

One asyncio process owns each venue connection. FastAPI exposes read-only
HTTP state and a coalesced dashboard WebSocket at 2 Hz. Rendering does not
drive data processing. One Kraken Spot v2 connection subscribes to all three
assets' ticker, trade, and ten-level L2 book feeds. One authenticated Kalshi
connection subscribes to discovered tickers, CF Benchmarks indices, and global
lifecycle notifications. Discovery refreshes every 30 seconds and on lifecycle
events. Without Kalshi credentials, REST books and recent trades are explicitly
labeled as polling every five seconds.

- `connectors/`: transport, bounded trade history, authentication, source timestamps.
- `markets/`: conservative rule normalization, current/upcoming selection,
  complementary Kalshi books, Kraken decimal-preserving CRC32 validation.
- `state.py`: bounded current state, separate channel health, clock-drift/stale states.
- `frontend/`: small compiled TypeScript client, source-health table and expandable
  depth/rules/trades. No frontend credentials or trading actions.
- `tests/`: deterministic synthetic protocol fixtures and local WebSocket transport
  tests; `scripts/live_check.py` separately checks actual feeds.

Failed checksums, sequence gaps, and reconnects invalidate books and request new
snapshots by reconnecting. Duplicate trade IDs are bounded and ignored. Kraken
out-of-order trades are discarded; out-of-order books require a new snapshot.
Memory is bounded to 100 displayed trades, 2,000 deduplication IDs per active
market, current books, and 60 connection events. Old market buffers are removed
at rollover. No persistent trading state exists until Phase 2.

Rule parsing supports explicit 60-second CF opening/closing comparison language,
known asset index IDs, and consistent strike comparisons. Unsupported wording
stays unverified. The contract strike, current CF tick, trailing average, partial
closing average, and official settlement result are distinct values. A partial
average is not a forecast or a settlement. No forecast is produced in Phase 1.

## Remaining sequence

1. Finish authenticated live Phase 1 acceptance, then request approval.
2. Paper fills/fees, limits, SQLite portfolio durability, buy/sell/settlement tests.
3. Immutable BASELINE V1 and versioned configurable entry/exit profiles. No legacy
   filters were available in this originally empty repository; recover and document
   them before claiming full baseline equivalence.
4. Whale/liquidity persistence, volatility, chop, swing risk.
5. Full decision strip, conditions, settings, portfolio, event notifications.
6. Recording/replay, matched strategy evaluation, analytics, performance validation.

Each phase has focused tests, a status update, and a Git commit. Stop for user
approval before starting the next phase. Do not add real-money execution.

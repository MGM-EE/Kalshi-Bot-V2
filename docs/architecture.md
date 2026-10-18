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
  depth/rules/trades, decisions, settings, paper controls and replay reports.
  No credentials enter the frontend.
- `tests/`: deterministic synthetic protocol fixtures and local WebSocket transport
  tests; `scripts/live_check.py` separately checks actual feeds.

Failed checksums, sequence gaps, and reconnects invalidate books and request new
snapshots by reconnecting. Duplicate trade IDs are bounded and ignored. Kraken
out-of-order trades are discarded; out-of-order books require a new snapshot.
Memory is bounded to 100 displayed trades, 2,000 deduplication IDs per active
market, current books, and 60 connection events. Old market buffers are removed
at rollover. Phase 2 adds transactional SQLite orders, cash reservations, positions,
fills, fee accumulators, counters, liquidity debt, and official settlements.
`execution/` and `risk/` are separate from connectors; the broker makes no network
requests. A worker processes pending/passive paper orders, and a separate worker
only reads official settlement data. The dashboard exposes manual paper controls;
automatic strategies use immutable profiles and pinned position criteria.

Rule parsing supports explicit 60-second CF opening/closing comparison language,
known asset index IDs, and consistent strike comparisons. Unsupported wording
stays unverified. The contract strike, current CF tick, trailing average, partial
closing average, and official settlement result are distinct values. A partial
average is not a forecast or a settlement. No settlement forecast is produced.

## Strategy, indicators and research

`strategy/` evaluates source-aware strict/weighted conditions, confirmations,
allocation and ordered adaptive exits. Intent IDs and signal state commit together
before dispatch; restart retries the same ID. Unknown required metrics block.
Profiles pin existing position criteria; the immutable baseline contains only
rules actually recovered from the brief. No missing historical filters are invented.

`indicators/` keeps separate venue units, bounded histories, observed L2 persistence
and transparent volatility/chop/swing rule scores. Configuration versions restart
warmup. UI snapshots share the same observation clock; they do not manufacture
fresh source ticks or fill prices.

`research/` records normalized input patches and logical operation times in a
bounded gzip writer. Replays use isolated SQLite and the same trading classes.
Exact replays verify ledger/config/decision fingerprints; comparisons substitute
one profile over matched inputs. Fee-aware analytics group full position cycles,
including partial exits and official settlements. Development, validation and
out-of-sample recording periods must be selected independently by the researcher.

Each of the six implementation phases has focused tests, a status update and a
separate local commit. The user authorized continuation through the remaining
phases. Authenticated live acceptance and Windows execution remain external
checks; implementation completion does not imply either passed. See
`PROJECT_STATUS.md` for evidence. No real-money execution is implemented.

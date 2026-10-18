# Recording, replay and research

Recording is opt-in and starts before feeds and workers. Use a new paper account
for each recorded session so the recording has a complete, reproducible starting
point. Your normal account is preserved.

Windows:

```bat
scripts\start_windows.bat --record development_001
```

Linux:

```bash
.venv/bin/python scripts/launch.py --record development_001
```

This creates `data/sessions/development_001.sqlite3` and
`data/recordings/development_001.jsonl.gz`. Existing paths are never overwritten.
Advanced launches may set `RECORDING_PATH` and `PAPER_DATABASE_PATH` explicitly.
Close with Ctrl+C in the launcher console. Clean shutdown writes a final ledger
fingerprint and integrity footer. Forced termination, disk errors or queue
overflow produce an incomplete file, which replay refuses. Linux launcher
shutdown was exercised; Windows process-group/DPAPI behavior still needs Windows
acceptance. Recording exposes normalized public market data and local paper
research settings/results, never signing keys, authorization headers or passwords.

The bounded writer queue compresses JSONL on a separate thread. Market/book
changes and new trades are stored as normalized patches. Source health retains
receive/exchange times. Timer cycles, manual requests, settings/control changes,
backend account-mark cycles and read-only dashboard observations preserve their exact
ordering and logical timestamps. Recorder failures do not block paper execution.

Replay a completed recording:

```bash
.venv/bin/python scripts/replay.py data/recordings/development_001.jsonl.gz --period development --output development_001_results.json
```

Or use the dashboard's Analytics / replay section. Replay opens an in-memory
SQLite account and starts no connectors. It feeds observations into the same
Features, IndicatorEngine, StrategyEngine and PaperBroker classes as live paper
execution. Exchange timestamps retain source ages; only already received inputs
are visible at each recorded cycle. No interpolated history, future quote or
invented fill is introduced. Original replay must match the final persisted
ledger, including configuration versions, decisions and accounting. Corrupt,
out-of-sequence, oversized, incomplete or wrong-code-version recordings fail.
The code SHA-256 in the header identifies the required backend source revision;
archive that Git revision with each dataset. Restoring data files alone is not
enough if application code has changed.

For matched strategy evaluation, export profiles from the dashboard and run:

```bash
.venv/bin/python scripts/replay.py data/recordings/development_001.jsonl.gz --compare conservative.json balanced.json --period validation --output comparison.json
```

The original exact replay runs first. Each comparison receives the same observed
data, timer cycles, initial account, control enable/pause/emergency sequence,
fees and execution settings, with its selected immutable strategy substituted.
Use strategy-only recordings with fixed indicator/execution definitions. Manual
orders/cancels and mid-session definition changes are rejected for comparisons;
the original configuration can still replay those recordings exactly. Comparison
ledgers are expected to differ and are not labeled exact reproductions.

Period labels are user assigned. Keep development, validation and out-of-sample
recordings disjoint in time, archive their dataset hashes, and freeze the chosen
profile before evaluating out-of-sample data. A label alone does not prove that a
sample was held out. No adequate profitability dataset has been collected here.

## Ledger analytics

A trade is a completed position cycle, not an individual partial fill. Buys add
fee-inclusive basis; partial sells allocate basis proportionally; official final
settlement closes residual inventory without a settlement fee. Win rate includes
breakeven cycles in its denominator. Empty averages, zero-loss profit factors,
and other undefined ratios are unavailable. Entry price is the observed
quantity-weighted fill price. Cash P&L includes fees.

Reports include net expectancy, average wins/losses, profit factor, fees/slippage,
observed conservative equity drawdown and realized-path drawdown. Observed marks
cannot measure unobserved intraperiod drawdown. Backend mark cycles update peaks;
dashboard reads cannot advance account peaks or risk confirmations. Breakdowns cover asset, strategy,
entry timing, purchase price, whale/chop regime, exit reason and remaining exit
time. Missing historical observations stay unknown. Older pre-instrumentation
trades cannot acquire indicator values retroactively.

Entry/exit decision transitions persist with actual metrics and reasons. Rejected
orders, unfilled orders and confirmed-but-blocked transitions are separate from
filled trades. No hypothetical profit is assigned to a missed signal. Fill audit
records include pinned profiles, entry observations, reference/line, executable
bid/ask, fees/slippage, source ages and exit reasons. Replay exports contain the
latest 10,000 fill audit records; analytics and final ledger validation cover the
full dataset. Use the account SQLite file for larger full-detail exports.

## Performance checks

```bash
.venv/bin/python scripts/benchmark.py --events 10000
.venv/bin/python scripts/benchmark.py --events 10000 --trace-memory
```

This script is an explicitly synthetic stress fixture and never feeds the running
app. Allocation tracing substantially affects timings; compare timings with the
same instrumentation. All measurements are host/workload specific, not a service
level guarantee. Replay reports p50/p95/max for up to its first 10,000 cycles,
including the measured count and failed cycle count. Source failures remain in
the recording and cannot establish authenticated live acceptance.

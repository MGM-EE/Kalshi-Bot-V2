# PROJECT: KALSHI CRYPTO TRADING TERMINAL V2

## 1. Your role and overall objective

Act as a senior quantitative trading systems engineer, full-stack developer, and market microstructure researcher.

Build a brand-new, fully functioning **real-time Kalshi 15-minute cryptocurrency analysis and paper-trading application** using live Kalshi and Kraken Pro market data.

This must be an operational trading research system, not a simulated dashboard with randomly generated numbers.

The application should identify potential entries, execute realistic paper trades, continuously reassess positions, and determine whether to sell, hold, partially exit, or allow settlement.

The project should prioritize:

1. Reliable real-time market data.
2. Accurate calculations and market mechanics.
3. A robust, configurable entry engine.
4. An adaptive, condition-driven exit engine.
5. Large-trade and liquidity analysis, including whale activity.
6. Realistic paper execution, fees, spreads, and slippage.
7. Low CPU and memory consumption.
8. A clean, information-dense live dashboard.
9. Comprehensive trade logging and strategy evaluation.
10. A maintainable architecture that can be expanded.

**The initial application must be PAPER-TRADING ONLY.** Use real market data but never submit real-money orders. Keep future live execution isolated from the paper execution engine and disabled.

Do not assume that any strategy is profitable. We are trying to discover whether a repeatable advantage exists.

---

## 2. Target markets and integration

Support these Kalshi 15-minute cryptocurrency markets:

- BTC — Bitcoin
- ETH — Ethereum
- SOL — Solana

Design market discovery so additional supported assets can be introduced later without rewriting core logic.

### Kalshi integration

Use the official Kalshi REST API and WebSocket API to obtain:

- Current and upcoming 15-minute markets.
- Market ticker, status, opening, closing, and settlement timestamps.
- Contract rules and exact YES/NO outcome definitions.
- Relevant settlement index and opening reference values.
- Live YES and NO prices.
- Real bid/ask quotes and available order-book depth.
- Order-book snapshots and incremental updates.
- Public executions, their sizes, and available aggressor-side information.
- Market volume, liquidity, and open interest when available.
- Current CF Benchmarks reference-index values.
- Relevant rolling and final-minute reference averages.
- Market lifecycle and settlement events.

Use actual contract rules to determine which YES/NO side corresponds to UP/DOWN. Do not infer the meaning from the ticker alone.

Kalshi's CF Benchmarks feed should be treated as the primary reference for settlement-related signals when specified by the contract. Use Kraken prices as additional independent market information.

### Kraken Pro integration

Use official Kraken Spot WebSocket v2 feeds for:

- BTC/USD
- ETH/USD
- SOL/USD

Subscribe to supported real-time trade, ticker, and L2 order-book feeds.

Use Kraken's authenticated L3 feed as an optional advanced feature when access is available.

Kraken should supply information about:

- Cryptocurrency price action.
- Aggressive market buying and selling.
- Trade sizes and volumes.
- Order-book imbalance.
- Large visible orders.
- Liquidity walls.
- Momentum and volatility.
- Potential order-flow reversals.

Do not place trades on Kraken. Kraken is primarily a data source for Kalshi decisions.

The bot must connect to BOTH sources, show their live connection health, and detect if either data stream becomes unreliable.

---

## 3. Every major market variable to monitor

Implement the following indicator families as independent, testable modules. Every indicator must define its source, calculation, update frequency, units, and data-quality requirements.

Not every indicator needs to become a mandatory trading condition. Let users activate, deactivate, weight, or display each one.

### A. Kalshi market and contract variables

Monitor:

- Current UP/YES and DOWN/NO executable buying prices.
- Current executable selling prices.
- Best bids and asks.
- Bid/ask spread.
- Depth available at each relevant price level.
- Liquidity within a configurable number of cents of the best quote.
- Order-book imbalance.
- Order-book changes and liquidity withdrawal.
- Contract price velocity and acceleration.
- Recent contract price highs and lows.
- Contract volatility.
- Signed aggressive trade flow when the source supports it.
- Volume per second and rolling-window volume.
- Large contract trades and block-trade indicators.
- Net aggressive buying versus selling pressure.
- Open interest, when available.
- Time until the actual trading cutoff.
- Time until settlement.
- Contract-specific tick size and applicable fee structure.
- Current effective all-in entry price.
- Realistic estimated exit proceeds.
- Maximum quantity executable at the configured price.

Track the UP and DOWN books separately and normalize all trade direction and price calculations correctly.

### B. Settlement index and price-line variables

Monitor:

- Current CF Benchmarks index value.
- Official reference value used for the 15-minute outcome.
- Opening reference average, if available.
- Current projected closing-window average.
- Difference between the live reference and opening reference.
- Absolute distance from the line in USD.
- Percentage distance from the line.
- Distance normalized by recent realized volatility.
- Speed of approach toward the line.
- Whether the reference has recently crossed the line.
- Number of crossings within the current round.
- Time spent above versus below the line.
- Price acceleration toward or away from the line.
- Stability of price on either side.
- Potential for the required closing average to differ from the latest spot price.

Do not use the instantaneous Kraken price as the settlement value.

When constructing a projected settlement outcome, follow the exact selected market's rules, including the averaging window.

### C. Kraken market-movement variables

Calculate:

- Last traded spot price.
- Best bid and ask.
- Spot bid/ask spread.
- Recent VWAP.
- Rolling price returns.
- Price momentum across 5s, 15s, 30s, 60s, 3m, and 5m intervals.
- Momentum acceleration.
- Realized volatility across configurable windows.
- Standard deviation of returns.
- Relative volume.
- Short-term price range.
- Trend efficiency.
- Mean-reversion behavior.
- Directional consistency.
- Reversal frequency.
- Price-to-VWAP distance.
- Microstructure liquidity imbalance.
- Trade arrival intensity.
- Buy/sell trade-volume imbalance.
- Rolling cumulative volume delta (CVD).
- Order-book depth and liquidity changes.
- Slippage estimates for configurable sizes.
- Spot-to-settlement-index divergence.

Use streaming ticks for calculations rather than downloading full candle histories repeatedly.

### D. Additional contextual variables

Offer optional modules for:

- Scheduled major economic events.
- Significant market-wide volatility.
- Correlated movement between BTC, ETH, and SOL.
- Broad market momentum.
- Kraken Futures funding rates, open interest, liquidations, and CVD where separately accessible and useful.
- Abnormal divergence between Kraken and the reference index.

Label futures information as coming from a separate market, not Kraken Spot.

Never present delayed, inferred, or third-party information as direct real-time spot-exchange data.

---

## 4. Dedicated whale activity and liquidity-monitoring engine

This is a major feature.

Create a live **WHALE FLOW** section that measures unusually large trading activity on both Kraken and Kalshi.

### Whale detection

Allow multiple definitions of a whale-sized transaction:

- Fixed minimum USD notional.
- Rolling percentile of recent trade sizes.
- A configurable multiple of median trade size.
- Abnormal aggressive trading bursts.
- Large visible book orders.
- Repeated high-notional activity within a short interval.

Whale thresholds must be customizable for BTC, ETH, SOL, and Kalshi contracts independently.

### Kraken whale metrics

Track aggressive buyer and seller volume in USD, using trade-side information.

Display:

- Large aggressive buy volume.
- Large aggressive sell volume.
- Net large-trade flow in USD.
- Large-trade buy/sell ratio.
- Largest recent observed trade.
- Trade frequency.
- Buying/selling acceleration.
- Sustained pressure direction.
- Whale activity over 5s, 15s, 30s, 60s, and 5m.
- Changes in visible large bid/ask liquidity.

For example:

Net large-trade flow = large aggressive buys − large aggressive sells.

Positive values indicate net aggressive buying within the measurement window; negative values indicate net aggressive selling.

### Kalshi whale metrics

Track:

- Large UP/YES trade executions.
- Large DOWN/NO trade executions.
- Net aggressive flow by outcome.
- Large liquidity increases and decreases.
- Unusual contract-volume bursts.
- Visible order-book concentration.
- Large trades opposing the selected position.

Report Kalshi flow in contracts and USD notional as appropriate.

**Do not combine Kraken USD notional and Kalshi contract notional into one misleading number.** Display venue-specific results and a separate normalized directional agreement indicator.

### Are whales holding?

Make a section named **LARGE-ORDER PERSISTENCE**.

When order-level visibility is available, monitor:

- Large resting buy orders.
- Large resting sell orders.
- Their visible remaining quantities.
- How long individual visible orders remain.
- Whether liquidity is added, reduced, filled, or canceled.
- Whether large bid or ask walls repeatedly reappear.
- Whether aggressive trades are being absorbed at a price.

Where only aggregated L2 data exists, calculate price-level liquidity persistence instead.

Do not claim these orders reveal an individual's complete holdings. A persistent limit order is not evidence of a persistent investment position.

Use statuses such as:

- Persistent buy-side liquidity.
- Persistent sell-side liquidity.
- Large bid withdrawn.
- Large ask withdrawn.
- Aggressive buying detected.
- Aggressive selling detected.
- No strong large-order signal.
- Insufficient data.

### Are whales planning to buy?

Never claim direct knowledge of future participant intentions.

Instead implement a **BUY/SELL PRESSURE SETUP** indicator using observable conditions:

- Unusually large resting bids or asks.
- Increasing depth on one side.
- Repeated liquidity replenishment.
- Absorption of aggressive selling or buying.
- Increasing signed trade flow.
- Book imbalance.
- Order placement and cancellation behavior when L3 data permits.

Describe the output as **observed pressure or a potential setup**, not confirmed intent.

If a predictive probability is displayed, it must come from a separately trained and validated model. Otherwise display a descriptive signal score with no probability interpretation.

---

## 5. Swing risk, volatility, and choppiness engine

Create an independent **SWING / CHOP RISK** section.

The goal is to identify markets where price repeatedly crosses the target line, rapidly reverses direction, or makes entry and exit unreliable.

Track:

- Realized short-term volatility.
- Rolling high-to-low price range.
- Number of target-line crossings.
- Frequency of directional reversals.
- Trend efficiency ratio.
- Short-term versus longer-term volatility.
- Acceleration of volatility.
- Order-flow reversals.
- Kalshi quote oscillations.
- Bid/ask spread widening.
- Sudden liquidity disappearance.
- Kraken-to-reference divergence.

Calculate a configurable 0–100 **Choppiness Score** using clearly documented components.

Calculate a separate 0–100 **Swing Risk Score** measuring the danger of sudden adverse movement relative to the target line and remaining time.

Scores must be transparent, initially rule-based, and not described as calibrated probabilities.

Allow conditions such as:

- No entries if choppiness exceeds a threshold.
- Require additional confirmations during high swing risk.
- Tighten profits protection during rapid reversals.
- Disable new positions during extreme volatility.
- Apply different thresholds to BTC, ETH, and SOL.

Use hysteresis and short confirmation windows to prevent risk indicators from constantly changing states due to one tick.

---

## 6. Fully configurable entry engine

Build a settings page in which the user can adjust every buying condition without modifying source code.

Preserve my previous strategy as an immutable, selectable **BASELINE V1** profile:

- Enter only when 2:00–10:00 remain before the actual trading cutoff.
- Require all-in entry cost no greater than $0.40 per contract.
- Require price to approach the settlement line from the currently losing side.
- Respect all additional original entry filters if they can be recovered and explicitly documented.
- Do not silently change the baseline.

Create a separate fully configurable **CUSTOM V2** profile.

### Required entry settings

Implement:

**Timing**
- Earliest allowed entry, specified as time remaining.
- Latest allowed entry, specified as time remaining.
- Minimum time remaining after entry.
- Maximum allowed time waiting for a fill.
- Entry signal confirmation duration.
- Reentry cooldown.

**Price**
- Maximum contract asking price.
- Maximum all-in entry price including fees.
- Optional different price ceilings for trending and non-trending conditions.
- Minimum and maximum distance from the reference line.
- Maximum allowable spread.
- Maximum permissible slippage.
- Minimum available order-book depth.
- Maximum recent contract price acceleration.

**Direction**
- Allow UP only, DOWN only, or both.
- Require approach from the losing side, optionally.
- Require directional trend, optionally.
- Allow trend-neutral entries, optionally.
- Minimum momentum score.
- Minimum trend consistency.
- Minimum approach velocity.
- Maximum adverse spot/index divergence.

**Whale confirmation**
- Whale confirmation required/optional/disabled.
- Minimum large-trade net flow.
- Minimum order-flow imbalance.
- Minimum persistent buy-side or sell-side liquidity.
- Maximum adverse large-trade flow.
- Minimum duration of whale pressure.
- Optional veto when major whale activity opposes the trade.

**Market quality**
- Maximum Choppiness Score.
- Maximum Swing Risk Score.
- Maximum realized volatility.
- Minimum liquidity.
- Maximum data age.
- Minimum number of valid market observations.
- Optional cross-market confirmation.

**Risk and allocation**
- Starting paper balance.
- Maximum USD per entry.
- Maximum USD exposure per 15-minute round.
- Maximum USD exposure per cryptocurrency.
- Maximum total portfolio exposure.
- Maximum number of entries per round.
- Maximum completed trades per round.
- Maximum simultaneous positions.
- Maximum paper loss per trade.
- Maximum paper loss per round.
- Maximum daily drawdown.
- Maximum consecutive losses before pausing.
- Fixed-dollar or percentage-based position sizing.
- Minimum and maximum contract quantity.

Clearly distinguish *entry count*, *round-trip count*, and *open positions*.

### Entry logic

Support two configurable evaluation modes:

**STRICT MODE:** All enabled required entry conditions must pass.

**WEIGHTED MODE:** Safety and execution constraints must pass, followed by a configurable weighted signal score.

Hard safety rules must never be bypassed by a weighted score.

Every entry must have an understandable reason.

Example:

"BUY UP — reference approaching line from below, positive spot order flow, favorable large-trade imbalance, acceptable volatility, executable ask within price cap."

Do not execute solely because a colored UI appears green. The trading engine must independently validate all relevant conditions and verify the final paper fill.

---

## 7. Adaptive and highly configurable sell engine

This is equally important to entry.

I do not want the bot automatically selling at an arbitrary fixed percentage every time.

It should assess current profit, the remaining upside, adverse movement, whale activity, liquidity, and the probability of losing an existing favorable position.

However, it must use transparent, validated logic—not intuition disguised as artificial intelligence.

### Adjustable exit conditions

Implement:

- Minimum time in position.
- Maximum time in position.
- Earliest normal exit time.
- Latest normal exit time.
- Profit-taking target price, default 85 cents.
- Minimum net USD profit.
- Minimum net percentage return.
- Maximum acceptable loss.
- Trailing stop based on contract price.
- Trailing stop based on achievable net profit.
- Profit-protection trigger.
- Minimum profit that must be protected after trigger.
- Emergency risk exit.
- Whale-reversal exit.
- Order-flow reversal exit.
- Trend-break exit.
- Choppiness-related exit.
- Reference-line crossing exit.
- Liquidity deterioration exit.
- Partial profit-taking.
- Optional hold-to-settlement mode.

Every threshold must be configurable.

### Example adaptive behavior

If a contract was purchased for $0.38 and can now be sold for $0.85:

Calculate the actual estimated proceeds, entry cost, applicable exit fees, and net profit.

Do not assume the position is worth 85 cents merely because the last traded price is 85 cents. Use the executable bid and available depth.

Then assess:

- Is whale buying pressure still supporting the direction?
- Is major adverse whale selling appearing?
- Is the underlying moving away from or toward the target line?
- Is the order book weakening?
- Is the market becoming choppy?
- Is the current profit large relative to the remaining upside?
- How much time remains?
- How severe would a reversal be?

Possible decisions:

- **SELL ALL** — profit target achieved with meaningful reversal risk.
- **SELL PARTIAL** — secure some profits while preserving limited exposure.
- **HOLD** — favorable conditions remain and holding is permitted.
- **PROTECT PROFIT** — tighten the trailing exit condition.
- **CUT LOSS** — adverse conditions satisfy an enabled protective rule.
- **HOLD TO SETTLEMENT** — explicitly enabled and valid for the position.

### Exit priority

Implement a deterministic priority order:

1. Emergency shutdown or invalid position state.
2. Market status and settlement/trading-cutoff restrictions.
3. Hard risk/exposure safeguards.
4. Protective stops and emergency reversals.
5. Profit-protection conditions.
6. Normal profit-taking conditions.
7. Optional evidence-backed hold-versus-sell comparison.

A signal must never override a higher-priority safety constraint.

When a normal trading exit becomes impossible because the market has closed, transition the position to settlement processing rather than claiming it was sold.

If a hold-versus-sell expectancy model is included, it must use probabilities established through appropriate out-of-sample calibration. When a reliable model does not exist, use transparent rule-based exits.

**A profitable position is not necessarily safe, and waiting for 100 cents can turn a profitable trade into a total loss.**

---

## 8. Realistic paper execution

Paper trading must simulate actual Kalshi execution as closely as practical.

Requirements:

- Use real order-book asks for buying and bids for selling.
- Walk available depth when an order is larger than top-of-book liquidity.
- Include applicable series-specific fees and current exchange rounding rules.
- Include both entry and exit fees.
- Simulate partial fills.
- Model order latency.
- Support configurable conservative slippage.
- Distinguish immediate fills from resting limit orders.
- Model resting-order queue uncertainty conservatively.
- Avoid assuming a trade filled simply because a quoted price was touched.
- Handle cancellations and orders that never fill.
- Update position quantities only after simulated fills.
- Deduct capital when exposure is opened.
- Restore proceeds when a position is sold.
- Account separately for settlement outcomes.
- Maintain exact decimal precision for money and contract prices.
- Reject orders that exceed available paper cash or risk limits.

Include both:

**MARKETABLE PAPER EXECUTION:** Estimated fills against available order-book depth.

**PASSIVE PAPER EXECUTION:** A conservative model for resting limit orders, with uncertain fills explicitly acknowledged.

Allow multiple buy/sell cycles during a single 15-minute round subject to the configured limits.

Support paper trading across BTC, ETH, and SOL independently without mixing position accounting.

Implement a persistent state store so restarting the application does not duplicate entries, reset trade counters, or invent new balances.

---

## 9. Compact dashboard and top trading-decision rectangle

The UI must prioritize speed, visibility, and minimal resource use.

Build a compact, dark-mode terminal with a single large horizontal **DECISION STRIP** at the very top.

It should span the full available width and remain visible while scrolling.

Aim for a compact desktop display, ideally around 160–220 pixels tall for the main strip at common desktop resolutions.

### Example visual structure

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ BTC 15M  | PAPER LIVE | 06:42 LEFT | KALSHI ● | KRAKEN ● | INDEX ● | RUNNING │
├──────────────────────────────────────────────────────────────────────────────┤
│ INDEX/LINE  │ UP  ASK/BID │ DOWN ASK/BID │ WHALE NET │ SWING │ CHOP │ P&L   │
├──────────────────────────────────────────────────────────────────────────────┤
│ BUY CONDITIONS                                                          7/9 │
│ TIME 🟢 PRICE 🟢 TREND 🟢 LINE 🟡 FLOW 🟢 WHALES 🔴 SPREAD 🟢 RISK 🟢 DEPTH 🟢 │
├──────────────────────────────────────────────────────────────────────────────┤
│ SELL CONDITIONS                                                         3/7 │
│ PROFIT 🟡 TARGET 🔴 REVERSAL 🔴 WHALES 🟢 TRAIL 🟡 TIME 🟢 LIQUIDITY 🟢       │
├──────────────────────────────────────────────────────────────────────────────┤
│ CURRENT DECISION: WAIT — LARGE-TRADE CONFIRMATION MISSING                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

The exact spacing and number of tiles can be adjusted for readability.

### Color semantics

Every enabled entry or exit condition must have an explicit state:

**RED**
- Condition is not satisfied.
- Outside allowed range.
- Blocking entry or exit.

**YELLOW**
- Condition is approaching its configured threshold.
- A threshold is temporarily satisfied but confirmation time has not elapsed.
- The condition is almost eligible.

**GREEN**
- Condition is met and confirmed.

**GRAY**
- Condition disabled, unavailable, or impossible to evaluate.
- Unknown critical data must block automatic entries; do not treat unknown as green.

Make the yellow tolerance configurable per condition.

For example, a maximum contract price of $0.40 could use a configurable $0.03 warning band. Prices from $0.41 to $0.43 might show yellow, while a qualifying executable cost of $0.40 or lower shows green.

Apply threshold direction correctly to each metric.

### Trading-event screen flash

When ALL mandatory entry conditions are met and an actual paper fill is confirmed:

- Briefly flash the top decision strip green.
- Show "PAPER BUY — UP" or "PAPER BUY — DOWN".
- Show the coin, quantity, actual simulated fill price, and total cost.
- Save the event in the log.
- Optionally play a short notification.

When a sell is confirmed:

- Flash the strip in a distinct exit color.
- Show "PAPER SELL — UP" or "PAPER SELL — DOWN".
- Show quantity, price, realized net P&L, and exit reason.

A qualifying signal that does not fill must display "ENTRY SIGNAL — NOT FILLED," not "BOUGHT."

Keep flashes brief, non-strobing, and user-disableable.

Do not flash repeatedly while the same signal remains active. Use event-based notifications.

### Individual metric tiles

A tile should be expandable or hoverable to display:

- Actual value.
- Required threshold.
- Signal status.
- Source.
- Last update age.
- Recent trend.
- Reason for failure or success.

Use small, readable typography and avoid unnecessary animation.

---

## 10. Additional dashboard sections

Below the top rectangle, create a few compact expandable sections.

### Market and execution

Display the selected asset, market ticker, official index, opening reference, current distance from the line, prices, available liquidity, spread, and paper position.

Include a compact lightweight sparkline for price and Kalshi contract movement.

### Whale activity

Show Kraken large-trade net flow prominently, for example:

- Buy volume: $1.8M
- Sell volume: $1.2M
- Net flow: +$600K
- Pressure: BUY-DOMINANT

These are illustrative UI values only, not real market data.

Provide separate Kalshi whale-flow statistics.

Include indicators for persistent large bids, large asks, liquidity withdrawal, and potential absorption.

### Swing and chop

Show both risk scores, reversal count, target-line crossing count, volatility regime, and trend efficiency.

### Entry and exit conditions

Show each configurable rule with its live status, numerical threshold, and actual observed value.

Separate entry conditions from exit conditions.

### Paper portfolio

Display:

- Starting paper balance.
- Available paper cash.
- Capital currently committed.
- Open positions.
- Realized and unrealized P&L.
- Daily P&L.
- All-time P&L.
- Return after estimated fees.
- Current round entry count.
- Current round completed trade count.
- Current exposure.
- Maximum permitted exposure.

### Event feed

Show a small rolling log containing:

- Market connectivity changes.
- New entry signals.
- Failed entry conditions.
- Paper orders submitted.
- Simulated fills and partial fills.
- Whale pressure reversals.
- Risk warnings.
- Sell decisions.
- Settlement results.

Keep detailed diagnostics in a separate tab instead of overwhelming the main screen.

---

## 11. Configurable settings and strategy presets

Create a settings UI that allows changing the strategy without writing code.

Group settings under:

- Markets.
- Data sources.
- Buy conditions.
- Sell conditions.
- Whale detection.
- Trend and volatility.
- Choppiness and swing risk.
- Position sizing.
- Risk limits.
- Paper execution.
- Dashboard and alerts.

Provide explanatory tooltips with units and examples.

Support named strategy profiles:

- BASELINE V1 — original fixed research strategy.
- CONSERVATIVE — strict, high-confirmation research preset.
- BALANCED — moderate confirmation research preset.
- EXPERIMENTAL — independently configurable research preset.
- CUSTOM V2 — fully user-controlled.

These presets are starting research configurations, not profitability claims.

Allow JSON export/import of custom profiles.

Validate settings before applying them.

Changes that affect open trades must have explicit behavior. Do not silently rewrite the original entry or exit criteria of existing positions.

Version every strategy configuration and store its identifier on each trade.

---

## 12. Analytics, testing, and research

I need to know whether the strategy actually works.

For every paper trade, record:

- Date and time.
- Asset and market ticker.
- Direction and YES/NO mapping.
- Strategy profile and version.
- Entry signals and numerical values.
- Whale-flow readings.
- Trend and volatility readings.
- Choppiness and swing scores.
- Reference-index values.
- Entry bid/ask and fill price.
- Entry fees and slippage.
- Quantity.
- Exit conditions and decision reason.
- Exit executable price and fill price.
- Exit fees.
- Net realized P&L.
- Whether the position settled.
- Actual settlement outcome.
- Market-data latency and freshness.

Create an analytics page showing:

- Total trades.
- Win rate.
- Average win and loss.
- Net expectancy per trade.
- Profit factor.
- Maximum drawdown.
- Fee burden.
- Estimated execution slippage.
- Performance by asset.
- Performance by entry timing.
- Performance by contract purchase price.
- Performance by whale-flow regime.
- Performance by trend/choppiness regime.
- Performance by exit reason.
- Performance by remaining time at exit.

Track missed opportunities and rejected entries separately, without inventing fills.

### Replay and backtesting

Record market data in a compact, timestamped format.

Provide deterministic replay that feeds historical recorded events into the SAME trading logic used during live paper trading.

Prevent look-ahead bias. Signals must use only data known at that event's timestamp.

Compare strategy versions over matched data and account for fees and execution differences.

Separate development, validation, and out-of-sample evaluation periods.

Do not add complex machine learning until sufficient reliable data has been collected.

---

## 13. Application architecture

Prefer a practical, low-resource Windows-compatible implementation.

Suggested architecture:

**Backend**
- Python 3.12+.
- Asyncio-based streaming market-data services.
- FastAPI for local application services and dashboard state.
- Typed models and configuration validation.
- Decimal arithmetic for monetary calculations.
- SQLite for paper trades, settings, and event history.
- Bounded memory buffers for high-frequency updates.
- Separate modules for market data, indicators, strategy, risk, execution, and persistence.

**Frontend**
- Lightweight TypeScript UI.
- Compact responsive layout.
- Minimal dependencies and animations.
- Efficient incremental updates rather than full-page rerendering.
- No heavy charts unless they clearly improve trading decisions.

Build a one-click Windows launcher that starts the backend, opens the local dashboard, and cleanly shuts down application processes.

Keep the trading engine running independently of UI rendering.

### Recommended project structure

```text
kalshi-terminal-v2/
  backend/
    app/
      connectors/
        kalshi.py
        kraken.py
        benchmark.py
      markets/
        discovery.py
        normalization.py
        orderbook.py
        lifecycle.py
      indicators/
        momentum.py
        volatility.py
        whale_flow.py
        choppiness.py
        swing_risk.py
      strategy/
        baseline_v1.py
        entry.py
        exit.py
        conditions.py
        profiles.py
      execution/
        paper_broker.py
        fill_model.py
        fees.py
        portfolio.py
      risk/
        limits.py
        circuit_breakers.py
      storage/
        database.py
        recorder.py
        replay.py
      api/
        routes.py
        dashboard_stream.py
      main.py
  frontend/
    src/
      components/
      pages/
      state/
  tests/
    fixtures/
    integration/
    replay/
  scripts/
    setup_windows.ps1
    start_windows.bat
    self_test.ps1
  config/
    defaults.json
    example.env
  docs/
    architecture.md
    indicator_definitions.md
    api_sources.md
    setup.md
    strategy_rules.md
  README.md
```

Use clear interfaces so any connector or indicator can be tested independently.

Use environment variables or secure local storage for credentials. Never hardcode, commit, log, or expose API credentials to the frontend.

---

## 14. Performance, reliability, and security requirements

This application must be efficient enough to run on a typical Windows laptop without significant CPU overhead.

Requirements:

- Prefer WebSockets over repeated REST polling for live data.
- Use one appropriate subscription per feed rather than opening redundant connections.
- Aggregate indicators on rolling event windows.
- Batch database writes when safe.
- Avoid recalculating all indicator history after each tick.
- Coalesce frequent events before sending dashboard updates.
- Refresh UI metrics at a configurable frequency, initially 2–4 times per second.
- Keep execution and safety logic event-driven and independent of the slower UI refresh.
- Automatically reconnect disconnected streams with exponential backoff.
- Recover missing snapshots after sequence gaps.
- Validate available order-book checksums where supported.
- Track exchange timestamps, local receive timestamps, and data age.
- Detect substantial clock drift.
- Detect duplicate and out-of-order events.
- Persist critical state before and after fills.
- Fail safely when market data becomes stale.
- Avoid repeated trades from duplicate signals.
- Stop new entries when critical price, order-book, settlement, or contract-rule information is unavailable.

A bad connection must not cause a false green trading signal.

Provide three clearly visible operational states:

- LIVE DATA — PAPER TRADING.
- REPLAY MODE.
- DISCONNECTED / PAUSED.

Never silently substitute mock data when a live connection fails.

---

## 15. Development sequence

Do not try to build all optional indicators before establishing a functioning trading application.

Implement in this order:

**Phase 1 — Real integrations**

Connect to Kalshi and Kraken, discover current 15-minute markets, validate outcome rules, obtain real-time quotes, underlying references, trade feeds, and order books.

Deliver a functional minimal dashboard showing authentic streaming values and source health.

**Phase 2 — Paper trading engine**

Implement exact fees, executable price modeling, realistic fills, risk controls, portfolio accounting, and persistence.

Verify paper buy, sell, and settlement behavior.

**Phase 3 — Entry and exit rules**

Implement baseline entry rules, configurable custom conditions, the adaptive exit framework, multiple round trips per market, and strategy configuration.

**Phase 4 — Whale and risk analysis**

Implement Kraken large-trade flow, Kalshi large-trade flow, order-book liquidity indicators, persistent-order monitoring where available, volatility, and choppiness.

**Phase 5 — Final dashboard**

Build the full decision strip, condition tiles, real-time transitions, buy/sell confirmation flashes, settings, and paper portfolio statistics.

**Phase 6 — Replay and validation**

Implement event recording, reproducible replay, automated tests, strategy comparisons, and performance reporting.

At the end of each phase, run the relevant tests and verify the completed functionality using actual market data when available.

Do not mark a phase complete merely because the code compiles.

---

## 16. Required automated tests

Include tests for:

1. Correct Kalshi market identification and YES/NO to UP/DOWN mapping.
2. Correct distinction between opening reference, current spot price, and settlement calculation.
3. Accurate order-book reconstruction.
4. Correct bid versus ask usage.
5. Kraken trade aggressor-side calculations.
6. Whale net-flow calculations.
7. Order-book liquidity persistence.
8. Choppiness and swing-risk indicators.
9. Entry-window boundaries.
10. All-in entry cost versus price caps.
11. Partial fills and insufficient liquidity.
12. Correct fee rounding.
13. Entry and exit condition precedence.
14. Multiple entries within one 15-minute market.
15. Maximum exposure, drawdown, and trade limits.
16. No duplicate orders after reconnect or restart.
17. Persistent portfolio state.
18. No fills after the trading cutoff.
19. Correct handling of actual settlement.
20. Stale data, out-of-order messages, and lost connections.
21. Accurate colored-condition transitions.
22. No buy/sell confirmation flash before a confirmed simulated fill.
23. Consistent results between recorded replay and paper execution.

Use deterministic fixtures and mock streams for repeatable tests, plus separate live connectivity checks.

---

## 17. Definition of done

The project is complete only when:

- The application launches successfully from Windows.
- It connects to actual Kalshi and Kraken live-data services.
- Both sources display genuine incoming prices and timestamps.
- It discovers and rolls into new 15-minute markets automatically.
- The correct market settlement reference is used.
- The live top decision strip displays accurate conditions.
- Whale metrics are computed from genuine public market activity.
- Swing risk and choppiness react to incoming data.
- All strategy conditions can be edited and saved.
- The bot paper-buys when a complete eligible entry signal produces a valid simulated fill.
- The bot paper-sells or settles positions according to valid exit rules.
- It supports configurable multiple trades per round.
- Paper balance and positions remain correct across restarts.
- Fees, spreads, and slippage are reflected in P&L.
- Trade reasons and indicator values are recorded.
- Replay tests work without future information leakage.
- Automated tests pass.
- No real-money order can accidentally be placed.
- The application remains responsive and computationally lightweight.

### Final instructions to Codex

Before writing code, inspect the working directory and any existing repository files. Treat this as a new V2 application; do not blindly inherit old bot code.

Confirm the supported API capabilities against current official documentation.

Create a concise implementation plan and architecture, then begin implementing the project in phases.

Use clean and readable code, minimal but meaningful comments, and robust error handling.

**Most importantly: build functioning connections and realistic paper trading before polishing visuals or creating speculative trading indicators.**

Make real, runnable source files, installation instructions, tests, and launch scripts. Do not stop at a conceptual design or UI mockup.

The ultimate objective is a trustworthy, customizable quantitative research terminal that helps determine whether specific Kalshi 15-minute cryptocurrency strategies remain profitable after realistic trading costs and risks.

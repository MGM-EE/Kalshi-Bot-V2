# Phase 3 strategy rules

Automatic paper entries are paused on a new database. Select a profile/version
and explicitly enable them in the strategy section. Real-money execution remains
absent. Pause cancels pending automatic buys while pinned exits keep managing
existing automatic positions. Emergency blocks/cancels all paper buys and attempts
exits when executable; closed markets wait for settlement. Clear emergency to
permit entries again. Source/rule/fee/risk checks still gate every fill.

## Profiles and baseline

Profiles are immutable versions in SQLite. JSON import/export and a validated
editor are available without source changes. Save a higher version to change
settings, then select/enable it explicitly. Existing positions retain the full
entry profile and exit criteria; selecting a new version does not rewrite them.
Manual positions are managed manually unless emergency exit is requested.

BASELINE V1 is immutable and contains the recovered entry rules: inclusive
120–600 seconds before the actual trading cutoff, all-in cost at most $0.40 per
contract, and CF reference approaching the opening line from the selected
currently losing side. Direction comes from contract rules. A flat reference
does not qualify as approaching. Additional original filters/exit rules could not
be recovered from the originally empty repository. The description says so;
shared execution/risk constraints and explicitly documented research exit defaults
still apply. This is not claimed to reproduce an unavailable complete V1 bot.

CUSTOM V2, CONSERVATIVE, BALANCED, and EXPERIMENTAL are editable research starting
points, not profitability claims. Profiles include timing/confirmation/cooldown,
fill timeout, price/spread/depth/slippage caps, optional trend-regime price caps,
allowed directions, rule weights and thresholds, sizing, hard risk limits, and
exit settings. Fixed-dollar and percentage-of-available-cash sizing include fees
and current usable depth. Global hard broker limits remain an upper bound.

Rules support minimum, maximum, and inclusive between comparisons, per-rule
confirmation durations and yellow warning bands. Catalog metadata describes units
and sources. STRICT requires all enabled required rules. WEIGHTED requires those
same mandatory constraints plus the configured descriptive score. Disabled/unknown
rules are gray; missing required inputs block entries. Scores are not probabilities.
Whale/chop/swing fields remain unavailable until their Phase 4 modules supply data.

CF strike distance/approach and Kraken returns are calculated from bounded,
timestamped observed ticks; no fabricated warmup or repeated candle downloads.
Signal confirmation uses advancing source timestamps and resets across data gaps.
Paper orders revalidate fresh rules after latency. Actual fills separately enforce
the all-in cap, including rounding fees on small partial quantities.

## Exit priority

1. Invalid position/emergency state. No sale is invented without an executable book.
2. Trading cutoff or paused market; closed positions transition to settlement.
3. Hard exposure/drawdown safeguards and maximum loss.
4. Enabled adverse whale/flow/trend, reference crossing, chop, or liquidity triggers.
5. Executable-price/net-profit trailing stops and activated profit protection.
6. Configured holding/normal timing and fee-adjusted target rules.
7. Hold while no enabled trigger applies. No uncalibrated expectancy model is used.

Default target is $0.85 executable bid, with net USD/return thresholds and reversal
confirmation. Reaching 85 cents alone does not force a sale. Partial profit-taking
is applied once; residual exposure remains governed by protective/time rules.
Cycle P&L includes realized partial exits so a partial fill does not create an
artificial trailing-profit drop. Explicit hold-to-settlement skips normal targets
and timers but retains protective safeguards. Emergency/risk rules override normal
minimum holding times. Every exit records its reason and original profile version.

## Durability and evaluation

Strategy confirmation/latch state and pending intents commit together before
submission. Intent recovery reuses the identical client ID/body. Completed cycles
and cooldown permit multiple round trips while broker limits distinguish entries,
completed round trips, and open positions. Pausing/changing selection cancels
unfilled automatic entries. No automatic fill or buy/sell flash is inferred from
a condition color or submitted order.

The worker evaluates strategy state at up to 4 Hz, with immediate broker-side
rechecks before actual paper fills. Bounded histories and batched state commits
keep the work limited. Strategy/profile endpoints are local and use the same
origin/host protections as paper-order controls. See PROJECT_STATUS.md for test
results and remaining authenticated-live/Windows acceptance requirements.

## Recovery and confirmed exits

Entry previews require the broker's mandatory source/readiness checks before an
intent is created. Unsupported or stale quotes remain unavailable without
stopping exits for other assets. A recovered feed can qualify a new entry.

An adverse reference crossing remains active while the observed reference stays
on the adverse side. This survives intent submission, cancellation and restart;
a recovered favorable reference clears it, including during fill revalidation.

Only a confirmed strategy partial-target fill consumes the partial-target flag.
Manual reductions and cancelled/unfilled orders do not consume that target for
new position cycles. Older positions without the new flag retain legacy runtime
handling. Automatic exits split accumulated holdings into valid per-order sizes;
protective residual exits continue on fresh books until filled or cutoff.

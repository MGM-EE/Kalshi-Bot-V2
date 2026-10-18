# Dashboard and settings

Start the local app and open its loopback URL. The sticky decision strip is 160px
at a 1440px desktop width, with bounded scrolling for narrower layouts. Select an
asset and YES/NO outcome; the verified UP/DOWN mapping is displayed separately.
LIVE DATA — PAPER TRADING means at least one external channel is live. Each source
has its own status; this label does not imply full trading readiness. PAUSED means
new automatic entries are paused. Pinned protective exits continue.

Entry tiles show configured signal rules and hard time/price/spread/depth gates.
Gray means disabled/unavailable, red failed, yellow near/confirming, green passed.
Hover for actual values, units, thresholds, source and reason; expand diagnostics
for source ages. Exit tiles describe independent evidence: priority and combined
requirements still come from the displayed exit decision, not a count of green
exit tiles. No position means no exit signal. Rule scores are not probabilities.

Only new durable fill IDs produce PAPER BUY / PAPER SELL notifications. Initial
history, repeated snapshots and qualifying unfilled signals never flash. Disable
flashes in Dashboard and alerts. Reduced-motion preferences disable animation.
Notifications have no sound. The research log preserves confirmed fill details.

Expand market, venue-separated whale, risk, portfolio and source diagnostics as
needed. Sparklines are bounded observed dashboard samples, with gaps when sources
are unavailable. They are not stored historical candles or settlement forecasts.

Use the field editor to copy a profile, adjust buy/sell/sizing/risk fields and save
an increased version. BASELINE V1 is immutable. Select and enable the saved version
explicitly. Open positions retain their original entry/exit profile. JSON
import/export remains available for research sharing (no credentials).

Indicator settings require an increased version and restart warmup. Changing
future observations for existing positions requires the acknowledgement checkbox.
Execution/global risk settings also use increasing versions and persist in the
local SQLite account; changes require a flat account and no pending orders.
Starting balance and cash precision cannot be changed on an existing account.
Server validation remains authoritative; errors appear next to the settings.

Credentials are configured through the native PC helper, never a browser upload.
See [local credential setup](local_credentials.md).

Optional browser check: with Playwright available, Chromium installed and a local
app running, run `node tests/ui_smoke.cjs`. `CHROMIUM_PATH` and `TERMINAL_URL` can
override its defaults. This check injects explicitly synthetic wire fixtures only
inside the test browser; it cannot place a live or paper order.

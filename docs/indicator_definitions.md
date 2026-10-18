# Observed indicators

All values describe the subscribed public market data. None represents a trader's
holdings, intentions, a prediction, or a probability. Decimal monetary values are
serialized as strings. Immutable indicator versions are stored in SQLite.

| Indicator | Source and calculation | Quality / units |
|---|---|---|
| Large executions | Kraken aggressor trades; Kalshi explicit taker YES/NO exposure, excluding block prints. Fixed size OR strictly above prior-sample percentile OR median multiple; optional Kraken one-second USD burst | Kraken USD/coins and Kalshi USD/contracts remain separate. Prior history excludes the classified trade. Unknown aggression is counted separately |
| Flow windows | 5, 15, 30, 60, 300 seconds: buy/sell/net, ratio, intensity, CVD, VWAP, largest observed execution; last 5s net rate versus preceding 5s rate for acceleration | Bounded buffers; incomplete on warmup, capacity loss, disconnect/mode transition. Sampled Kalshi REST flow is always incomplete. No zero-denominator ratios |
| Persistent large liquidity | Kraken price × visible aggregated quantity; Kalshi visible contracts; track first/last continuously observed level and replenishments | Three-second default observation gap resets persistence. Leaving visible depth has unknown cause; no claim that orders were cancelled |
| Spot statistics | Observed last price per second; log-return realized volatility (not annualized), sample return standard deviation, range, momentum, path efficiency, reversals | At least six observed seconds, fresh within 3s, no internal gap >3s. Longer momentum requires actual coverage; no interpolation |
| Chop | Weighted normalized CF line crossings/6, spot path inefficiency, reversal fraction, short/long volatility acceleration | 0–100 rule score, configurable weights summing to 100; needs fresh observed index and spot statistics |
| Swing | Weighted line proximity relative to spot volatility × square-root remaining-time scale, extreme volatility ratio, spread/10c, adverse 5s momentum/20bp | 0–100 rule score, separate outcomes; needs verified mapping, current index, book and spot history |
| Pressure/setup | 50 + 50 × (0.5 signed large USD flow fraction + 0.3 L2 quantity imbalance + 0.2 signed wall persistence/30s) | Observed pressure only, with explicit window completeness. Replenishment against CVD is labeled potential absorption |

Flow confirmation duration advances on exchange trade timestamps. Chop/swing
states use configurable confirmation and high-state hysteresis. Unknown resets
confirmation. Strategy-required unavailable metrics remain blocking; venue flows
are never added together. Directional agreement compares their separate signs.

The engine observes normalized feed events; the dashboard samples at 2Hz and
indicator snapshots are cached for at most 250ms. High-frequency trade insertion
uses bounded rolling buffers. Config changes increment versions, reset warmup,
and require explicit acknowledgement while positions are open, since future
observations change. Existing position entry/exit profiles remain pinned.

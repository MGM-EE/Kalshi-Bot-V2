from decimal import Decimal


def choppiness(stats, crossings, short_volatility, long_volatility, weights):
    if stats is None:
        return None
    crossing = min(Decimal(1), Decimal(crossings) / 6)
    inefficiency = 1 - stats["trend_efficiency"]
    reversals = min(Decimal(1), Decimal(stats["reversals"]) / max(1, stats["observations"] - 2))
    ratio = short_volatility / long_volatility if long_volatility else Decimal(0)
    instability = min(Decimal(1), max(Decimal(0), ratio - Decimal(".5")) * 2)
    components = [crossing, inefficiency, reversals, instability]
    return {
        "score": sum((c * w for c, w in zip(components, weights)), Decimal(0)),
        "components": dict(
            zip(
                ("crossings", "inefficiency", "reversal_rate", "volatility_acceleration"),
                components,
            )
        ),
        "interpretation": "Rule score 0–100, not a probability",
    }


class ConfirmedRisk:
    def __init__(self, high=65, hysteresis=10, seconds=2):
        self.high, self.hysteresis, self.seconds = high, hysteresis, seconds
        self.state, self.candidate, self.since = "INSUFFICIENT DATA", None, None

    def update(self, score, ts):
        if score is None:
            self.state, self.candidate, self.since = "INSUFFICIENT DATA", None, None
            return self.state
        threshold = self.high - self.hysteresis if self.state == "HIGH" else self.high
        target = "HIGH" if score >= threshold else "CAUTION" if score >= 40 else "LOW"
        if target != self.candidate:
            self.candidate, self.since = target, ts
        if ts - self.since >= self.seconds:
            self.state = target
        return self.state if self.state != "INSUFFICIENT DATA" else "CONFIRMING"

from decimal import Decimal


def swing_risk(
    distance, index, remaining, stats, extreme_volatility, spread, adverse_momentum, weights
):
    if distance is None or index is None or stats is None or remaining < 0:
        return None
    volatility = stats["realized_volatility_percent"]
    sigma = index * volatility / 100
    proximity = (
        Decimal(1) / (1 + distance / sigma)
        if sigma > 0
        else Decimal(1)
        if distance == 0
        else Decimal(0)
    )
    time_scale = min(Decimal(1), (Decimal(str(max(1, remaining))) / 900).sqrt())
    components = [
        proximity * time_scale,
        min(Decimal(1), volatility / extreme_volatility),
        min(Decimal(1), spread / Decimal(".10")),
        min(Decimal(1), max(Decimal(0), adverse_momentum) / 20),
    ]
    return {
        "score": sum((c * w for c, w in zip(components, weights)), Decimal(0)),
        "components": dict(
            zip(("line_proximity_and_time", "volatility", "spread", "adverse_momentum"), components)
        ),
        "interpretation": "Rule score 0–100, not a probability",
    }

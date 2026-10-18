import math
from decimal import Decimal


def price_statistics(points, now, window=60):
    # One observed last price per second; no interpolation across missing ticks.
    buckets = {}
    for ts, price in points:
        if 0 <= now - ts <= window:
            buckets[int(ts)] = (ts, float(price))
    rows = list(buckets.values())
    if (
        len(rows) < 6
        or now - rows[-1][0] > 3
        or any(p <= 0 or not math.isfinite(p) for _, p in rows)
        or any(b[0] - a[0] > 3 for a, b in zip(rows, rows[1:]))
    ):
        return None
    prices = [p for _, p in rows]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    average = sum(returns) / len(returns)
    variance = (
        sum((r - average) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0
    )
    changes = [b - a for a, b in zip(prices, prices[1:]) if b != a]
    variation = sum(abs(c) for c in changes)
    efficiency = abs(prices[-1] - prices[0]) / variation if variation else 1
    reversals = sum(a * b < 0 for a, b in zip(changes, changes[1:]))

    def d(value):
        return Decimal(str(round(value, 10)))

    momentum = {}
    for seconds in (5, 15, 30, 60, 180, 300):
        candidates = [(ts, p) for ts, p in points if 0 <= now - ts <= seconds]
        covered = (
            candidates
            and now - candidates[0][0] >= seconds - 1
            and all(b[0] - a[0] <= 3 for a, b in zip(candidates, candidates[1:]))
        )
        momentum[str(seconds)] = (
            d((float(candidates[-1][1]) / float(candidates[0][1]) - 1) * 100)
            if covered and len(candidates) >= 2
            else None
        )
    return {
        "realized_volatility_percent": d(math.sqrt(sum(r * r for r in returns)) * 100),
        "return_standard_deviation_percent": d(math.sqrt(variance) * 100),
        "range_usd": d(max(prices) - min(prices)),
        "trend_efficiency": d(efficiency),
        "reversals": reversals,
        "observations": len(rows),
        "elapsed_seconds": rows[-1][0] - rows[0][0],
        "momentum_percent": momentum,
    }

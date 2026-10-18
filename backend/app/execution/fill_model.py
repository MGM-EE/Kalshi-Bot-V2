"""Depth walking and conservative passive queue estimates; no network operations."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ..markets.orderbook import number

CONTRACT = Decimal("0.01")


def on_grid(price, ranges):
    price = number(price)
    if any(number(b["step"]) <= 0 for b in ranges):
        raise ValueError("Invalid tick grid")
    return any(
        number(b["start"]) <= price <= number(b["end"])
        and (price - number(b["start"])) % number(b["step"]) == 0
        for b in ranges
    )


def adverse_price(price, action, amount, ranges):
    target = number(price) + number(amount) * (1 if action == "buy" else -1)
    candidates = []
    for band in ranges:
        start, end, step = (number(band[k]) for k in ("start", "end", "step"))
        if step <= 0:
            raise ValueError("Invalid tick grid")
        rounding = ROUND_CEILING if action == "buy" else ROUND_FLOOR
        anchor = max(target, start) if action == "buy" else min(target, end)
        result = start + ((anchor - start) / step).to_integral_value(rounding=rounding) * step
        if start <= result <= end and 0 < result < 1:
            candidates.append(result)
    return (min(candidates) if action == "buy" else max(candidates)) if candidates else None


def book_levels(book, outcome, action):
    if not book.get("valid"):
        raise ValueError("Valid Kalshi order-book snapshot required")
    rows = book.get(f"{outcome}_{'asks' if action == 'buy' else 'bids'}", [])
    return sorted([(number(p), number(q)) for p, q in rows], reverse=action == "sell")


def canonical_level(outcome, action, price):
    # YES buy == NO sell on the canonical YES bid; the other pair is the YES ask.
    side = "bid" if (action == "buy") == (outcome == "yes") else "ask"
    return side, number(price) if outcome == "yes" else 1 - number(price)


def taker_plan(book, outcome, action, quantity, limit, ranges, config, debt):
    rows = book_levels(book, outcome, action)
    remaining, result = number(quantity), []
    if not rows:
        return result
    best = rows[0][0]
    for raw_price, displayed in rows:
        if displayed < 0 or not on_grid(raw_price, ranges):
            raise ValueError("Invalid executable depth/grid")
        price = adverse_price(raw_price, action, config.slippage_dollars, ranges)
        if price is None or (price > limit if action == "buy" else price < limit):
            break
        if abs(price - best) > config.max_slippage_dollars:
            break
        side, canonical_price = canonical_level(
            outcome, "sell" if action == "buy" else "buy", raw_price
        )
        available = max(
            Decimal(0), displayed * config.depth_participation - debt(side, canonical_price)
        )
        qty = min(remaining, available).quantize(CONTRACT, rounding=ROUND_FLOOR)
        if qty > 0:
            result.append(
                {
                    "quantity": qty,
                    "price": price,
                    "raw_price": raw_price,
                    "side": side,
                    "canonical_price": canonical_price,
                }
            )
            remaining -= qty
        if remaining == 0:
            break
    return result

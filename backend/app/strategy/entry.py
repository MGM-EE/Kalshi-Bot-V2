from decimal import Decimal

from ..execution.fees import fill_fee
from ..execution.fill_model import taker_plan
from ..markets.orderbook import number
from .conditions import evaluate_rules


def quote(broker, asset, outcome, action, quantity, limit, now, use_debt=True):
    target = broker.state.assets[asset]
    market = target["market"]
    if not market or not market.get("price_ranges"):
        return None
    book = target["kalshi"].get("book", {})
    if not book.get("valid"):
        return None
    rows = book.get(f"{outcome}_{'asks' if action == 'buy' else 'bids'}", [])
    opposing = book.get(f"{outcome}_{'bids' if action == 'buy' else 'asks'}", [])
    if not rows or not opposing:
        return None
    plan = taker_plan(
        book,
        outcome,
        action,
        quantity,
        limit,
        market["price_ranges"],
        broker.config,
        lambda side, price: (
            broker.db.debt(market["ticker"], side, price) if use_debt else Decimal(0)
        ),
    )
    filled, gross, fees, accumulator = Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    for leg in plan:
        fee = fill_fee(
            leg["price"],
            leg["quantity"],
            action,
            market["fee_type"],
            market["fee_multiplier"],
            accumulator=accumulator,
            precision=Decimal(broker.config.balance_precision),
        )
        accumulator = fee.accumulator
        filled += leg["quantity"]
        gross += leg["quantity"] * leg["price"]
        fees += fee.net
    best = number(rows[0][0])
    bid = number(opposing[0][0]) if action == "buy" else best
    ask = best if action == "buy" else number(opposing[0][0])
    depth_plan = taker_plan(
        book,
        outcome,
        action,
        sum((number(q) for _, q in rows), Decimal(0)),
        limit,
        market["price_ranges"],
        broker.config,
        lambda side, price: (
            broker.db.debt(market["ticker"], side, price) if use_debt else Decimal(0)
        ),
    )
    return {
        "quantity": filled,
        "gross": gross,
        "fees": fees,
        "cost": gross + fees if action == "buy" else gross - fees,
        "all_in_price": (gross + fees) / filled if filled else None,
        "ask": ask,
        "bid": bid,
        "spread": ask - bid,
        "depth": sum((leg["quantity"] for leg in depth_plan), Decimal(0)),
        "slippage": max((abs(leg["price"] - best) for leg in plan), default=Decimal(0)),
        "plan": plan,
    }


def evaluate_entry(profile, metrics, remaining, quote_value, confirmation=None):
    settings = profile.entry
    ask_cap, all_in_cap = price_caps(settings, metrics)
    hard = []
    if (
        any(
            value is not None
            for value in [
                settings.trending_max_ask,
                settings.trending_max_all_in,
                settings.nontrending_max_ask,
                settings.nontrending_max_all_in,
            ]
        )
        and metrics.get("momentum") is None
    ):
        hard.append("Trend regime unavailable for conditional price caps")
    if (
        not settings.latest_remaining_seconds <= remaining <= settings.earliest_remaining_seconds
        or remaining < settings.minimum_remaining_after_entry
    ):
        hard.append("Outside entry window")
    if not quote_value or not quote_value["quantity"] or quote_value["all_in_price"] is None:
        hard.append("No executable depth")
    else:
        for value, threshold, reason in [
            (quote_value["ask"], ask_cap, "Ask cap exceeded"),
            (quote_value["all_in_price"], all_in_cap, "All-in cap exceeded"),
            (quote_value["spread"], settings.max_spread, "Spread cap exceeded"),
            (quote_value["slippage"], settings.max_slippage, "Slippage cap exceeded"),
        ]:
            if value > threshold:
                hard.append(reason)
        if quote_value["depth"] < settings.min_depth:
            hard.append("Insufficient depth")
        metrics = {
            **metrics,
            **{k: quote_value[k] for k in ("ask", "all_in_price", "spread", "depth")},
            "liquidity": quote_value["depth"],
        }
    result = evaluate_rules(
        settings.rules, metrics, settings.mode, settings.weighted_threshold, confirmation
    )
    result["eligible"] = result["eligible"] and not hard
    result["hard_blockers"] = hard
    result["reason"] = (
        "; ".join(
            hard
            or [
                s["metric"] + ": " + s["reason"]
                for s in result["conditions"]
                if s["enabled"] and s["state"] != "green"
            ]
        )
        or "All enabled required rules and execution constraints passed"
    )
    return result


def price_caps(settings, metrics):
    trending = (
        metrics.get("momentum") is not None
        and metrics["momentum"] >= settings.trending_momentum_threshold
    )
    ask = settings.trending_max_ask if trending else settings.nontrending_max_ask
    all_in = settings.trending_max_all_in if trending else settings.nontrending_max_all_in
    return (
        ask if ask is not None else settings.max_ask,
        all_in if all_in is not None else settings.max_all_in_price,
    )

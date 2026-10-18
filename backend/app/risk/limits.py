"""Hard execution/allocation constraints, independent of future signal scores."""

from decimal import Decimal

from ..markets.orderbook import number


def remaining(order):
    return number(order["spec"]["quantity"]) - number(order["filled_quantity"])


def reservation(order):
    return (
        max(Decimal(0), number(order["spec"]["max_cost"]) - number(order["spent"]))
        if (order["spec"]["action"] == "buy")
        else Decimal(0)
    )


def validate_order(
    spec, limits, account, positions, active, round_state, equity, day_peak, new_entry=True
):
    if (
        spec.action == "buy" and new_entry and spec.quantity < limits.min_quantity
    ) or spec.quantity > limits.max_quantity:
        raise ValueError("Quantity outside configured risk limits")
    key = (spec.ticker, spec.outcome)
    held = next((p for p in positions if (p["ticker"], p["outcome"]) == key), None)
    if spec.action == "sell":
        reserved = sum(
            (
                remaining(o)
                for o in active
                if o["spec"]["action"] == "sell"
                and (o["spec"]["ticker"], o["spec"]["outcome"]) == key
            ),
            Decimal(0),
        )
        if spec.quantity > (number(held["quantity"]) if held else 0) - reserved:
            raise ValueError("Insufficient unreserved contracts; paper short selling is disabled")
        return
    budget = spec.max_cost
    if budget > limits.max_entry_cost:
        raise ValueError("Maximum entry cost exceeded")
    if number(account["cash"]) - sum((reservation(o) for o in active), Decimal(0)) < budget:
        raise ValueError("Insufficient available paper cash")
    pending_buys = [o for o in active if o["spec"]["action"] == "buy"]
    exposure = [
        (p["asset"], p["ticker"], p["outcome"], number(p["cost_basis"]))
        for p in positions
        if number(p["quantity"]) > 0
    ]
    exposure += [
        (o["spec"]["asset"], o["spec"]["ticker"], o["spec"]["outcome"], reservation(o))
        for o in pending_buys
    ]

    def total(predicate):
        return sum(
            (
                cost
                for asset, ticker, outcome, cost in exposure
                if predicate(asset, ticker, outcome)
            ),
            Decimal(0),
        )

    if total(lambda a, t, s: True) + budget > limits.max_total_exposure:
        raise ValueError("Maximum portfolio exposure exceeded")
    if total(lambda a, t, s: a == spec.asset) + budget > limits.max_asset_exposure:
        raise ValueError("Maximum asset exposure exceeded")
    round_exposure = total(lambda a, t, s: t == spec.ticker) + budget
    if round_exposure > limits.max_round_exposure:
        raise ValueError("Maximum round exposure exceeded")
    if total(lambda a, t, s: (t, s) == key) + budget > limits.max_loss_per_trade:
        raise ValueError("Maximum potential paper loss per position exceeded")
    if (
        round_exposure - min(Decimal(0), number(round_state["realized_pnl"]))
        > limits.max_loss_per_round
    ):
        raise ValueError("Maximum potential paper loss per round exceeded")
    pending_entries = sum(
        o["spec"]["ticker"] == spec.ticker and number(o["filled_quantity"]) == 0
        for o in pending_buys
    )
    if new_entry and round_state["entries"] + pending_entries >= limits.max_entries_per_round:
        raise ValueError("Maximum entry count reached")
    keys = {(ticker, outcome) for _, ticker, outcome, _ in exposure}
    keys.add(key)
    if len(keys) > limits.max_positions:
        raise ValueError("Maximum simultaneous positions reached")
    cycles = sum(ticker == spec.ticker for ticker, _ in keys)
    if round_state["round_trips"] + cycles > limits.max_round_trips_per_round:
        raise ValueError("Maximum round-trip count reached")
    if day_peak - equity >= limits.max_daily_drawdown:
        raise ValueError("Daily drawdown pause active")
    if account["consecutive_losses"] >= limits.max_consecutive_losses:
        raise ValueError("Consecutive-loss pause active")

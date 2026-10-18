"""Read-only condition diagnostics; trading decisions stay in entry/exit evaluators."""

from decimal import Decimal

from ..markets.orderbook import number
from .conditions import evaluate_rules
from .profiles import METRICS, Rule


def entry_constraints(profile, metrics, remaining, estimate):
    from .entry import price_caps

    ask_cap, all_in_cap = price_caps(profile.entry, metrics)
    settings = profile.entry
    rules = [
        Rule(
            metric="data_age",
            operator="between",
            threshold=max(
                settings.latest_remaining_seconds, settings.minimum_remaining_after_entry
            ),
            upper=settings.earliest_remaining_seconds,
        ),
        Rule(metric="ask", operator="max", threshold=ask_cap),
        Rule(metric="all_in_price", operator="max", threshold=all_in_cap),
        Rule(metric="spread", operator="max", threshold=settings.max_spread),
        Rule(metric="depth", threshold=settings.min_depth),
    ]
    values = dict(estimate or {}, data_age=Decimal(str(remaining)))
    result = evaluate_rules(rules, values)["conditions"]
    result[0].update(metric="time_remaining", unit="seconds", source="Kalshi contract cutoff")
    for row in result[1:]:
        row.update(unit=METRICS[row["metric"]][0], source="Kalshi executable book + fee model")
    return result


def exit_conditions(profile, position, market, estimate, metrics, runtime, now):
    settings = profile.exit
    qty = number(position["quantity"])
    cost = number(position["cost_basis"])
    original = number(position.get("cycle_entry_cost", cost))
    pnl = estimate["cost"] - cost + number(position.get("cycle_realized", 0)) if estimate else None
    bid = estimate["bid"] if estimate else None
    rows = []

    def add(
        name,
        value,
        operator,
        threshold,
        enabled=True,
        unit="",
        source="Kalshi executable bid / paper ledger",
    ):
        passed = value is not None and (
            value >= threshold if operator == ">=" else value <= threshold
        )
        rows.append(
            dict(
                metric=name,
                value=value,
                threshold=threshold,
                operator=operator,
                enabled=enabled,
                state="gray" if not enabled or value is None else "green" if passed else "red",
                reason="Disabled"
                if not enabled
                else "Unavailable"
                if value is None
                else "Exit evidence met"
                if passed
                else "Exit evidence absent",
                source=source,
                unit=unit,
            )
        )

    add("target_bid", bid, ">=", settings.target_price, unit="USD/contract")
    add("net_profit", pnl, ">=", settings.minimum_net_profit, unit="USD")
    add(
        "net_return",
        pnl / original if pnl is not None and original else None,
        ">=",
        settings.minimum_net_return,
        unit="fraction",
    )
    add("maximum_loss", pnl, "<=", -settings.maximum_loss, unit="USD")
    for name, key, threshold, enabled in [
        (
            "whale_reversal",
            "whale_flow",
            -settings.adverse_whale_usd,
            settings.whale_reversal_enabled,
        ),
        (
            "flow_reversal",
            "flow_imbalance",
            -settings.adverse_flow_threshold,
            settings.flow_reversal_enabled,
        ),
        ("trend_break", "momentum", -settings.adverse_momentum, settings.trend_break_enabled),
    ]:
        add(
            name,
            metrics.get(key),
            "<=",
            threshold,
            enabled,
            METRICS[key][0],
            "Kraken observed flow / price",
        )
    add(
        "choppiness",
        metrics.get("choppiness"),
        ">=",
        settings.maximum_choppiness,
        settings.chop_exit_enabled,
        "score 0–100",
        "Observed spot + CF index",
    )
    add(
        "exit_depth_ratio",
        estimate["quantity"] / qty if estimate and qty else None,
        "<=",
        settings.minimum_exit_depth_ratio,
        settings.liquidity_exit_enabled,
        "fraction",
    )
    add(
        "trailing_bid_drop",
        max(Decimal(0), number(runtime.get("peak_bid", bid)) - bid) if bid is not None else None,
        ">=",
        settings.trailing_price_drop,
        settings.trailing_price_enabled,
        "USD/contract",
    )
    add(
        "trailing_profit_drop",
        number(runtime.get("peak_profit", pnl)) - pnl if pnl is not None else None,
        ">=",
        settings.trailing_profit_drop,
        settings.trailing_profit_enabled,
        "USD",
    )
    add(
        "protected_profit",
        pnl if runtime.get("protection_armed") else None,
        "<=",
        settings.protected_profit,
        settings.protection_enabled,
        "USD",
    )
    previous, signed = runtime.get("signed_distance"), metrics.get("signed_distance")
    crossed = (
        Decimal(int(number(previous) >= 0 > signed))
        if previous is not None and signed is not None
        else None
    )
    add(
        "reference_cross",
        crossed,
        ">=",
        1,
        settings.reference_cross_enabled,
        "boolean",
        "CF Benchmarks index",
    )
    add(
        "minimum_hold",
        Decimal(str(now - position.get("opened_at", now))),
        ">=",
        Decimal(str(settings.minimum_hold_seconds)),
        unit="seconds",
    )
    add(
        "maximum_hold",
        Decimal(str(now - position.get("opened_at", now))),
        ">=",
        Decimal(str(settings.maximum_hold_seconds)),
        not settings.hold_to_settlement,
        "seconds",
    )
    add(
        "latest_exit_time",
        Decimal(str(market["close_time"] - now)) if market else None,
        "<=",
        Decimal(str(settings.latest_remaining_seconds)),
        not settings.hold_to_settlement,
        "seconds",
    )
    add(
        "earliest_exit_time",
        Decimal(str(market["close_time"] - now)) if market else None,
        "<=",
        Decimal(str(settings.earliest_remaining_seconds)),
        not settings.hold_to_settlement,
        "seconds",
    )
    add(
        "target_reversal",
        Decimal(
            int(
                (metrics.get("momentum") is not None and metrics["momentum"] < 0)
                or (metrics.get("flow_imbalance") is not None and metrics["flow_imbalance"] < 0)
            )
        )
        if metrics.get("momentum") is not None or metrics.get("flow_imbalance") is not None
        else None,
        ">=",
        1,
        settings.require_reversal_at_target,
        "boolean",
        "Kraken price / trades",
    )
    return rows

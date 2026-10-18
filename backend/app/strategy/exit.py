"""Deterministic priority-ordered exits. No uncalibrated expectancy model."""

from decimal import Decimal

from ..markets.orderbook import number


def evaluate_exit(
    profile, position, market, estimate, metrics, runtime, now, emergency=False, hard_risk=False
):
    settings = profile.exit
    qty, cost = number(position["quantity"]), number(position["cost_basis"])
    result = {
        "action": "HOLD",
        "reason": "No enabled exit trigger",
        "priority": 7,
        "fraction": Decimal(1),
        "state": dict(runtime),
    }

    def decision(action, reason, priority, fraction=Decimal(1)):
        result.update(action=action, reason=reason, priority=priority, fraction=fraction)
        return result

    if qty <= 0 or cost < 0:
        return decision("PAUSE_INVALID", "Invalid position state", 1)
    if not market or market["ticker"] != position["ticker"] or now >= market["close_time"]:
        return decision("WAIT_SETTLEMENT", "Trading cutoff reached; await official settlement", 2)
    if market["status"] not in {"active", "open"}:
        return decision("WAIT_MARKET", "Market not tradable", 2)
    if emergency:
        return decision("SELL_ALL", "Emergency shutdown", 1)
    if not estimate or estimate["quantity"] == 0:
        return decision("HOLD_DATA_UNAVAILABLE", "No executable bid; no sale invented", 3)
    bid, pnl = estimate["bid"], estimate["cost"] - cost + number(position.get("cycle_realized", 0))
    original_cost = number(position.get("cycle_entry_cost", cost))
    result.update(
        net_profit=pnl,
        net_return=pnl / original_cost if original_cost else None,
        executable_quantity=estimate["quantity"],
    )
    state = result["state"]
    previous_signed = state.get("signed_distance")
    if metrics.get("signed_distance") is not None:
        state["signed_distance"] = metrics["signed_distance"]
        state["reference_cross_active"] = metrics["signed_distance"] < 0 and (
            state.get("reference_cross_active", False)
            or (previous_signed is not None and number(previous_signed) >= 0)
        )
    state["peak_bid"] = max(number(state.get("peak_bid", bid)), bid)
    state["peak_profit"] = max(number(state.get("peak_profit", pnl)), pnl)
    if pnl >= settings.protection_trigger:
        state["protection_armed"] = True
    if hard_risk:
        return decision("SELL_ALL", "Hard exposure/drawdown safeguard", 3)
    if pnl <= -settings.maximum_loss:
        return decision("CUT_LOSS", "Maximum acceptable loss reached", 3)
    reversals = []
    checks = [
        (
            settings.whale_reversal_enabled,
            "whale_flow",
            -settings.adverse_whale_usd,
            "Adverse whale flow",
        ),
        (
            settings.flow_reversal_enabled,
            "flow_imbalance",
            -settings.adverse_flow_threshold,
            "Order-flow reversal",
        ),
        (settings.trend_break_enabled, "momentum", -settings.adverse_momentum, "Trend break"),
    ]
    for enabled, key, threshold, reason in checks:
        if enabled and metrics.get(key) is not None and metrics[key] <= threshold:
            reversals.append(reason)
    if (
        settings.reference_cross_enabled
        and metrics.get("signed_distance") is not None
        and state.get("reference_cross_active", False)
    ):
        reversals.append("Adverse reference-line crossing")
    if (
        settings.chop_exit_enabled
        and metrics.get("choppiness") is not None
        and metrics["choppiness"] >= settings.maximum_choppiness
    ):
        reversals.append("Choppiness threshold reached")
    if (
        settings.liquidity_exit_enabled
        and estimate["quantity"] / qty < settings.minimum_exit_depth_ratio
    ):
        reversals.append("Exit liquidity deteriorated")
    if reversals:
        return decision("SELL_ALL", "; ".join(reversals), 4)
    if settings.trailing_price_enabled and state["peak_bid"] - bid >= settings.trailing_price_drop:
        return decision("PROTECT_PROFIT", "Executable-bid trailing stop", 5)
    if (
        settings.trailing_profit_enabled
        and number(state["peak_profit"]) > 0
        and number(state["peak_profit"]) - pnl >= settings.trailing_profit_drop
    ):
        return decision("PROTECT_PROFIT", "Achievable net-profit trailing stop", 5)
    if (
        settings.protection_enabled
        and state.get("protection_armed")
        and pnl <= settings.protected_profit
    ):
        return decision("PROTECT_PROFIT", "Protect previously activated net profit", 5)
    held = now - position.get("opened_at", now)
    remaining = market["close_time"] - now
    if held < settings.minimum_hold_seconds:
        return decision("HOLD", "Minimum holding time", 6)
    if settings.hold_to_settlement:
        return decision(
            "HOLD_TO_SETTLEMENT", "Explicit hold mode; protective rules remain active", 6
        )
    if held >= settings.maximum_hold_seconds or remaining <= settings.latest_remaining_seconds:
        return decision("SELL_ALL", "Maximum holding time or latest normal exit time", 6)
    if remaining > settings.earliest_remaining_seconds:
        return decision("HOLD", "Before earliest normal exit", 6)
    evidence = (metrics.get("momentum") is not None and metrics["momentum"] < 0) or (
        metrics.get("flow_imbalance") is not None and metrics["flow_imbalance"] < 0
    )
    target = (
        bid >= settings.target_price
        and pnl >= settings.minimum_net_profit
        and (pnl / original_cost if original_cost else 0) >= settings.minimum_net_return
    )
    if target and (evidence or not settings.require_reversal_at_target):
        if state.get("partial_taken"):
            return decision(
                "HOLD",
                "Partial target already taken; residual remains under protective/time rules",
                6,
            )
        fraction = settings.partial_fraction
        return decision(
            "SELL_PARTIAL" if fraction < 1 else "SELL_ALL",
            "Net profit target with configured reversal confirmation",
            6,
            fraction,
        )
    if target:
        return decision("HOLD", "Target reached; required reversal evidence absent", 6)
    return result

"""Complete-ledger cycle analytics, including partial exits and official settlement."""

from collections import defaultdict
from decimal import Decimal

from ..markets.orderbook import number

ZERO = Decimal(0)


def analytics(database):
    fills = database.fills(-1)[::-1]
    # Merge real recorded settlement exits into chronological cash-flow events.
    settlements = database.many("SELECT data FROM settlements ORDER BY rowid")
    positions = database.positions()
    events = [(f["time"], 0, f["id"], f) for f in fills]
    events.extend((s["time"], 1, i, s) for i, s in enumerate(settlements))
    active = {}
    closed = []
    fees = ZERO
    slippage = ZERO
    realized_path = []
    for ts, kind, _, event in sorted(events, key=lambda v: v[:3]):
        if kind == 1:
            for key, cycle in list(active.items()):
                if key[0] != event["ticker"]:
                    continue
                payout = cycle["quantity"] if key[1] == event["result"] else ZERO
                pnl = payout - cycle["basis"]
                cycle.update(
                    pnl=cycle["pnl"] + pnl,
                    quantity=ZERO,
                    basis=ZERO,
                    exit_time=ts,
                    exit_reason="Official settlement",
                    settled=True,
                    result=event["result"],
                )
                realized_path.append(pnl)
                closed.append(cycle)
                del active[key]
            continue
        f = event
        key = (f["ticker"], f["outcome"])
        quantity = number(f["quantity"])
        fee = number(f["net_fee"])
        fees += fee
        slippage += number(f["slippage_dollars"])
        if f["action"] == "buy":
            if key not in active:
                context = f.get("strategy_context") or {}
                observations = context.get("observations") or f.get("market_observation") or {}
                metrics = observations.get("metrics", {})
                signed = metrics.get("whale_flow")
                chop = metrics.get("choppiness")
                active[key] = dict(
                    asset=f["asset"],
                    ticker=f["ticker"],
                    outcome=f["outcome"],
                    strategy_version=f["strategy_version"],
                    entry_time=ts,
                    entry_price=f["price"],
                    entry_remaining=observations.get("remaining_seconds"),
                    whale_regime="unknown"
                    if signed is None
                    else "supportive"
                    if number(signed) > 0
                    else "adverse"
                    if number(signed) < 0
                    else "neutral",
                    chop_regime="unknown"
                    if chop is None
                    else "high"
                    if number(chop) >= 65
                    else "moderate"
                    if number(chop) >= 40
                    else "low",
                    pnl=ZERO,
                    basis=ZERO,
                    quantity=ZERO,
                    entry_quantity=ZERO,
                    entry_notional=ZERO,
                    fees=ZERO,
                    slippage=ZERO,
                    settled=False,
                )
            cycle = active[key]
            cost = number(f["price"]) * quantity + fee
            cycle["quantity"] += quantity
            cycle["basis"] += cost
            cycle["entry_quantity"] += quantity
            cycle["entry_notional"] += number(f["price"]) * quantity
            cycle["entry_price"] = cycle["entry_notional"] / cycle["entry_quantity"]
        elif key in active:
            cycle = active[key]
            pnl = number(f["realized_pnl"])
            cycle["pnl"] += pnl
            realized_path.append(pnl)
            basis = cycle["basis"] * quantity / cycle["quantity"]
            cycle["quantity"] -= quantity
            cycle["basis"] -= basis
            cycle.update(
                exit_time=ts,
                exit_reason=f["reason"],
                exit_remaining=(f.get("market_observation") or {}).get("remaining_seconds"),
            )
            if cycle["quantity"] == 0:
                closed.append(cycle)
                del active[key]
        else:
            continue
        cycle["fees"] += fee
        cycle["slippage"] += number(f["slippage_dollars"])
    account = database.account()
    pnls = [c["pnl"] for c in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    equity = peak = drawdown = ZERO
    for pnl in realized_path:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    def group(key):
        result = defaultdict(lambda: dict(trades=0, net_pnl=ZERO, wins=0))
        for cycle in closed:
            row = result[str(key(cycle))]
            row["trades"] += 1
            row["net_pnl"] += cycle["pnl"]
            row["wins"] += cycle["pnl"] > 0
        return dict(result)

    decisions = database.many("SELECT data FROM strategy_decisions ORDER BY id")
    reasons = defaultdict(int)
    for decision in decisions:
        if decision["side"] == "entry" and not decision["eligible"]:
            reasons[decision["decision"]["reason"]] += 1
    orders = database.many("SELECT data FROM orders ORDER BY rowid")
    runtime = database.many("SELECT data FROM strategy_runtime WHERE key LIKE 'entry:%'")
    return dict(
        total_closed_trades=len(closed),
        open_cycles=len(active),
        win_rate=Decimal(len(wins)) / len(pnls) if pnls else None,
        average_win=gross_win / len(wins) if wins else None,
        average_loss=-gross_loss / len(losses) if losses else None,
        net_expectancy=sum(pnls, ZERO) / len(pnls) if pnls else None,
        profit_factor=gross_win / gross_loss if gross_loss else None,
        net_closed_pnl=sum(pnls, ZERO),
        maximum_realized_drawdown=drawdown,
        maximum_observed_drawdown=account.get("maximum_observed_drawdown"),
        observed_drawdown_scope="Conservative executable marks at recorded account observations; gaps are not interpolated",
        realized_drawdown_scope="Realized cash-flow path only; not intraday mark-to-market drawdown",
        fees=fees,
        estimated_slippage_usd=slippage,
        fee_burden_per_closed_trade=sum((c["fees"] for c in closed), ZERO) / len(closed)
        if closed
        else None,
        by_asset=group(lambda c: c["asset"]),
        by_strategy=group(lambda c: c["strategy_version"]),
        by_entry_timing=group(
            lambda c: (
                "unknown"
                if c["entry_remaining"] is None
                else "last_2m"
                if c["entry_remaining"] <= 120
                else "2_to_5m"
                if c["entry_remaining"] <= 300
                else "over_5m"
            )
        ),
        by_purchase_price=group(
            lambda c: (
                "under_20c"
                if number(c["entry_price"]) < Decimal(".2")
                else "20_to_40c"
                if number(c["entry_price"]) <= Decimal(".4")
                else "over_40c"
            )
        ),
        by_whale_regime=group(lambda c: c["whale_regime"]),
        by_chop_regime=group(lambda c: c["chop_regime"]),
        by_exit_reason=group(lambda c: c["exit_reason"]),
        by_exit_remaining=group(
            lambda c: (
                "unknown"
                if c.get("exit_remaining") is None
                else "last_minute"
                if c["exit_remaining"] <= 60
                else "over_1m"
            )
        ),
        rejected_orders=sum(o["status"] == "rejected" for o in orders),
        unfilled_orders=sum(
            o["status"] in {"unfilled", "expired", "cancelled"}
            and number(o["filled_quantity"]) == 0
            for o in orders
        ),
        missed_opportunities={
            "scope": "Observed decision transitions and actual unfilled orders only; no hypothetical fills or returns",
            "entry_episodes": sum(r.get("episode", 0) for r in runtime),
            "confirmed_blocked_transitions": sum(
                d["side"] == "entry" and d.get("blocked_without_order", False) for d in decisions
            ),
            "blocked_or_unknown_transitions_by_reason": dict(reasons),
        },
        closed_trades=closed,
        open_positions=sum(number(p["quantity"]) > 0 for p in positions),
    )

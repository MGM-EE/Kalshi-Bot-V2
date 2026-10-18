"""Durable paper-only order state machine. Every financial transition is atomic."""

import hashlib
import time
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal

from ..markets.discovery import timestamp
from ..markets.orderbook import number
from ..risk.limits import remaining, reservation, validate_order
from ..storage.database import encode
from .fees import fee_rate, fill_fee
from .fill_model import CONTRACT, book_levels, canonical_level, on_grid, taker_plan
from .models import PaperConfig, PaperOrder, RiskLimits

ZERO = Decimal(0)
TERMINAL = {"filled", "partial", "unfilled", "cancelled", "expired", "rejected"}


class IdempotencyConflict(ValueError):
    pass


class PaperBroker:
    def __init__(self, database, state, config=None, limits=None, clock=time.time):
        self.db, self.state, self.clock = database, state, clock
        self.config, self.limits = config or PaperConfig(), limits or RiskLimits()
        self.strategy_guard = None
        self.strategy_fill_guard = None
        self.emergency_gate = None
        with self.db.transaction():
            if self.db.account() is None:
                balance = self.config.starting_balance
                self.db.save_account(
                    {
                        "starting_balance": balance,
                        "cash": balance,
                        "realized_pnl": ZERO,
                        "fees": ZERO,
                        "consecutive_losses": 0,
                        "daily": {},
                        "daily_realized": {},
                        "balance_precision": self.config.balance_precision,
                    }
                )
            elif self.db.account()["balance_precision"] != self.config.balance_precision:
                raise ValueError("Existing paper account balance precision cannot be changed")

    def market(self, spec, now, require_entry=True):
        target = self.state.assets[spec.asset]
        market = target["market"]
        if not market or market["ticker"] != spec.ticker:
            raise ValueError("Selected market is unavailable or has rolled over")
        if (
            market["status"] not in {"open", "active"}
            or not market["open_time"] <= now < market["close_time"]
        ):
            raise ValueError("Market closed/paused or trading cutoff reached; await settlement")
        sources = [f"kalshi:{spec.asset}:book"]
        if require_entry:
            if (
                not market["outcome_mapping"]
                or market.get("rule_checks")
                or market.get("current_index") is None
            ):
                raise ValueError(
                    "Verified contract rules and current settlement reference required"
                )
            sources += [f"kalshi:{spec.asset}:reference"]
            sources += [f"kraken:{spec.asset}:{channel}" for channel in ("book", "ticker", "trade")]
        for source in sources:
            health = self.state.sources.get(source)
            if not health or health["status"] not in {"live", "polling"}:
                raise ValueError(f"Required data unavailable: {source}")
            age = now - health["received_at"]
            exchange_age = now - health["exchange_ts"] if health["exchange_ts"] is not None else age
            if (
                not -5 <= age <= self.config.max_data_age_seconds
                or not -5 <= exchange_age <= self.config.max_data_age_seconds
            ):
                raise ValueError(f"Stale or clock-drifted data: {source}")
        if not market.get("price_ranges") or not on_grid(spec.limit_price, market["price_ranges"]):
            raise ValueError("Limit is outside the contract's current tick grid")
        if (
            not market.get("fees_verified_at")
            or now - market["fees_verified_at"] > 60
            or now >= market.get("fees_valid_until", 0)
        ):
            raise ValueError("Fresh effective series/event fee metadata required")
        fee_rate(market["fee_type"], market["fee_multiplier"])
        if (
            market.get("fee_waiver_expiration_time")
            and timestamp(market["fee_waiver_expiration_time"]) > now
        ):
            raise ValueError("Fee waiver requires separate verification")
        book_levels(target["kalshi"].get("book", {}), spec.outcome, spec.action)
        return market, target["kalshi"]["book"]

    def submit(self, spec, strategy_context=None):
        spec = PaperOrder.model_validate(spec)
        payload = spec.model_dump(mode="json")
        fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
        now = self.clock()
        with self.db.transaction():
            old = self.db.order(spec.client_order_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(
                        "Client order ID was already used for a different request"
                    )
                return old
            order = {
                "id": spec.client_order_id,
                "fingerprint": fingerprint,
                "spec": payload,
                "created_at": now,
                "eligible_at": now + self.config.latency_ms / 1000,
                "expires_at": now + spec.timeout_seconds,
                "status": "pending",
                "reason": spec.reason,
                "filled_quantity": ZERO,
                "spent": ZERO,
                "proceeds": ZERO,
                "fees": ZERO,
                "accumulator": ZERO,
                "config": self.config.model_dump(mode="json"),
                "risk_version": self.limits.version,
                "fill_count": 0,
                "realized_pnl": ZERO,
                "strategy_context": strategy_context,
            }
            try:
                if spec.action == "buy" and self.emergency_gate and self.emergency_gate():
                    raise ValueError("Emergency paper-entry stop is active")
                market, book = self.market(spec, now, spec.action == "buy")
                equity, peak = self.mark_account(now)
                validate_order(
                    spec,
                    self.limits,
                    self.db.account(),
                    self.db.positions(),
                    self.db.active(),
                    self.db.round(spec.ticker),
                    equity,
                    peak,
                )
                if strategy_context:
                    if self.strategy_guard is None:
                        raise ValueError("Strategy validation is unavailable")
                    self.strategy_guard(order, spec, now)
                held = next(
                    (
                        p
                        for p in self.db.positions()
                        if p["ticker"] == spec.ticker
                        and p["outcome"] == spec.outcome
                        and number(p["quantity"]) > 0
                    ),
                    None,
                )
                if (
                    spec.action == "buy"
                    and held
                    and held.get("strategy_context") != strategy_context
                ):
                    raise ValueError("Existing position has different pinned strategy criteria")
                if spec.mode == "passive":
                    canonical = canonical_level(spec.outcome, spec.action, spec.limit_price)
                    if any(
                        o["spec"]["ticker"] == spec.ticker
                        and o["spec"]["mode"] == "passive"
                        and canonical_level(
                            o["spec"]["outcome"],
                            o["spec"]["action"],
                            number(o["spec"]["limit_price"]),
                        )
                        == canonical
                        for o in self.db.active()
                    ):
                        raise ValueError(
                            "One passive paper order per canonical price level is supported"
                        )
                order["market_at_submission"] = market
                order["health_at_submission"] = self.state.snapshot(include_paper=False)["sources"]
            except ValueError as exc:
                order["status"], order["reason"] = "rejected", str(exc)
            self.db.save_order(order)
            return self.db.order(order["id"])

    def cancel(self, order_id):
        with self.db.transaction():
            order = self.db.order(order_id)
            if order is None:
                raise KeyError(order_id)
            if order["status"] not in TERMINAL:
                order["status"], order["reason"] = (
                    "cancelled",
                    "Paper order cancelled; unfilled reservations released",
                )
                self.db.save_order(order)
            return self.db.order(order_id)

    def revalidate_risk(self, order, spec, now):
        if spec.action == "buy" and self.emergency_gate and self.emergency_gate():
            raise ValueError("Emergency paper-entry stop is active")
        if order.get("strategy_context"):
            if self.strategy_guard is None:
                raise ValueError("Strategy validation is unavailable")
            self.strategy_guard(order, spec, now)
        if spec.action == "sell":
            return
        equity, peak = self.mark_account(now)
        active = [o for o in self.db.active() if o["id"] != order["id"]]
        effective = spec.model_copy(
            update={"max_cost": reservation(order), "quantity": remaining(order)}
        )
        validate_order(
            effective,
            self.limits,
            self.db.account(),
            self.db.positions(),
            active,
            self.db.round(spec.ticker),
            equity,
            peak,
            new_entry=number(order["filled_quantity"]) == 0,
        )

    def process(self):
        now = self.clock()
        with self.db.transaction():
            for order in self.db.active():
                spec = PaperOrder.model_validate(order["spec"])
                market = self.state.assets[spec.asset]["market"]
                cutoff = number(
                    market["close_time"]
                    if market and market["ticker"] == spec.ticker
                    else order["market_at_submission"]["close_time"]
                )
                if now >= min(order["expires_at"], float(cutoff)):
                    order["status"], order["reason"] = (
                        "expired",
                        "Order timeout or actual trading cutoff reached",
                    )
                    self.db.save_order(order)
                    continue
                if now < order["eligible_at"]:
                    continue
                try:
                    market, book = self.market(spec, now, spec.action == "buy")
                    self.revalidate_risk(order, spec, now)
                except ValueError as exc:
                    if order["status"] == "pending" and spec.mode == "marketable":
                        order["status"] = "rejected"
                    order["reason"] = str(exc)
                    self.db.save_order(order)
                    continue
                config = PaperConfig.model_validate(order["config"])
                if order["status"] == "pending":
                    if spec.mode == "marketable":
                        plan = taker_plan(
                            book,
                            spec.outcome,
                            spec.action,
                            remaining(order),
                            spec.limit_price,
                            market["price_ranges"],
                            config,
                            lambda side, price: self.db.debt(spec.ticker, side, price),
                        )
                        for leg in plan:
                            if not self.apply_fill(order, spec, leg, market, now, maker=False):
                                break
                            self.db.consume(
                                spec.ticker, leg["side"], leg["canonical_price"], leg["quantity"]
                            )
                        order["status"] = (
                            "filled"
                            if remaining(order) == 0
                            else ("partial" if number(order["filled_quantity"]) > 0 else "unfilled")
                        )
                        order["reason"] = (
                            "IOC depth execution complete"
                            if remaining(order) == 0
                            else (
                                "Unfilled IOC remainder cancelled: depth, slippage, or cash budget"
                            )
                        )
                    else:
                        opposite = book_levels(book, spec.outcome, spec.action)
                        if opposite and (
                            opposite[0][0] <= spec.limit_price
                            if spec.action == "buy"
                            else opposite[0][0] >= spec.limit_price
                        ):
                            order["status"], order["reason"] = (
                                "rejected",
                                "Passive post-only order would take liquidity",
                            )
                        else:
                            own = book_levels(
                                book, spec.outcome, "sell" if spec.action == "buy" else "buy"
                            )
                            ahead = next((q for p, q in own if p == spec.limit_price), ZERO)
                            order.update(
                                status="resting",
                                resting_at=now,
                                queue_ahead=ahead * config.queue_multiplier
                                + config.queue_buffer_contracts,
                                reason="Conservative passive queue; a touched quote does not fill",
                            )
                    self.db.save_order(order)
            self.process_passive(now)
            self.mark_account(now)

    def apply_fill(self, order, spec, leg, market, now, maker):
        qty, price = number(leg["quantity"]), number(leg["price"])
        config = PaperConfig.model_validate(order["config"])
        if spec.action == "buy":
            budget = min(reservation(order), number(self.db.account()["cash"]))
            low, high = 0, int(qty / CONTRACT)
            while low < high:
                mid = (low + high + 1) // 2
                candidate = CONTRACT * mid
                candidate_fee = fill_fee(
                    price,
                    candidate,
                    spec.action,
                    market["fee_type"],
                    market["fee_multiplier"],
                    maker,
                    number(order["accumulator"]),
                    Decimal(config.balance_precision),
                )
                if price * candidate + candidate_fee.net <= budget:
                    low = mid
                else:
                    high = mid - 1
            qty = CONTRACT * low
            leg["quantity"] = qty
            if qty == 0:
                return False
        fee = fill_fee(
            price,
            qty,
            spec.action,
            market["fee_type"],
            market["fee_multiplier"],
            maker,
            number(order["accumulator"]),
            Decimal(config.balance_precision),
        )
        if order.get("strategy_context"):
            if self.strategy_fill_guard is None or not self.strategy_fill_guard(
                order, spec, leg, fee, now
            ):
                return False
        gross, account = price * qty, self.db.account()
        position = next(
            (
                p
                for p in self.db.positions()
                if (p["ticker"], p["outcome"]) == (spec.ticker, spec.outcome)
            ),
            None,
        )
        if position is None:
            position = {
                "asset": spec.asset,
                "ticker": spec.ticker,
                "outcome": spec.outcome,
                "quantity": ZERO,
                "cost_basis": ZERO,
                "cycle_realized": ZERO,
                "close_time": market["close_time"],
            }
        round_state = self.db.round(spec.ticker)
        realized = ZERO
        if spec.action == "buy":
            cost = gross + fee.net
            if cost > reservation(order) or cost > number(account["cash"]):
                return False
            if number(position["quantity"]) == 0:
                position["cycle_realized"] = ZERO
                position["cycle_entry_cost"] = ZERO
                position["opened_at"] = now
                position["cycle_id"] = order["id"]
                position["strategy_context"] = order.get("strategy_context")
            position["cycle_entry_cost"] = number(position.get("cycle_entry_cost", 0)) + cost
            if number(order["filled_quantity"]) == 0:
                round_state["entries"] += 1
            account["cash"] = number(account["cash"]) - cost
            position["cost_basis"] = number(position["cost_basis"]) + cost
            position["quantity"] = number(position["quantity"]) + qty
            order["spent"] = number(order["spent"]) + cost
        else:
            if qty > number(position["quantity"]):
                return False
            basis = number(position["cost_basis"]) * qty / number(position["quantity"])
            proceeds = gross - fee.net
            # A tiny sell may legitimately cost more in rounding/fees than it returns.
            if number(account["cash"]) + proceeds < 0:
                return False
            realized = proceeds - basis
            account["cash"] = number(account["cash"]) + proceeds
            position["quantity"] = number(position["quantity"]) - qty
            position["cost_basis"] = number(position["cost_basis"]) - basis
            order["proceeds"] = number(order["proceeds"]) + proceeds
            self.realize(account, position, round_state, realized, now)
        account["fees"] = number(account["fees"]) + fee.net
        order["fees"] = number(order["fees"]) + fee.net
        order["accumulator"] = fee.accumulator
        order["filled_quantity"] = number(order["filled_quantity"]) + qty
        order["realized_pnl"] = number(order["realized_pnl"]) + realized
        order["fill_count"] += 1
        fill = {
            "order_id": order["id"],
            "asset": spec.asset,
            "ticker": spec.ticker,
            "action": spec.action,
            "outcome": spec.outcome,
            "quantity": qty,
            "price": price,
            "raw_price": leg["raw_price"],
            "slippage_dollars": abs(price - number(leg["raw_price"])) * qty,
            "fee_trade": fee.trade,
            "fee_rounding": fee.rounding,
            "fee_rebate": fee.rebate,
            "net_fee": fee.net,
            "maker": maker,
            "realized_pnl": realized,
            "fee_type": market["fee_type"],
            "fee_multiplier": market["fee_multiplier"],
            "time": now,
            "strategy_version": spec.strategy_version,
            "reason": spec.reason,
            "strategy_context": order.get("strategy_context"),
        }
        self.db.connection.execute(
            "INSERT INTO fills(order_id,data) VALUES(?,?)", (order["id"], encode(fill))
        )
        self.db.save_account(account)
        self.db.save_position(position)
        self.db.save_round(round_state)
        return True

    def realize(self, account, position, round_state, pnl, now):
        account["realized_pnl"] = number(account["realized_pnl"]) + pnl
        position["cycle_realized"] = number(position["cycle_realized"]) + pnl
        round_state["realized_pnl"] = number(round_state["realized_pnl"]) + pnl
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        realized = account.get("daily_realized", {})
        realized[day] = number(realized.get(day, 0)) + pnl
        account["daily_realized"] = dict(sorted(realized.items())[-7:])
        if number(position["quantity"]) == 0:
            position["closed_at"] = now
            round_state["round_trips"] += 1
            account["consecutive_losses"] = (
                account["consecutive_losses"] + 1 if number(position["cycle_realized"]) < 0 else 0
            )

    def process_passive(self, now):
        for asset, target in self.state.assets.items():
            for trade in target["kalshi"].get("trades", []):
                trade_id = trade.get("trade_id")
                if not trade_id:
                    continue
                cursor = self.db.connection.execute(
                    "INSERT OR IGNORE INTO seen_trades VALUES(?)", (trade_id,)
                )
                if cursor.rowcount == 0:
                    continue
                try:
                    ts = trade.get("ts_ms")
                    ts = (
                        float(ts) / 1000
                        if ts is not None
                        else timestamp(trade.get("created_time") or trade.get("ts"))
                    )
                    price, volume = number(trade["yes_price_dollars"]), number(trade["count_fp"])
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
                if (
                    trade.get("is_block_trade")
                    or volume <= 0
                    or ts > now + 5
                    or now - ts > self.config.max_data_age_seconds
                ):
                    continue
                # New canonical outcome information is required; don't guess legacy aggressor semantics.
                taker = trade.get("taker_outcome_side")
                for order in self.db.active():
                    if order["status"] != "resting" or ts <= order["resting_at"]:
                        continue
                    spec = PaperOrder.model_validate(order["spec"])
                    if (
                        spec.asset != asset
                        or not target["market"]
                        or spec.ticker != target["market"]["ticker"]
                    ):
                        continue
                    side, canonical = canonical_level(spec.outcome, spec.action, spec.limit_price)
                    if canonical != price or taker != ("no" if side == "bid" else "yes"):
                        continue
                    try:
                        market, _ = self.market(spec, now, spec.action == "buy")
                        self.revalidate_risk(order, spec, now)
                    except ValueError:
                        continue
                    ahead = number(order["queue_ahead"])
                    order["queue_ahead"] = max(ZERO, ahead - volume)
                    config = PaperConfig.model_validate(order["config"])
                    qty = min(
                        remaining(order), max(ZERO, volume - ahead) * config.depth_participation
                    ).quantize(CONTRACT, rounding=ROUND_FLOOR)
                    if qty > 0:
                        leg = {
                            "quantity": qty,
                            "price": spec.limit_price,
                            "raw_price": spec.limit_price,
                        }
                        if (
                            self.apply_fill(order, spec, leg, market, now, maker=True)
                            and remaining(order) == 0
                        ):
                            order["status"], order["reason"] = (
                                "filled",
                                "Observed trade volume exceeded conservative queue",
                            )
                    self.db.save_order(order)

    def settle(self, ticker, official_market):
        """Only exchange-reported final binary results, never spot/index extrapolation."""
        if (
            official_market.get("ticker") != ticker
            or official_market.get("status") not in {"finalized", "settled"}
            or official_market.get("result") not in {"yes", "no"}
            or not official_market.get("settlement_ts")
            or timestamp(official_market["close_time"]) > self.clock()
        ):
            raise ValueError("Final exchange-reported binary settlement required")
        now = self.clock()
        with self.db.transaction():
            existing = self.db.one("SELECT data FROM settlements WHERE ticker=?", (ticker,))
            if existing:
                if existing["result"] != official_market["result"]:
                    raise ValueError("Conflicting official result; manual reconciliation required")
                return existing
            affected = [
                p
                for p in self.db.positions()
                if p["ticker"] == ticker and number(p["quantity"]) > 0
            ]
            account, round_state, total_payout = self.db.account(), self.db.round(ticker), ZERO
            for position in affected:
                payout = (
                    number(position["quantity"])
                    if position["outcome"] == official_market["result"]
                    else ZERO
                )
                pnl = payout - number(position["cost_basis"])
                total_payout += payout
                account["cash"] = number(account["cash"]) + payout
                position["quantity"], position["cost_basis"] = ZERO, ZERO
                self.realize(account, position, round_state, pnl, now)
                self.db.save_position(position)
            for order in self.db.active():
                if order["spec"]["ticker"] == ticker:
                    order["status"], order["reason"] = (
                        "expired",
                        "Market settled; remainder cancelled",
                    )
                    self.db.save_order(order)
            result = {
                "ticker": ticker,
                "result": official_market["result"],
                "payout": total_payout,
                "time": now,
                "official_market": official_market,
                "source": "Kalshi REST final market result",
            }
            self.db.connection.execute(
                "INSERT INTO settlements VALUES(?,?)", (ticker, encode(result))
            )
            self.db.save_account(account)
            self.db.save_round(round_state)
            return result

    def mark_account(self, now):
        account, value = self.db.account(), ZERO
        for position in self.db.positions():
            qty = number(position["quantity"])
            if not qty:
                continue
            target = self.state.assets[position["asset"]]
            if not target["market"] or target["market"]["ticker"] != position["ticker"]:
                continue
            source = self.state.sources.get(f"kalshi:{position['asset']}:book", {})
            if now - source.get("received_at", 0) > self.config.max_data_age_seconds or source.get(
                "status"
            ) not in {"live", "polling"}:
                continue
            market = target["market"]
            try:
                plan = taker_plan(
                    target["kalshi"].get("book", {}),
                    position["outcome"],
                    "sell",
                    qty,
                    Decimal("0.0001"),
                    market["price_ranges"],
                    self.config,
                    lambda s, p: ZERO,
                )
                accumulator = ZERO
                for leg in plan:
                    fee = fill_fee(
                        leg["price"],
                        leg["quantity"],
                        "sell",
                        market["fee_type"],
                        market["fee_multiplier"],
                        accumulator=accumulator,
                        precision=Decimal(self.config.balance_precision),
                    )
                    accumulator = fee.accumulator
                    value += leg["price"] * leg["quantity"] - fee.net
            except (ValueError, KeyError):
                continue
        equity = number(account["cash"]) + value
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        daily = account["daily"]
        peak = max(number(daily.get(day, {}).get("peak_equity", equity)), equity)
        daily[day] = {"peak_equity": peak}
        account["daily"] = dict(sorted(daily.items())[-7:])
        self.db.save_account(account)
        return equity, peak

    def portfolio(self):
        with self.db.transaction():
            equity, peak = self.mark_account(self.clock())
            account, active, positions = self.db.account(), self.db.active(), self.db.positions()
            committed = sum((number(p["cost_basis"]) for p in positions), ZERO)
            reserved = sum((reservation(o) for o in active), ZERO)
            return {
                "mode": "PAPER ONLY",
                **account,
                "available_cash": number(account["cash"]) - reserved,
                "reserved_cash": reserved,
                "capital_committed": committed,
                "equity_conservative": equity,
                "daily_drawdown": peak - equity,
                "unrealized_pnl_conservative": equity - number(account["cash"]) - committed,
                "positions": positions,
                "open_positions": sum(number(p["quantity"]) > 0 for p in positions),
                "orders": self.db.many("SELECT data FROM orders ORDER BY rowid DESC LIMIT 100"),
                "fills": self.db.fills(),
                "daily_realized_pnl": account.get("daily_realized", {}).get(
                    datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat(), ZERO
                ),
                "all_time_pnl_conservative": equity - number(account["starting_balance"]),
                "return_after_fees_percent": (equity / number(account["starting_balance"]) - 1)
                * 100,
                "rounds": self.db.many("SELECT data FROM rounds ORDER BY rowid DESC LIMIT 100"),
                "config": self.config.model_dump(mode="json"),
                "limits": self.limits.model_dump(mode="json"),
            }

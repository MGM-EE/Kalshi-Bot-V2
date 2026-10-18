"""Paper strategy orchestration with durable intent IDs and pinned position criteria."""

import hashlib
from decimal import ROUND_FLOOR, Decimal

from ..execution.fill_model import adverse_price
from ..execution.models import PaperOrder
from ..markets.orderbook import number
from ..risk.limits import remaining, validate_order
from ..storage.database import encode
from .conditions import evaluate_rules
from .entry import evaluate_entry, price_caps, quote
from .exit import evaluate_exit
from .presentation import entry_constraints, exit_conditions
from .profiles import METRICS, Profile
from .store import StrategyStore

SELL_ACTIONS = {"SELL_ALL", "SELL_PARTIAL", "CUT_LOSS", "PROTECT_PROFIT"}


class StrategyEngine:
    def __init__(self, broker, features):
        self.broker, self.db, self.features = broker, broker.db, features
        self.store = StrategyStore(self.db)
        self.views = {}
        self.last_tick = None
        self._collecting = False
        self._writes, self._intents = {}, {}
        with self.db.transaction():
            self.db.connection.execute(
                "CREATE TABLE IF NOT EXISTS strategy_intents(id TEXT PRIMARY KEY,status TEXT,data TEXT NOT NULL)"
            )
        broker.strategy_guard = self.guard
        broker.strategy_fill_guard = self.fill_guard
        broker.emergency_gate = lambda: self.store.read("control")["emergency"]

    def snapshot(self):
        return {
            "control": self.store.read("control"),
            "decisions": self.views,
            "real_execution_enabled": False,
        }

    def control(self, enabled=None, emergency=None, profile_name=None, profile_version=None):
        with self.db.transaction():
            value = self.store.read("control")
            changed = profile_name is not None or profile_version is not None
            if changed:
                name = profile_name or value["profile_name"]
                version = (
                    profile_version if profile_version is not None else value["profile_version"]
                )
                self.store.get_profile(name, version)
                value.update(profile_name=name, profile_version=version)
            if enabled is not None:
                value["enabled"] = enabled
            if emergency is not None:
                value["emergency"] = emergency
            self.store.write("control", value)
            # Open positions keep original criteria; pending automatic buys are cancelled.
            if changed or enabled is False or emergency is True:
                for order in self.db.active():
                    if order["spec"]["action"] == "buy" and (
                        order.get("strategy_context") or emergency is True
                    ):
                        order.update(
                            status="cancelled",
                            reason="Strategy selection/pause changed; pending entry cancelled",
                        )
                        self.db.save_order(order)
            if changed or enabled is not None:
                for key, raw in self.db.connection.execute(
                    "SELECT key,data FROM strategy_runtime WHERE key LIKE 'entry:%'"
                ).fetchall():
                    import json

                    runtime = json.loads(raw)
                    runtime.update(armed=True, since=None, condition_since={}, qualified=False)
                    self.store.write(key, runtime)
        self.broker.state.changed.set()
        return value

    def _profile(self, control):
        return self.store.get_profile(control["profile_name"], control["profile_version"])

    def _runtime(self, key):
        return (
            self._writes.get(key)
            or self.store.read(key)
            or {
                "episode": 0,
                "armed": True,
                "since": None,
                "condition_since": {},
                "qualified": False,
                "last_checked": 0,
            }
        )

    def _persist(self, key, runtime):
        if self._collecting:
            self._writes[key] = runtime
            return
        with self.db.transaction():
            self.store.write(key, runtime)

    def _intent(self, order_id, spec, context):
        data = {"spec": spec.model_dump(mode="json"), "context": context}
        if self._collecting:
            self._intents[order_id] = data
            return
        with self.db.transaction():
            self.db.connection.execute(
                "INSERT OR IGNORE INTO strategy_intents VALUES(?,'pending',?)",
                (order_id, encode(data)),
            )

    def dispatch(self):
        # An interruption after submit but before marking dispatched retries the identical ID/body.
        pending = self.db.many(
            "SELECT data FROM strategy_intents WHERE status='pending' ORDER BY rowid"
        )
        for item in pending:
            spec = PaperOrder.model_validate(item["spec"])
            self.broker.submit(spec, strategy_context=item["context"])
            with self.db.transaction():
                self.db.connection.execute(
                    "UPDATE strategy_intents SET status='dispatched' WHERE id=?",
                    (spec.client_order_id,),
                )

    def entry_spec(self, profile, asset, outcome, order_id, now):
        target = self.broker.state.assets[asset]
        market = target["market"]
        if not market:
            return None, None
        asks = target["kalshi"].get("book", {}).get(outcome + "_asks", [])
        if not asks:
            return None, None
        with self.db.transaction():
            account = self.db.account()
            from ..risk.limits import reservation

            available = number(account["cash"]) - sum(
                (reservation(o) for o in self.db.active()), Decimal(0)
            )
        budget = (
            profile.sizing.amount
            if profile.sizing.mode == "fixed_usd"
            else available * profile.sizing.amount / 100
        )
        budget = min(
            budget, available, profile.risk.max_entry_cost, self.broker.limits.max_entry_cost
        ).quantize(Decimal(".0001"), rounding=ROUND_FLOOR)
        if budget <= 0:
            return None, None
        ask_cap, _ = price_caps(profile.entry, self.features.metrics(asset, outcome, now))
        limit = adverse_price(ask_cap, "sell", 0, market["price_ranges"])
        if limit is None:
            return None, None
        maximum = min(
            profile.sizing.maximum_quantity,
            profile.risk.max_quantity,
            self.broker.limits.max_quantity,
            budget / number(asks[0][0]),
        )
        initial = quote(
            self.broker,
            asset,
            outcome,
            "buy",
            maximum.quantize(Decimal(".01"), rounding=ROUND_FLOOR),
            limit,
            now,
        )
        if not initial or initial["quantity"] <= 0:
            return None, initial
        low, high = 0, int(initial["quantity"] * 100)
        while low < high:
            mid = (low + high + 1) // 2
            value = quote(self.broker, asset, outcome, "buy", Decimal(mid) / 100, limit, now)
            if value and value["cost"] <= budget:
                low = mid
            else:
                high = mid - 1
        quantity = Decimal(low) / 100
        if quantity < max(
            profile.sizing.minimum_quantity,
            profile.risk.min_quantity,
            self.broker.limits.min_quantity,
        ):
            return None, initial
        value = quote(self.broker, asset, outcome, "buy", quantity, limit, now)
        spec = PaperOrder(
            client_order_id=order_id,
            asset=asset,
            ticker=market["ticker"],
            outcome=outcome,
            action="buy",
            quantity=quantity,
            limit_price=limit,
            max_cost=budget,
            timeout_seconds=profile.entry.fill_timeout_seconds,
            strategy_version=profile.identifier,
            reason=f"BUY {market['outcome_mapping'][outcome]} — configured entry rules confirmed",
        )
        return spec, value

    def _confirmations(self, profile, metrics, runtime, now):
        ts = metrics.get("source_ts")
        last_source = runtime.get("last_source")
        if (
            now - runtime.get("last_checked", 0) > 2.5
            or ts is None
            or (last_source is not None and ts - last_source > 2.5)
        ):
            runtime.update(since=None, condition_since={})
        runtime["last_source"] = ts
        raw = evaluate_rules(
            profile.entry.rules, metrics, confirmation={r.metric: True for r in profile.entry.rules}
        )
        confirmations = {}
        for rule, result in zip(profile.entry.rules, raw["conditions"]):
            if result["state"] == "green" and ts is not None:
                start = runtime["condition_since"].setdefault(rule.metric, ts)
                confirmations[rule.metric] = ts - start >= rule.confirmation_seconds
            else:
                runtime["condition_since"].pop(rule.metric, None)
                confirmations[rule.metric] = False
        runtime["last_checked"] = now
        return confirmations

    def tick(self):
        now = self.broker.clock()
        if self.last_tick is not None and 0 <= now - self.last_tick < 0.25:
            return
        self.last_tick = now
        self._writes, self._intents = {}, {}
        self._collecting = True
        try:
            self._tick_body()
        finally:
            self._collecting = False
        # Confirmation/latch state and intents commit together before any order is submitted.
        with self.db.transaction():
            for key, runtime in self._writes.items():
                self.store.write(key, runtime)
            for order_id, data in self._intents.items():
                self.db.connection.execute(
                    "INSERT OR IGNORE INTO strategy_intents VALUES(?,'pending',?)",
                    (order_id, encode(data)),
                )
        self.dispatch()

    def _tick_body(self):
        now, control = self.broker.clock(), self.store.read("control")
        profile = self._profile(control)
        positions, active = self.db.positions(), self.db.active()
        for asset, target in self.broker.state.assets.items():
            market = target["market"]
            if not market or not market.get("outcome_mapping"):
                self.views[asset] = {
                    "entry": {"eligible": False, "reason": "Market/rules unavailable"}
                }
                continue
            self.views[asset] = {"entries": {}}
            for outcome in ("yes", "no"):
                key = "entry:" + market["ticker"] + ":" + outcome + ":" + profile.identifier
                runtime = self._runtime(key)
                placeholder = "preview-" + hashlib.sha256(key.encode()).hexdigest()[:24]
                spec, estimate = self.entry_spec(profile, asset, outcome, placeholder, now)
                metrics = self.features.metrics(asset, outcome, now)
                if estimate:
                    metrics.update(
                        {k: estimate[k] for k in ("ask", "all_in_price", "spread", "depth")}
                    )
                    metrics["liquidity"] = estimate["depth"]
                confirmations = self._confirmations(profile, metrics, runtime, now)
                result = evaluate_entry(
                    profile, metrics, market["close_time"] - now, estimate, confirmations
                )
                result["execution_conditions"] = entry_constraints(
                    profile, metrics, market["close_time"] - now, estimate
                )
                result["source_ts"] = metrics.get("source_ts")
                for condition in result["conditions"]:
                    metric_key = condition["metric"]
                    condition["unit"] = METRICS[metric_key][0]
                    condition["source"] = (
                        "Kalshi book / CF index"
                        if metric_key
                        in {
                            "ask",
                            "all_in_price",
                            "spread",
                            "depth",
                            "distance",
                            "distance_percent",
                            "losing_side",
                            "approach_velocity",
                            "approach_positive",
                            "observations",
                            "data_age",
                            "normalized_distance",
                        }
                        else "Kalshi trades"
                        if metric_key == "kalshi_whale_flow"
                        else "Kraken / observed indicators"
                    )
                direction = market["outcome_mapping"][outcome]
                if direction not in profile.entry.directions:
                    result.update(eligible=False, reason="Direction disabled")
                held = next(
                    (
                        p
                        for p in positions
                        if p["ticker"] == market["ticker"] and p["outcome"] == outcome
                    ),
                    None,
                )
                has_position = held is not None and number(held["quantity"]) > 0
                pending = any(
                    o["spec"]["ticker"] == market["ticker"] and o["spec"]["outcome"] == outcome
                    for o in active
                )
                cooldown = (
                    held is not None
                    and now - held.get("closed_at", 0) < profile.entry.reentry_cooldown_seconds
                )
                if (
                    held
                    and not has_position
                    and held.get("closed_at", 0) > runtime.get("last_closed", 0)
                ):
                    runtime.update(armed=True, since=None, last_closed=held["closed_at"])
                if not result["eligible"] or not control["enabled"] or control["emergency"]:
                    runtime.update(armed=True, since=None, qualified=False)
                else:
                    ts = metrics.get("source_ts")
                    if ts is not None and runtime["since"] is None:
                        runtime["since"] = ts
                    confirmed = (
                        ts is not None
                        and runtime["since"] is not None
                        and ts - runtime["since"] >= profile.entry.confirmation_seconds
                    )
                    runtime["qualified"] = confirmed
                    if not confirmed:
                        result.update(eligible=False, reason="Entry signal confirmation pending")
                    if (
                        confirmed
                        and runtime["armed"]
                        and spec
                        and not has_position
                        and not pending
                        and not cooldown
                    ):
                        runtime["episode"] += 1
                        runtime["armed"] = False
                        order_id = (
                            "entry-"
                            + hashlib.sha256(f"{key}:{runtime['episode']}".encode()).hexdigest()[
                                :40
                            ]
                        )
                        spec = spec.model_copy(update={"client_order_id": order_id})
                        context = {
                            "profile": profile.model_dump(mode="json"),
                            "entry_key": key,
                            "episode": runtime["episode"],
                            "conditions": result,
                        }
                        self._persist(key, runtime)
                        self._intent(order_id, spec, context)
                if not control["enabled"]:
                    result["operational_state"] = "PAUSED — monitoring only"
                elif has_position or pending or cooldown:
                    result["operational_state"] = "Existing position/order or reentry cooldown"
                self.views[asset]["entries"][outcome] = result
                self._persist(key, runtime)
        self.exits(now, control)

    def exits(self, now, control):
        for position in self.db.positions():
            if number(position["quantity"]) <= 0:
                continue
            context = position.get("strategy_context")
            if not context and not control["emergency"]:
                continue
            profile = (
                Profile.model_validate(context["profile"]) if context else self._profile(control)
            )
            asset, outcome = position["asset"], position["outcome"]
            market = self.broker.state.assets[asset]["market"]
            key = "exit:" + position.get("cycle_id", position["ticker"] + outcome)
            runtime = self.store.read(key) or {}
            if number(position["quantity"]) < number(
                runtime.get("last_quantity", position["quantity"])
            ):
                runtime["partial_taken"] = True
            minimum = (
                adverse_price(0, "buy", Decimal(".0001"), market["price_ranges"])
                if market and market.get("price_ranges")
                else None
            )
            estimate = (
                quote(
                    self.broker, asset, outcome, "sell", number(position["quantity"]), minimum, now
                )
                if minimum
                else None
            )
            metrics = self.features.metrics(asset, outcome, now)
            with self.db.transaction():
                equity, peak = self.broker.mark_account(now)
            hard = self.hard_risk(profile, position, equity, peak)
            result = evaluate_exit(
                profile,
                position,
                market,
                estimate,
                metrics,
                runtime,
                now,
                control["emergency"],
                hard,
            )
            result["conditions"] = exit_conditions(
                profile, position, market, estimate, metrics, runtime, now
            )
            result["profile"] = profile.identifier
            runtime.update(result["state"])
            runtime["last_quantity"] = position["quantity"]
            self.views.setdefault(asset, {}).setdefault("exits", {})[outcome] = {
                k: v for k, v in result.items() if k != "state"
            }
            active = any(
                o["spec"]["ticker"] == position["ticker"]
                and o["spec"]["outcome"] == outcome
                and o["spec"]["action"] == "sell"
                for o in self.db.active()
            )
            source = self.broker.state.sources.get(f"kalshi:{asset}:book", {}).get("received_at", 0)
            if (
                result["action"] in SELL_ACTIONS
                and not active
                and minimum
                and source > runtime.get("last_attempt_source", 0)
                and now - runtime.get("last_attempt_at", 0) >= 1
            ):
                quantity = (number(position["quantity"]) * result["fraction"]).quantize(
                    Decimal(".01"), rounding=ROUND_FLOOR
                )
                if quantity == 0:
                    quantity = number(position["quantity"])
                runtime["attempt"] = runtime.get("attempt", 0) + 1
                runtime.update(last_attempt_source=source, last_attempt_at=now)
                order_id = (
                    "exit-"
                    + hashlib.sha256(f"{key}:{runtime['attempt']}".encode()).hexdigest()[:40]
                )
                spec = PaperOrder(
                    client_order_id=order_id,
                    asset=asset,
                    ticker=position["ticker"],
                    action="sell",
                    outcome=outcome,
                    quantity=quantity,
                    limit_price=minimum,
                    strategy_version=profile.identifier,
                    reason=result["reason"],
                )
                self._intent(
                    order_id,
                    spec,
                    {
                        "profile": profile.model_dump(mode="json"),
                        "exit_key": key,
                        "action": result["action"],
                        "emergency": control["emergency"],
                    },
                )
            self._persist(key, runtime)

    def guard(self, order, spec, now):
        context = order["strategy_context"]
        profile = Profile.model_validate(context["profile"])
        if spec.action == "buy":
            control = self.store.read("control")
            runtime = self.store.read(context["entry_key"])
            if (
                not control["enabled"]
                or control["emergency"]
                or self._profile(control).identifier != profile.identifier
            ):
                raise ValueError("Automatic entry paused or profile changed")
            if (
                not runtime
                or not runtime["qualified"]
                or runtime["episode"] != context["episode"]
                or now - runtime["last_checked"] > 2.5
            ):
                raise ValueError("Entry signal lost confirmation")
            market = self.broker.state.assets[spec.asset]["market"]
            if market["outcome_mapping"][spec.outcome] not in profile.entry.directions:
                raise ValueError("Selected direction is disabled")
            metrics = self.features.metrics(spec.asset, spec.outcome, now)
            estimate = quote(
                self.broker,
                spec.asset,
                spec.outcome,
                "buy",
                remaining(order),
                spec.limit_price,
                now,
            )
            result = evaluate_entry(
                profile,
                metrics,
                market["close_time"] - now,
                estimate,
                {r.metric: True for r in profile.entry.rules},
            )
            if not result["eligible"]:
                raise ValueError("Entry revalidation: " + result["reason"])
            equity, peak = self.broker.mark_account(now)
            from ..risk.limits import reservation

            effective = spec.model_copy(
                update={"max_cost": reservation(order), "quantity": remaining(order)}
            )
            validate_order(
                effective,
                profile.risk,
                self.db.account(),
                self.db.positions(),
                [o for o in self.db.active() if o["id"] != order["id"]],
                self.db.round(spec.ticker),
                equity,
                peak,
                new_entry=number(order["filled_quantity"]) == 0,
            )
        else:
            position = next(
                (
                    p
                    for p in self.db.positions()
                    if p["ticker"] == spec.ticker and p["outcome"] == spec.outcome
                ),
                None,
            )
            if not position:
                raise ValueError("Position unavailable")
            runtime = self.store.read(context["exit_key"]) or {}
            market = self.broker.state.assets[spec.asset]["market"]
            estimate = quote(
                self.broker,
                spec.asset,
                spec.outcome,
                "sell",
                number(position["quantity"]),
                spec.limit_price,
                now,
            )
            metrics = self.features.metrics(spec.asset, spec.outcome, now)
            equity, peak = self.broker.mark_account(now)
            hard = self.hard_risk(profile, position, equity, peak)
            value = evaluate_exit(
                profile,
                position,
                market,
                estimate,
                metrics,
                runtime,
                now,
                self.store.read("control")["emergency"],
                hard,
            )
            if value["action"] not in SELL_ACTIONS:
                raise ValueError("Exit trigger no longer satisfied")

    def fill_guard(self, order, spec, leg, fee, now):
        if spec.action == "sell":
            return True
        profile = Profile.model_validate(order["strategy_context"]["profile"])
        ask_cap, all_in_cap = price_caps(
            profile.entry, self.features.metrics(spec.asset, spec.outcome, now)
        )
        cumulative = number(order["spent"]) + leg["price"] * number(leg["quantity"]) + fee.net
        quantity = number(order["filled_quantity"]) + number(leg["quantity"])
        return quantity > 0 and cumulative / quantity <= all_in_cap and leg["price"] <= ask_cap

    def hard_risk(self, profile, position, equity, peak):
        from ..risk.limits import reservation

        limits = self.broker.limits
        exposures = [
            (p["asset"], p["ticker"], number(p["cost_basis"]))
            for p in self.db.positions()
            if number(p["quantity"]) > 0
        ]
        exposures += [
            (o["spec"]["asset"], o["spec"]["ticker"], reservation(o))
            for o in self.db.active()
            if o["spec"]["action"] == "buy"
        ]
        total = sum((cost for _, _, cost in exposures), Decimal(0))
        asset = sum((cost for a, _, cost in exposures if a == position["asset"]), Decimal(0))
        market = sum((cost for _, t, cost in exposures if t == position["ticker"]), Decimal(0))
        return (
            peak - equity >= min(profile.risk.max_daily_drawdown, limits.max_daily_drawdown)
            or number(position["cost_basis"])
            > min(profile.risk.max_loss_per_trade, limits.max_loss_per_trade)
            or total > min(profile.risk.max_total_exposure, limits.max_total_exposure)
            or asset > min(profile.risk.max_asset_exposure, limits.max_asset_exposure)
            or market > min(profile.risk.max_round_exposure, limits.max_round_exposure)
        )

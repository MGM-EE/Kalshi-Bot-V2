"""Venue-separated observed flow, L2 persistence and transparent risk scores."""

from collections import OrderedDict, deque
from decimal import Decimal

from ..markets.discovery import timestamp
from ..markets.orderbook import number
from ..settings import ASSETS
from ..storage.database import encode
from .choppiness import ConfirmedRisk, choppiness
from .config import IndicatorConfig
from .liquidity import LiquidityPersistence
from .swing_risk import swing_risk
from .volatility import price_statistics
from .whale_flow import WhaleFlow


class IndicatorEngine:
    def __init__(self, state, features, database):
        self.state, self.features, self.db = state, features, database
        with self.db.transaction():
            self.db.connection.execute(
                "CREATE TABLE IF NOT EXISTS indicator_versions(version INTEGER PRIMARY KEY,data TEXT NOT NULL)"
            )
            data = self.db.one("SELECT data FROM indicator_versions ORDER BY version DESC LIMIT 1")
            if data is None:
                data = IndicatorConfig().model_dump(mode="json")
                self.db.connection.execute(
                    "INSERT INTO indicator_versions VALUES(?,?)", (data["version"], encode(data))
                )
        self.configure(IndicatorConfig.model_validate(data))

    def configure(self, config):
        self.config = config
        self.flow, self.walls, self.seen = {}, {}, {}
        self.trade_modes = {}
        self.line, self.rounds, self.cache = {}, {}, {}
        self.risk_states = {}
        for asset, thresholds in config.assets.items():
            self.flow[(asset, "kraken")] = WhaleFlow(
                config, thresholds.spot_fixed_usd, thresholds.burst_usd
            )
            self.flow[(asset, "kalshi")] = WhaleFlow(
                config,
                thresholds.kalshi_fixed_contracts
                if thresholds.kalshi_size_unit == "contracts"
                else thresholds.kalshi_fixed_usd,
            )
            self.walls[(asset, "kraken")] = LiquidityPersistence(
                thresholds.spot_wall_usd, config.book_gap_seconds
            )
            self.walls[(asset, "kalshi")] = LiquidityPersistence(
                thresholds.kalshi_wall_contracts, config.book_gap_seconds, notional=False
            )
            for venue in ("kraken", "kalshi"):
                self.seen[(asset, venue)] = OrderedDict()
            self.line[asset] = {
                "crossings": deque(maxlen=1000),
                "above_seconds": 0,
                "below_seconds": 0,
                "missing_seconds": 0,
                "last_ts": None,
                "sign": 0,
            }
            self.risk_states[(asset, "chop")] = ConfirmedRisk(
                config.risk_high_threshold, config.risk_hysteresis, config.confirmation_seconds
            )
            for outcome in ("yes", "no"):
                self.risk_states[(asset, outcome)] = ConfirmedRisk(
                    config.risk_high_threshold, config.risk_hysteresis, config.confirmation_seconds
                )

    def save_config(self, config, acknowledge_open_positions=False):
        config = IndicatorConfig.model_validate(config)
        with self.db.transaction():
            if not acknowledge_open_positions and any(
                number(p["quantity"]) > 0 for p in self.db.positions()
            ):
                raise ValueError(
                    "Changing indicator definitions affects future observations for open positions; acknowledge explicitly"
                )
            if config.version <= self.config.version:
                raise ValueError("Indicator versions must increase")
            self.db.connection.execute(
                "INSERT INTO indicator_versions VALUES(?,?)",
                (config.version, encode(config.model_dump(mode="json"))),
            )
        self.configure(config)
        return config

    def observe(self, source):
        parts = source.split(":")
        if len(parts) != 3 or parts[1] not in ASSETS or parts[0] not in {"kalshi", "kraken"}:
            return
        venue, asset, channel = parts
        self.cache.pop(asset, None)
        target = self.state.assets[asset]
        market = target["market"]
        ticker = market["ticker"] if market else None
        if ticker != self.rounds.get(asset):
            self.rounds[asset] = ticker
            thresholds = self.config.assets[asset]
            self.flow[(asset, "kalshi")] = WhaleFlow(
                self.config,
                thresholds.kalshi_fixed_contracts
                if thresholds.kalshi_size_unit == "contracts"
                else thresholds.kalshi_fixed_usd,
            )
            self.walls[(asset, "kalshi")].invalidate()
            self.seen[(asset, "kalshi")].clear()
            self.line[asset].update(
                crossings=deque(maxlen=1000),
                above_seconds=0,
                below_seconds=0,
                missing_seconds=0,
                last_ts=None,
                sign=0,
            )
        health = self.state.sources[source]
        ts = health["exchange_ts"] if health["exchange_ts"] is not None else health["received_at"]
        if channel == "trade":
            previous_mode = self.trade_modes.get((asset, venue))
            self.trade_modes[(asset, venue)] = health["status"]
            if previous_mode is not None and previous_mode != health["status"]:
                old = self.flow[(asset, venue)]
                self.flow[(asset, venue)] = WhaleFlow(self.config, old.fixed, old.burst_threshold)
        if channel == "trade" and health["status"] not in {"live", "polling"}:
            old = self.flow[(asset, venue)]
            self.flow[(asset, venue)] = WhaleFlow(self.config, old.fixed, old.burst_threshold)
            return
        if channel == "book":
            book = target[venue].get("book", {})
            if health["status"] not in {"live", "polling"} or not book.get("valid"):
                self.walls[(asset, venue)].invalidate()
            else:
                self.walls[(asset, venue)].update(
                    book.get("bids" if venue == "kraken" else "yes_bids", []),
                    book.get("asks" if venue == "kraken" else "yes_asks", []),
                    ts,
                )
        if (
            channel == "reference"
            and market
            and market.get("current_index") is not None
            and market.get("opening_reference") is not None
        ):
            line = self.line[asset]
            if line["last_ts"] is None or ts > line["last_ts"]:
                distance = number(market["current_index"]) - number(market["opening_reference"])
                sign = 1 if distance > 0 else -1 if distance < 0 else 0
                if line["last_ts"] is not None:
                    dt = ts - line["last_ts"]
                    if dt <= self.config.book_gap_seconds:
                        if line["sign"] and sign and sign != line["sign"]:
                            line["crossings"].append(ts)
                        if line["sign"]:
                            line["above_seconds" if line["sign"] > 0 else "below_seconds"] += dt
                    else:
                        line["missing_seconds"] += dt
                line["last_ts"], line["sign"] = ts, sign
        if channel == "trade" and health["status"] in {"live", "polling"}:
            seen = self.seen[(asset, venue)]
            pending = []
            for row in reversed(target[venue].get("trades", [])):
                trade_id = row.get("id") if venue == "kraken" else row.get("trade_id")
                if trade_id in seen:
                    break
                if trade_id is not None:
                    pending.append((trade_id, row))
            for trade_id, row in reversed(pending):
                seen[trade_id] = True
                if len(seen) > self.config.max_window_trades:
                    seen.popitem(last=False)
                try:
                    if venue == "kraken":
                        event_ts, qty, price = row["exchange_ts"], row["quantity"], row["price"]
                        sign = (
                            1
                            if row["aggressor_side"] == "buy"
                            else -1
                            if row["aggressor_side"] == "sell"
                            else 0
                        )
                        size = None
                    else:
                        if row.get("is_block_trade"):
                            continue
                        event_ts = row.get("ts_ms")
                        event_ts = (
                            float(event_ts) / 1000
                            if event_ts is not None
                            else timestamp(row.get("created_time") or row.get("ts"))
                        )
                        qty = row["count_fp"]
                        side = row.get("taker_outcome_side")
                        sign = 1 if side == "yes" else -1 if side == "no" else 0
                        price = (
                            row["no_price_dollars"] if side == "no" else row["yes_price_dollars"]
                        )
                        size = (
                            qty
                            if self.config.assets[asset].kalshi_size_unit == "contracts"
                            else number(qty) * number(price)
                        )
                    if event_ts is not None:
                        self.flow[(asset, venue)].add(event_ts, qty, price, sign, size)
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    continue

    def asset_snapshot(self, asset, now):
        bucket = int(now * 4)
        cached = self.cache.get(asset)
        if cached and cached[0] == bucket:
            return cached[1]
        target, thresholds = self.state.assets[asset], self.config.assets[asset]
        market = target["market"]
        spot = self.flow[(asset, "kraken")].snapshot(now)
        contracts = self.flow[(asset, "kalshi")].snapshot(now)
        contracts["source_mode"] = self.state.sources.get(f"kalshi:{asset}:trade", {}).get(
            "status", "unavailable"
        )
        if contracts["source_mode"] != "live":
            for window in contracts["windows"].values():
                window["complete"] = False
        spot_walls = self.walls[(asset, "kraken")].snapshot(now)
        kalshi_walls = self.walls[(asset, "kalshi")].snapshot(now)
        stats = price_statistics(self.features.history[asset]["spot"], now, 60)
        short = price_statistics(self.features.history[asset]["spot"], now, 15)
        line = self.line[asset]
        crossings = sum(now - ts <= 60 for ts in line["crossings"])
        index_ready = (
            len(self.features.history[asset]["index"]) >= 6
            and line["last_ts"] is not None
            and 0 <= now - line["last_ts"] <= 3
        )
        chop = choppiness(
            stats if index_ready else None,
            crossings,
            short["realized_volatility_percent"] if short else Decimal(0),
            stats["realized_volatility_percent"] if stats else Decimal(0),
            self.config.chop_weights,
        )
        source_ts = (
            self.features.history[asset]["spot"][-1][0]
            if self.features.history[asset]["spot"]
            else now
        )
        chop_state = self.risk_states[(asset, "chop")].update(
            chop["score"] if chop else None, source_ts
        )
        swings = {}
        for outcome in ("yes", "no"):
            score = None
            if (
                market
                and market.get("outcome_mapping")
                and market.get("current_index") is not None
                and market.get("opening_reference") is not None
                and line["last_ts"] is not None
                and 0 <= now - line["last_ts"] <= 3
            ):
                book = target["kalshi"].get("book", {})
                bids, asks = book.get(outcome + "_bids", []), book.get(outcome + "_asks", [])
                if bids and asks and book.get("valid"):
                    direction = 1 if market["outcome_mapping"][outcome] == "UP" else -1
                    momentum = stats["momentum_percent"].get("5") if stats else None
                    adverse = (
                        max(Decimal(0), -momentum * 100 * direction)
                        if momentum is not None
                        else Decimal(0)
                    )
                    score = swing_risk(
                        abs(number(market["current_index"]) - number(market["opening_reference"])),
                        number(market["current_index"]),
                        market["close_time"] - now,
                        stats,
                        thresholds.extreme_volatility_percent,
                        number(asks[0][0]) - number(bids[0][0]),
                        adverse,
                        self.config.swing_weights,
                    )
            swings[outcome] = {
                "value": score,
                "state": self.risk_states[(asset, outcome)].update(
                    score["score"] if score else None, source_ts
                ),
            }
        book = target["kraken"].get("book", {})
        bid_size = sum((number(q) for _, q in book.get("bids", [])), Decimal(0))
        ask_size = sum((number(q) for _, q in book.get("asks", [])), Decimal(0))
        imbalance = (
            (bid_size - ask_size) / (bid_size + ask_size)
            if book.get("valid") and bid_size + ask_size
            else None
        )
        flow = spot["windows"]["60"]
        gross_flow = flow["large_buy_usd"] + flow["large_sell_usd"]
        flow_ratio = flow["net_large_usd"] / gross_flow if gross_flow else Decimal(0)
        persistence = min(
            Decimal(1),
            max(
                Decimal(-1),
                Decimal(
                    str(
                        spot_walls["bid_persistence_seconds"]
                        - spot_walls["ask_persistence_seconds"]
                    )
                )
                / 30,
            ),
        )
        pressure_score = (
            50
            + 50
            * (Decimal(".5") * flow_ratio + Decimal(".3") * imbalance + Decimal(".2") * persistence)
            if imbalance is not None and spot_walls["fresh"] and spot["fresh"]
            else None
        )
        potential_absorption = any(
            level["replenishments"] > 0
            and (
                (level["side"] == "bid" and spot["windows"]["5"]["cvd_quantity"] < 0)
                or (level["side"] == "ask" and spot["windows"]["5"]["cvd_quantity"] > 0)
            )
            for level in spot_walls["levels"]
        )
        result = {
            "config_version": self.config.version,
            "kraken_whale_flow": spot,
            "kalshi_whale_flow": contracts,
            "large_order_persistence": {"kraken": spot_walls, "kalshi": kalshi_walls},
            "spot_statistics": stats,
            "choppiness": chop,
            "choppiness_state": chop_state,
            "swing_risk": swings,
            "line": {**line, "crossings": list(line["crossings"]), "crossings_60s": crossings},
            "buy_sell_pressure_setup": {
                "score": pressure_score,
                "complete_window": flow["complete"],
                "book_imbalance": imbalance,
                "potential_absorption": potential_absorption,
                "interpretation": "Observed pressure / potential setup, not intent or a probability",
            },
            "holdings_visibility": "Unknown; visible orders do not reveal investment holdings",
        }
        self.cache[asset] = bucket, result
        return result

    def metrics(self, asset, outcome, now):
        data = self.asset_snapshot(asset, now)
        market = self.state.assets[asset]["market"]
        if not market or not market.get("outcome_mapping"):
            return {}
        direction = 1 if market["outcome_mapping"][outcome] == "UP" else -1
        spot = data["kraken_whale_flow"]
        contract = data["kalshi_whale_flow"]
        seconds = str(self.config.flow_window_seconds)
        s, c = spot["windows"][seconds], contract["windows"][seconds]
        result = {"flow_imbalance": None}
        if spot["fresh"] and s["complete"]:
            result["whale_flow"] = s["net_large_usd"] * direction
            result["flow_imbalance"] = (
                s["cvd_quantity"] / s["volume_quantity"] * direction
                if s["volume_quantity"]
                else None
            )
            result["whale_duration"] = (
                Decimal(str(spot["pressure_duration"]))
                if (s["net_large_usd"] * direction > 0)
                else Decimal(0)
            )
        if contract["fresh"] and c["complete"] and c["unknown_direction_count"] == 0:
            result["kalshi_whale_flow"] = c["net_large_quantity"] * (1 if outcome == "yes" else -1)
        walls = data["large_order_persistence"]["kraken"]
        if walls["fresh"]:
            result["persistent_liquidity"] = Decimal(
                str(
                    walls["bid_persistence_seconds"]
                    if direction == 1
                    else walls["ask_persistence_seconds"]
                )
            )
        if data["choppiness"] and data["choppiness_state"] in {"LOW", "CAUTION", "HIGH"}:
            result["choppiness"] = data["choppiness"]["score"]
        risk = data["swing_risk"][outcome]["value"]
        if risk and data["swing_risk"][outcome]["state"] in {"LOW", "CAUTION", "HIGH"}:
            result["swing_risk"] = risk["score"]
        if data["spot_statistics"]:
            result["volatility"] = data["spot_statistics"]["realized_volatility_percent"]
        others = []
        for other in ASSETS:
            if other != asset:
                stats = price_statistics(self.features.history[other]["spot"], now, 15)
                move = stats["momentum_percent"]["5"] if stats else None
                if move is not None:
                    others.append(1 if move * direction > 0 else -1 if move * direction < 0 else 0)
        if others:
            result["cross_market"] = Decimal(sum(others)) / len(others)
        if "whale_flow" in result and "kalshi_whale_flow" in result:
            a, b = result["whale_flow"], result["kalshi_whale_flow"]
            result["directional_agreement"] = (
                "SUPPORTIVE"
                if a > 0 and b > 0
                else "ADVERSE"
                if a < 0 and b < 0
                else "DIVERGENT"
                if a * b < 0
                else "NEUTRAL"
            )
        else:
            result["directional_agreement"] = "INSUFFICIENT DATA"
        return result

    def snapshot(self, now):
        return {asset: self.asset_snapshot(asset, now) for asset in ASSETS}

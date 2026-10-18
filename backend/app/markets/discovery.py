"""Conservative directional mapping from explicit outcome labels and contract rules."""

import re
from datetime import datetime
from decimal import Decimal

INDEX_IDS = {"BTC": "BRTI", "ETH": "ETHUSD_RTI", "SOL": "SOLUSD_RTI"}
RULE_INDEX_IDS = {"BTC": "BRTI", "ETH": "ETHUSDRTI", "SOL": "SOLUSDRTI"}
AVERAGE_RULE = re.compile(
    r"^If the simple average of the sixty seconds of CF Benchmarks' ([A-Z0-9_]+) before .+? "
    r"is (at least|greater than|less than|at most) the simple average of the sixty seconds "
    r"of CF Benchmarks' \1 before .+?, then the market resolves to Yes\.$"
)


def timestamp(value):
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Exchange timestamp must include a timezone")
    return parsed.timestamp()


def normalize_market(raw, series, asset):
    rules = "\n".join(filter(None, [raw.get("rules_primary"), raw.get("rules_secondary")]))
    yes = raw.get("yes_sub_title", "").strip().lower()
    no = raw.get("no_sub_title", "").strip().lower()
    mapping = None
    if yes in {"up", "higher"} and no in {"down", "lower"}:
        mapping = {"yes": "UP", "no": "DOWN"}
    elif no in {"up", "higher"} and yes in {"down", "lower"}:
        mapping = {"yes": "DOWN", "no": "UP"}
    contract = AVERAGE_RULE.fullmatch(raw.get("rules_primary", ""))
    parsed_comparison = None
    if contract and contract[1] in {INDEX_IDS.get(asset), RULE_INDEX_IDS.get(asset)}:
        parsed_comparison = {
            "at least": "greater_or_equal",
            "greater than": "greater",
            "less than": "less",
            "at most": "less_or_equal",
        }[contract[2]]
        rule_mapping = (
            {"yes": "UP", "no": "DOWN"}
            if parsed_comparison.startswith("greater")
            else {"yes": "DOWN", "no": "UP"}
        )
        if mapping is None:
            mapping = rule_mapping
    # Labels alone don't prove the comparison, benchmark, or tie treatment.
    issues = []
    if mapping is None:
        issues.append("Explicit UP/DOWN outcome labels unavailable; mapping unverified")
    if not rules:
        issues.append("Contract rules unavailable")
    if not parsed_comparison:
        issues.append("Averaging rule or index identity unrecognized; contract review required")
    if not re.search(r"CF\s*Benchmarks", rules, re.I):
        issues.append("CF Benchmarks reference not established by contract text")
    if raw.get("strike_type") not in {"greater", "greater_or_equal", "less", "less_or_equal"}:
        issues.append("Unsupported or unspecified strike comparison")
    if parsed_comparison and parsed_comparison != raw.get("strike_type"):
        issues.append("Contract wording contradicts strike comparison or tie treatment")
    if mapping:
        higher_yes = raw.get("strike_type") in {"greater", "greater_or_equal"}
        if higher_yes != (mapping["yes"] == "UP"):
            issues.append("Outcome labels contradict strike comparison")
    opening = (
        raw.get("floor_strike")
        if raw.get("strike_type") in {"greater", "greater_or_equal"}
        else raw.get("cap_strike")
    )
    if opening is None:
        issues.append("Opening reference missing; never substitute Kraken spot")
    opened, closed = timestamp(raw["open_time"]), timestamp(raw["close_time"])
    if closed - opened != 900:
        issues.append("Trading window is not exactly 15 minutes; manual contract review required")
    return {
        "asset": asset,
        "ticker": raw["ticker"],
        "event_ticker": raw["event_ticker"],
        "title": raw.get("title", ""),
        "status": raw["status"],
        "open_time": opened,
        "close_time": closed,
        "expected_expiration_time": raw.get("expected_expiration_time"),
        "expiration_time": raw.get("expiration_time"),
        "settlement_ts": raw.get("settlement_ts"),
        "settlement_timer_seconds": raw.get("settlement_timer_seconds"),
        "rules": rules,
        "outcome_mapping": mapping if not issues else None,
        "rule_checks": issues,
        "opening_reference": opening,
        "opening_reference_source": "Kalshi market strike (not a recomputed average)",
        "comparison": raw.get("strike_type"),
        "index_id": INDEX_IDS[asset] if parsed_comparison else None,
        "averaging_window_seconds": 60 if parsed_comparison else None,
        "settlement_rounding_decimal_places": raw.get("custom_strike", {}).get("round_digits"),
        "result": raw.get("result"),
        "settlement_value": raw.get("expiration_value"),
        "contract_url": series.get("contract_url"),
        "contract_terms_url": series.get("contract_terms_url"),
        "settlement_sources": series.get("settlement_sources", []),
        "fee_type": series.get("fee_type"),
        "fee_multiplier": series.get("fee_multiplier"),
        "price_ranges": raw.get("price_ranges"),
        "price_level_structure": raw.get("price_level_structure"),
        "volume_contracts": raw.get("volume_fp", raw.get("volume")),
        "open_interest_contracts": raw.get("open_interest_fp", raw.get("open_interest")),
        "reference_status": "unavailable: waiting for authenticated CF Benchmarks stream",
        "current_index": None,
        "projected_settlement_average": None,
    }


def select_markets(markets, now):
    current = [
        m
        for m in markets
        if m["status"] in {"open", "active"} and m["open_time"] <= now < m["close_time"]
    ]
    upcoming = sorted([m for m in markets if m["open_time"] > now], key=lambda m: m["open_time"])
    return min(current, key=lambda m: m["close_time"], default=None), upcoming[:4]

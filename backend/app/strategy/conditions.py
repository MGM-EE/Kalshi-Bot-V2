"""Transparent condition states; scores are descriptive, never probabilities."""

from decimal import Decimal

from ..markets.orderbook import number


def evaluate_rules(rules, metrics, mode="STRICT", weighted_threshold=70, confirmation=None):
    statuses = []
    earned, total, blocked = Decimal(0), Decimal(0), False
    confirmation = confirmation or {}
    for rule in rules:
        value = metrics.get(rule.metric)
        state, passed, reason = (
            "gray",
            False,
            "Disabled" if not rule.enabled else "Insufficient data",
        )
        if rule.enabled:
            total += rule.weight
            if value is not None:
                value = number(value)
                passed = (
                    value >= rule.threshold if rule.operator == "min" else value <= rule.threshold
                )
                if rule.operator == "between":
                    passed = rule.threshold <= value <= rule.upper
                near = (
                    value >= rule.threshold - rule.warning_band
                    if rule.operator == "min"
                    else value <= rule.threshold + rule.warning_band
                )
                if rule.operator == "between":
                    near = (
                        rule.threshold - rule.warning_band
                        <= value
                        <= rule.upper + rule.warning_band
                    )
                confirmed = confirmation.get(rule.metric, rule.confirmation_seconds == 0)
                state = "green" if passed and confirmed else "yellow" if passed or near else "red"
                passed = passed and confirmed
                reason = (
                    "Met and confirmed"
                    if passed
                    else "Confirming"
                    if state == "yellow"
                    else "Outside threshold"
                )
            if passed:
                earned += rule.weight
            if rule.required and not passed:
                blocked = True
        statuses.append(
            {
                "metric": rule.metric,
                "value": value,
                "state": state,
                "threshold": rule.threshold,
                "upper": rule.upper,
                "reason": reason,
                "enabled": rule.enabled,
                "required": rule.required,
            }
        )
    score = earned / total * 100 if total else Decimal(0)
    eligible = not blocked and (mode == "STRICT" or score >= weighted_threshold)
    return {
        "eligible": eligible,
        "score": score,
        "conditions": statuses,
        "score_interpretation": "Descriptive rule score; not a probability",
    }

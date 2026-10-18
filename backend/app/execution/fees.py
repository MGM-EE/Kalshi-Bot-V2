"""Kalshi fee model plus documented per-order rounding/rebate accumulator."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ..markets.orderbook import number

MICRO = Decimal("0.000001")


@dataclass(frozen=True)
class Fee:
    trade: Decimal
    rounding: Decimal
    rebate: Decimal
    accumulator: Decimal

    @property
    def net(self):
        return self.trade + self.rounding - self.rebate


def fee_rate(fee_type, multiplier, maker=False):
    multiplier = number(multiplier)
    if multiplier < 0:
        raise ValueError("Negative fee multiplier")
    maker_rates = {
        "quadratic": "0",
        "quadratic_with_maker_fees": "0.0175",
        "quadratic_with_combo_maker_fees": "0.035",
    }
    if fee_type not in maker_rates:
        raise ValueError("Unsupported series/event fee structure; paper execution blocked")
    return multiplier * Decimal(maker_rates[fee_type] if maker else "0.07")


def rounded_fee(revenue, model_fee, accumulator=Decimal(0), precision=Decimal("0.01")):
    revenue, model_fee, accumulator = map(number, (revenue, model_fee, accumulator))
    if model_fee < 0 or accumulator < 0 or precision not in {Decimal("0.01"), Decimal("0.0001")}:
        raise ValueError("Invalid fee inputs")
    trade = model_fee.quantize(MICRO, rounding=ROUND_CEILING)
    aligned = (revenue - trade).quantize(precision, rounding=ROUND_FLOOR)
    rounding = revenue - trade - aligned
    carried = accumulator + rounding
    rebate = min(
        carried.quantize(precision, rounding=ROUND_FLOOR),
        (trade + rounding).quantize(precision, rounding=ROUND_FLOOR),
    )
    return Fee(trade, rounding, rebate, carried - rebate)


def fill_fee(
    price,
    quantity,
    action,
    fee_type,
    multiplier,
    maker=False,
    accumulator=Decimal(0),
    precision=Decimal("0.01"),
):
    price, quantity = number(price), number(quantity)
    if (
        action not in {"buy", "sell"}
        or not 0 < price < 1
        or quantity <= 0
        or quantity % Decimal("0.01")
    ):
        raise ValueError("Invalid fill price, quantity, or action")
    revenue = price * quantity * (-1 if action == "buy" else 1)
    model = fee_rate(fee_type, multiplier, maker) * quantity * price * (1 - price)
    return rounded_fee(revenue, model, accumulator, precision)

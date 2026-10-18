"""Validated, paper-only order and simulator configuration contracts."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ORDER_QUANTITY = Decimal("100000")


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    asset: Literal["BTC", "ETH", "SOL"]
    ticker: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")
    action: Literal["buy", "sell"]
    outcome: Literal["yes", "no"]
    quantity: Decimal = Field(gt=0, le=MAX_ORDER_QUANTITY, decimal_places=2)
    limit_price: Decimal = Field(gt=0, lt=1, decimal_places=4)
    mode: Literal["marketable", "passive"] = "marketable"
    max_cost: Decimal | None = Field(default=None, gt=0, decimal_places=4)
    timeout_seconds: int = Field(default=30, ge=1, le=900)
    strategy_version: str = Field(default="MANUAL_PHASE2:1", min_length=1, max_length=100)
    reason: str = Field(default="Manual paper research order", min_length=1, max_length=500)

    @model_validator(mode="after")
    def budget_required(self):
        if self.action == "buy" and self.max_cost is None:
            raise ValueError("Paper buys require an explicit maximum total cost, including fees")
        return self


class PaperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "PAPER_EXECUTION:1"
    starting_balance: Decimal = Field(default=Decimal("1000.00"), gt=0, decimal_places=2)
    balance_precision: Literal["0.01", "0.0001"] = "0.01"
    latency_ms: int = Field(default=250, ge=0, le=10000)
    slippage_dollars: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.10"))
    max_slippage_dollars: Decimal = Field(default=Decimal("0.02"), ge=0, le=Decimal("0.10"))
    depth_participation: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    queue_multiplier: Decimal = Field(default=Decimal("1.5"), ge=1, le=10)
    queue_buffer_contracts: Decimal = Field(default=Decimal("1.00"), ge=0)
    max_data_age_seconds: float = Field(default=15, gt=0, le=60)


class RiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "PAPER_RISK:1"
    max_entry_cost: Decimal = Field(default=Decimal("25"), gt=0)
    max_round_exposure: Decimal = Field(default=Decimal("100"), gt=0)
    max_asset_exposure: Decimal = Field(default=Decimal("200"), gt=0)
    max_total_exposure: Decimal = Field(default=Decimal("500"), gt=0)
    max_entries_per_round: int = Field(default=10, ge=1)
    max_round_trips_per_round: int = Field(default=10, ge=1)
    max_positions: int = Field(default=6, ge=1)
    min_quantity: Decimal = Field(default=Decimal("0.01"), gt=0)
    max_quantity: Decimal = Field(default=Decimal("1000"), gt=0)
    max_loss_per_trade: Decimal = Field(default=Decimal("25"), gt=0)
    max_loss_per_round: Decimal = Field(default=Decimal("50"), gt=0)
    max_daily_drawdown: Decimal = Field(default=Decimal("100"), gt=0)
    max_consecutive_losses: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def quantities(self):
        if self.max_quantity < self.min_quantity:
            raise ValueError("Maximum quantity cannot be below minimum quantity")
        return self

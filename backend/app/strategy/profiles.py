"""Immutable versioned research profiles; no profitability or probability claims."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..execution.models import RiskLimits
from ..storage.database import encode

METRICS = {
    "ask": ("USD/contract", "Executable Kalshi ask"),
    "all_in_price": ("USD/contract", "Depth, slippage and fees per estimated filled contract"),
    "spread": ("USD/contract", "Executable ask minus bid"),
    "depth": ("contracts", "Usable buy-side depth within the limit"),
    "distance": ("USD", "Absolute CF index distance from the official opening strike"),
    "distance_percent": ("percent", "CF distance divided by opening strike, times 100"),
    "normalized_distance": (
        "standard deviations",
        "CF distance divided by recent index variability",
    ),
    "losing_side": ("boolean", "Selected direction is currently below its settlement line"),
    "approach_velocity": ("USD/second", "Decrease in absolute CF distance over observed ticks"),
    "approach_positive": ("boolean", "Observed CF distance is strictly decreasing"),
    "momentum": ("score -100..100", "Directional spot return in basis points, clipped at 100"),
    "consistency": ("fraction", "Fraction of directional spot changes supporting the outcome"),
    "contract_acceleration": (
        "USD/second squared",
        "Recent executable contract-price acceleration",
    ),
    "divergence": ("percent", "Absolute Kraken/CF divergence"),
    "observations": ("count", "Distinct current-round CF observations"),
    "data_age": ("seconds", "Age of the primary CF source timestamp"),
    "whale_flow": ("Kraken USD", "Directional large-trade net notional; Phase 4"),
    "kalshi_whale_flow": ("contracts", "Directional Kalshi large-trade flow; Phase 4"),
    "flow_imbalance": ("fraction -1..1", "Directional spot aggressive volume imbalance"),
    "persistent_liquidity": ("seconds", "Supporting aggregated price-level persistence; Phase 4"),
    "whale_duration": ("seconds", "Sustained favorable large-trade pressure; Phase 4"),
    "choppiness": ("score 0..100", "Transparent chop score; Phase 4"),
    "swing_risk": ("score 0..100", "Transparent adverse-movement risk; Phase 4"),
    "volatility": ("percent", "Realized spot return variability; Phase 4"),
    "liquidity": ("contracts", "Total visible selected-outcome depth"),
    "cross_market": ("score -1..1", "Directional agreement across available assets; Phase 4"),
}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric: str
    operator: Literal["min", "max", "between"] = "min"
    threshold: Decimal
    upper: Decimal | None = None
    enabled: bool = True
    required: bool = True
    weight: Decimal = Field(default=Decimal(1), gt=0, le=100)
    warning_band: Decimal = Field(default=Decimal(0), ge=0)
    confirmation_seconds: float = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def known_metric(self):
        if self.metric not in METRICS:
            raise ValueError("Unknown strategy metric")
        if self.operator == "between" and (self.upper is None or self.upper < self.threshold):
            raise ValueError("Between rules require an ordered upper bound")
        return self


class EntrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["STRICT", "WEIGHTED"] = "STRICT"
    weighted_threshold: Decimal = Field(default=Decimal(70), ge=0, le=100)
    earliest_remaining_seconds: float = Field(default=600, ge=0, le=86400)
    latest_remaining_seconds: float = Field(default=120, ge=0, le=86400)
    minimum_remaining_after_entry: float = Field(default=120, ge=0)
    fill_timeout_seconds: int = Field(default=5, ge=1, le=900)
    confirmation_seconds: float = Field(default=1, ge=0, le=60)
    reentry_cooldown_seconds: float = Field(default=10, ge=0, le=3600)
    max_ask: Decimal = Field(default=Decimal(".40"), gt=0, lt=1)
    max_all_in_price: Decimal = Field(default=Decimal(".40"), gt=0, le=2)
    trending_momentum_threshold: Decimal = Field(default=Decimal(5), ge=0, le=100)
    trending_max_ask: Decimal | None = Field(default=None, gt=0, lt=1)
    trending_max_all_in: Decimal | None = Field(default=None, gt=0, le=2)
    nontrending_max_ask: Decimal | None = Field(default=None, gt=0, lt=1)
    nontrending_max_all_in: Decimal | None = Field(default=None, gt=0, le=2)
    max_spread: Decimal = Field(default=Decimal(".05"), ge=0, lt=1)
    max_slippage: Decimal = Field(default=Decimal(".02"), ge=0, lt=1)
    min_depth: Decimal = Field(default=Decimal(".01"), gt=0)
    directions: tuple[Literal["UP", "DOWN"], ...] = ("UP", "DOWN")
    rules: tuple[Rule, ...] = (
        Rule(metric="losing_side", threshold=1),
        Rule(metric="approach_velocity", threshold=Decimal("0.000001")),
        Rule(metric="observations", threshold=3),
    )

    @model_validator(mode="after")
    def ranges(self):
        if self.latest_remaining_seconds > self.earliest_remaining_seconds or not self.directions:
            raise ValueError("Invalid entry window or empty direction selection")
        if len({r.metric for r in self.rules}) != len(self.rules):
            raise ValueError("Each metric may occur only once per profile")
        return self


class ExitSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    minimum_hold_seconds: float = Field(default=0, ge=0)
    maximum_hold_seconds: float = Field(default=300, gt=0)
    earliest_remaining_seconds: float = Field(default=900, ge=0)
    latest_remaining_seconds: float = Field(default=30, ge=0)
    target_price: Decimal = Field(default=Decimal(".85"), gt=0, lt=1)
    minimum_net_profit: Decimal = Field(default=Decimal(".25"), ge=0)
    minimum_net_return: Decimal = Field(default=Decimal(".05"), ge=0)
    maximum_loss: Decimal = Field(default=Decimal("5"), gt=0)
    trailing_price_enabled: bool = True
    trailing_price_drop: Decimal = Field(default=Decimal(".05"), gt=0, lt=1)
    trailing_profit_enabled: bool = True
    trailing_profit_drop: Decimal = Field(default=Decimal("1"), gt=0)
    protection_enabled: bool = True
    protection_trigger: Decimal = Field(default=Decimal("1"), ge=0)
    protected_profit: Decimal = Field(default=Decimal(".25"), ge=0)
    flow_reversal_enabled: bool = False
    adverse_flow_threshold: Decimal = Field(default=Decimal(".3"), gt=0, le=1)
    whale_reversal_enabled: bool = False
    adverse_whale_usd: Decimal = Field(default=Decimal("100000"), gt=0)
    trend_break_enabled: bool = False
    adverse_momentum: Decimal = Field(default=Decimal("5"), gt=0)
    chop_exit_enabled: bool = False
    maximum_choppiness: Decimal = Field(default=Decimal("80"), ge=0, le=100)
    reference_cross_enabled: bool = True
    liquidity_exit_enabled: bool = False
    minimum_exit_depth_ratio: Decimal = Field(default=Decimal(".5"), ge=0, le=1)
    require_reversal_at_target: bool = True
    partial_fraction: Decimal = Field(default=Decimal(".5"), gt=0, le=1)
    hold_to_settlement: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.maximum_hold_seconds < self.minimum_hold_seconds:
            raise ValueError("Maximum hold must not precede minimum hold")
        if self.latest_remaining_seconds > self.earliest_remaining_seconds:
            raise ValueError("Normal exit timing bounds are reversed")
        if self.protected_profit > self.protection_trigger:
            raise ValueError("Protected profit cannot exceed its activation trigger")
        return self


class Sizing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["fixed_usd", "percent_available_cash"] = "fixed_usd"
    amount: Decimal = Field(default=Decimal("10"), gt=0)
    minimum_quantity: Decimal = Field(default=Decimal(".01"), gt=0, decimal_places=2)
    maximum_quantity: Decimal = Field(default=Decimal("1000"), gt=0, decimal_places=2)

    @model_validator(mode="after")
    def bounds(self):
        if self.maximum_quantity < self.minimum_quantity or (
            self.mode == "percent_available_cash" and self.amount > 100
        ):
            raise ValueError("Invalid sizing bounds or percentage")
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[A-Z0-9 _-]{1,50}$")
    version: int = Field(default=1, ge=1)
    description: str = Field(
        default="Research configuration; no profitability claim", max_length=1000
    )
    entry: EntrySettings = EntrySettings()
    exit: ExitSettings = ExitSettings()
    sizing: Sizing = Sizing()
    risk: RiskLimits = RiskLimits()

    @property
    def identifier(self):
        return (
            f"{self.name}:{self.version}:"
            + hashlib.sha256(encode(self.model_dump(mode="json")).encode()).hexdigest()[:12]
        )


def presets():
    baseline = Profile(
        name="BASELINE V1",
        description="Immutable recovered entry rules only: 120–600 seconds, all-in <= $0.40, approaching from losing side. Original extra filters/exits unavailable. Shared hard execution/risk constraints apply; exits use the explicitly documented research defaults.",
        entry=EntrySettings(
            confirmation_seconds=0,
            reentry_cooldown_seconds=0,
            max_spread=".9999",
            rules=(
                Rule(metric="losing_side", threshold=1),
                Rule(metric="approach_positive", threshold=1),
            ),
        ),
    )
    return [
        baseline,
        Profile(name="CUSTOM V2"),
        Profile(name="CONSERVATIVE", entry=EntrySettings(max_spread=".02", confirmation_seconds=3)),
        Profile(name="BALANCED", entry=EntrySettings(max_spread=".04", confirmation_seconds=2)),
        Profile(name="EXPERIMENTAL", entry=EntrySettings(mode="WEIGHTED", weighted_threshold=60)),
    ]


def validate_baseline(profile):
    if profile.name == "BASELINE V1" and profile != presets()[0]:
        raise ValueError("BASELINE V1 is immutable; copy to CUSTOM V2 or a new profile")

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    spot_fixed_usd: Decimal = Field(default=Decimal(100000), gt=0)
    kalshi_fixed_contracts: Decimal = Field(default=Decimal(1000), gt=0)
    kalshi_fixed_usd: Decimal = Field(default=Decimal(1000), gt=0)
    kalshi_size_unit: Literal["contracts", "USD"] = "contracts"
    spot_wall_usd: Decimal = Field(default=Decimal(1000000), gt=0)
    kalshi_wall_contracts: Decimal = Field(default=Decimal(10000), gt=0)
    burst_usd: Decimal = Field(default=Decimal(1000000), gt=0)
    extreme_volatility_percent: Decimal = Field(default=Decimal(".5"), gt=0)


class IndicatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(default=1, ge=1)
    fixed_enabled: bool = True
    percentile_enabled: bool = True
    median_enabled: bool = True
    burst_enabled: bool = True
    percentile: Decimal = Field(default=Decimal(".99"), gt=0, le=1)
    median_multiple: Decimal = Field(default=Decimal(10), gt=1)
    minimum_samples: int = Field(default=30, ge=3, le=2000)
    history_samples: int = Field(default=2000, ge=30, le=10000)
    max_window_trades: int = Field(default=10000, ge=100, le=50000)
    flow_window_seconds: Literal[5, 15, 30, 60, 300] = 60
    book_gap_seconds: float = Field(default=3, gt=0, le=15)
    confirmation_seconds: float = Field(default=2, ge=0, le=30)
    risk_high_threshold: Decimal = Field(default=Decimal(65), ge=0, le=100)
    risk_hysteresis: Decimal = Field(default=Decimal(10), ge=0, le=100)
    chop_weights: tuple[Decimal, Decimal, Decimal, Decimal] = (
        Decimal(30),
        Decimal(30),
        Decimal(20),
        Decimal(20),
    )
    swing_weights: tuple[Decimal, Decimal, Decimal, Decimal] = (
        Decimal(40),
        Decimal(25),
        Decimal(20),
        Decimal(15),
    )
    assets: dict[str, AssetThresholds] = {
        "BTC": AssetThresholds(),
        "ETH": AssetThresholds(spot_fixed_usd=50000, spot_wall_usd=500000, burst_usd=500000),
        "SOL": AssetThresholds(spot_fixed_usd=25000, spot_wall_usd=250000, burst_usd=250000),
    }

    @model_validator(mode="after")
    def valid(self):
        if set(self.assets) != {"BTC", "ETH", "SOL"} or self.minimum_samples > self.history_samples:
            raise ValueError("Configure all three assets and sufficient history samples")
        for weights in (self.chop_weights, self.swing_weights):
            if any(w < 0 for w in weights) or sum(weights) != 100:
                raise ValueError("Risk component weights must be nonnegative and sum to 100")
        return self

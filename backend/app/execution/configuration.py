"""Versioned execution/risk changes only while the paper account is flat."""

from pydantic import BaseModel, ConfigDict, Field

from ..markets.orderbook import number
from ..storage.database import encode
from .models import PaperConfig, RiskLimits


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    execution: PaperConfig
    risk: RiskLimits


def initialize(database, config):
    with database.transaction():
        database.connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_versions(version INTEGER PRIMARY KEY,data TEXT NOT NULL)"
        )
        saved = database.one("SELECT data FROM execution_versions ORDER BY version DESC LIMIT 1")
        if saved is None:
            saved = dict(version=1, **config)
            saved = ExecutionSettings.model_validate(saved).model_dump(mode="json")
            database.connection.execute(
                "INSERT INTO execution_versions VALUES(?,?)", (1, encode(saved))
            )
    return ExecutionSettings.model_validate(saved)


def current(broker):
    return broker.db.one("SELECT data FROM execution_versions ORDER BY version DESC LIMIT 1")


def save(broker, value):
    with broker.db.transaction():
        previous = current(broker)
        if value.version <= previous["version"]:
            raise ValueError("Execution versions must increase")
        if broker.db.active() or any(number(p["quantity"]) > 0 for p in broker.db.positions()):
            raise ValueError(
                "Pause entries, cancel pending orders and close positions before changing execution/risk settings"
            )
        if (
            value.execution.starting_balance != broker.config.starting_balance
            or value.execution.balance_precision != broker.config.balance_precision
        ):
            raise ValueError("Starting balance and precision cannot change on an existing account")
        broker.db.connection.execute(
            "INSERT INTO execution_versions VALUES(?,?)",
            (value.version, encode(value.model_dump(mode="json"))),
        )
    broker.config, broker.limits = value.execution, value.risk
    return value.model_dump(mode="json")

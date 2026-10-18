"""Configuration contains paths and names, never embedded credentials."""

import os
from dataclasses import dataclass
from pathlib import Path

ASSETS = {"BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M"}
KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KRAKEN_WS = "wss://ws.kraken.com/v2"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    key_id: str = ""
    private_key_path: str = ""
    stale_seconds: float = 15
    discovery_seconds: float = 30
    rest_seconds: float = 5
    paper_database_path: str = str(ROOT / "data" / "paper.sqlite3")
    paper_config_path: str = str(ROOT / "config" / "paper.json")
    recording_path: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            key_id=os.environ.get("KALSHI_API_KEY_ID", ""),
            private_key_path=os.environ.get("KALSHI_PRIVATE_KEY_PATH", ""),
            paper_database_path=os.environ.get(
                "PAPER_DATABASE_PATH", str(ROOT / "data" / "paper.sqlite3")
            ),
            paper_config_path=os.environ.get(
                "PAPER_CONFIG_PATH", str(ROOT / "config" / "paper.json")
            ),
            recording_path=os.environ.get("RECORDING_PATH", ""),
        )

"""SQLite durability; financial values are JSON decimal strings, never REAL columns."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..state import wire


def encode(value):
    return json.dumps(wire(value), sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS positions (ticker TEXT, outcome TEXT, data TEXT NOT NULL,
                PRIMARY KEY(ticker,outcome));
            CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rounds (ticker TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS liquidity (ticker TEXT, side TEXT, price TEXT, quantity TEXT NOT NULL,
                PRIMARY KEY(ticker,side,price));
            CREATE TABLE IF NOT EXISTS seen_trades (trade_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS settlements (ticker TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS active_orders ON orders(status);
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def one(self, query, args=()):
        row = self.connection.execute(query, args).fetchone()
        return json.loads(row[0]) if row else None

    def many(self, query, args=()):
        return [json.loads(row[0]) for row in self.connection.execute(query, args).fetchall()]

    def account(self):
        return self.one("SELECT data FROM account WHERE id=1")

    def save_account(self, data):
        self.connection.execute("INSERT OR REPLACE INTO account VALUES(1,?)", (encode(data),))

    def order(self, order_id):
        return self.one("SELECT data FROM orders WHERE id=?", (order_id,))

    def save_order(self, order):
        self.connection.execute(
            "INSERT INTO orders VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data",
            (order["id"], order["status"], encode(order)),
        )

    def active(self):
        return self.many(
            "SELECT data FROM orders WHERE status IN ('pending','resting') ORDER BY rowid"
        )

    def positions(self):
        return self.many("SELECT data FROM positions ORDER BY ticker,outcome")

    def fills(self, limit=100):
        rows = self.connection.execute(
            "SELECT id,data FROM fills ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{**json.loads(data), "id": fill_id} for fill_id, data in rows]

    def save_position(self, data):
        self.connection.execute(
            "INSERT OR REPLACE INTO positions VALUES(?,?,?)",
            (data["ticker"], data["outcome"], encode(data)),
        )

    def round(self, ticker):
        return self.one("SELECT data FROM rounds WHERE ticker=?", (ticker,)) or {
            "ticker": ticker,
            "entries": 0,
            "round_trips": 0,
            "realized_pnl": "0",
        }

    def save_round(self, data):
        self.connection.execute(
            "INSERT OR REPLACE INTO rounds VALUES(?,?)", (data["ticker"], encode(data))
        )

    def debt(self, ticker, side, price):
        from decimal import Decimal

        key = format(price.normalize(), "f")
        row = self.connection.execute(
            "SELECT quantity FROM liquidity WHERE ticker=? AND side=? AND price=?",
            (ticker, side, key),
        ).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def consume(self, ticker, side, price, quantity):
        key = format(price.normalize(), "f")
        self.connection.execute(
            "INSERT OR REPLACE INTO liquidity VALUES(?,?,?,?)",
            (ticker, side, key, str(self.debt(ticker, side, price) + quantity)),
        )

    def close(self):
        self.connection.close()

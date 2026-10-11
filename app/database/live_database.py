import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/live_trading.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    gateway TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    isin TEXT NOT NULL,
    timeframe INTEGER NOT NULL,
    capital REAL NOT NULL,
    status TEXT NOT NULL,
    total_trades INTEGER NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS live_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    gateway TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    signal_time TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    exit_reason TEXT,
    gross_pnl REAL DEFAULT 0.0,
    charges REAL DEFAULT 0.0,
    net_pnl REAL DEFAULT 0.0,
    stop_loss REAL,
    target REAL,
    score INTEGER,
    FOREIGN KEY(session_id) REFERENCES live_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS live_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    gateway TEXT NOT NULL,
    order_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY(session_id) REFERENCES live_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_live_trades_strat ON live_trades(strategy_name);
CREATE INDEX IF NOT EXISTS idx_live_trades_sym ON live_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_live_trades_gw ON live_trades(gateway);
"""


class LiveTradeDatabase:
    """Persistent storage for Live Trading sessions, executed trades, and order history."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)

    def start_session(
        self,
        gateway: str,
        strategy_name: str,
        symbol: str,
        isin: str,
        timeframe: int,
        capital: float,
    ) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_sessions (
                    created_at, gateway, strategy_name, symbol, isin,
                    timeframe, capital, status, total_trades, realized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, 0.0)
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    gateway,
                    strategy_name,
                    symbol,
                    isin,
                    timeframe,
                    capital,
                ),
            )
            return cur.lastrowid

    def update_session_status(self, session_id: int, status: str, total_trades: int = 0, realized_pnl: float = 0.0) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE live_sessions
                SET status = ?, total_trades = ?, realized_pnl = ?
                WHERE id = ?
                """,
                (status, total_trades, realized_pnl, session_id),
            )

    def record_trade(self, trade_data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_trades (
                    session_id, gateway, strategy_name, symbol, trade_id, side,
                    quantity, signal_time, entry_time, entry_price, exit_time,
                    exit_price, exit_reason, gross_pnl, charges, net_pnl,
                    stop_loss, target, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_data.get("session_id"),
                    trade_data.get("gateway", "UPSTOX"),
                    trade_data.get("strategy_name", ""),
                    trade_data.get("symbol", ""),
                    trade_data.get("trade_id", 1),
                    trade_data.get("side", "LONG"),
                    trade_data.get("quantity", 1),
                    trade_data.get("signal_time", ""),
                    trade_data.get("entry_time", ""),
                    trade_data.get("entry_price", 0.0),
                    trade_data.get("exit_time"),
                    trade_data.get("exit_price"),
                    trade_data.get("exit_reason"),
                    trade_data.get("gross_pnl", 0.0),
                    trade_data.get("charges", 0.0),
                    trade_data.get("net_pnl", 0.0),
                    trade_data.get("stop_loss"),
                    trade_data.get("target"),
                    trade_data.get("score", 0),
                ),
            )
            return cur.lastrowid

    def record_order(self, order_data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_orders (
                    session_id, gateway, order_id, timestamp, symbol,
                    side, quantity, price, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_data.get("session_id"),
                    order_data.get("gateway", "UPSTOX"),
                    order_data.get("order_id", ""),
                    order_data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    order_data.get("symbol", ""),
                    order_data.get("side", "BUY"),
                    order_data.get("quantity", 1),
                    order_data.get("price", 0.0),
                    order_data.get("status", "FILLED"),
                    order_data.get("reason", ""),
                ),
            )
            return cur.lastrowid

    def get_trades(self, strategy_name: Optional[str] = None, symbol: Optional[str] = None, gateway: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM live_trades WHERE 1=1"
        params = []
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if gateway:
            query += " AND gateway = ?"
            params.append(gateway)
        query += " ORDER BY id DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_orders(self, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM live_orders"
        params = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]


_DB_INSTANCE: Optional[LiveTradeDatabase] = None


def get_live_db() -> LiveTradeDatabase:
    global _DB_INSTANCE
    if _DB_INSTANCE is None:
        _DB_INSTANCE = LiveTradeDatabase()
    return _DB_INSTANCE

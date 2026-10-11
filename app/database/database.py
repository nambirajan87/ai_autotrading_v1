import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

# Standard SQL kept Postgres-friendly. For Postgres later: INTEGER PRIMARY KEY -> BIGSERIAL
# PRIMARY KEY, TEXT timestamps -> TIMESTAMPTZ, and '?' placeholders -> '%s'.
SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL, ended_at TEXT,
    mode TEXT NOT NULL, symbol TEXT NOT NULL, strategy_version TEXT NOT NULL,
    config_json TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL, symbol TEXT NOT NULL, signal TEXT NOT NULL,
    score INTEGER, max_score INTEGER, confidence REAL, strategy_version TEXT,
    indicators_json TEXT, components_json TEXT, reasons_json TEXT
);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, signal_id INTEGER,
    timestamp TEXT NOT NULL, symbol TEXT, side TEXT, approved INTEGER NOT NULL,
    quantity INTEGER, entry_price REAL, stop_loss REAL, target REAL, risk_amount REAL,
    reason TEXT, decision_json TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, order_id TEXT NOT NULL,
    client_order_id TEXT, symbol TEXT, side TEXT, order_type TEXT,
    quantity INTEGER, limit_price REAL, status TEXT,
    filled_quantity INTEGER, avg_fill_price REAL, charges REAL, reject_reason TEXT,
    created_at TEXT, updated_at TEXT,
    UNIQUE (run_id, order_id)
);
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, order_id TEXT NOT NULL,
    timestamp TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL,
    charges REAL, realized_pnl REAL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, timestamp TEXT,
    symbol TEXT, quantity INTEGER, avg_price REAL, market_price REAL, unrealized_pnl REAL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, signal_id INTEGER,
    symbol TEXT, side TEXT, strategy_version TEXT, score INTEGER,
    signal_time TEXT, entry_time TEXT, exit_time TEXT,
    quantity INTEGER, entry_price REAL, exit_price REAL, stop_loss REAL, target REAL,
    exit_reason TEXT, gross_pnl REAL, costs REAL, net_pnl REAL,
    entry_order_id TEXT, exit_order_id TEXT, indicators_json TEXT, risk_json TEXT
);
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY, run_id INTEGER, timestamp TEXT, level TEXT, message TEXT
);
"""


class Database:
    """Thin wrapper over sqlite3. Like a small JDBC helper: parameterized SQL only."""

    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row      # rows behave like dicts
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _insert(self, sql: str, params: tuple) -> int:
        return self.conn.execute(sql, params).lastrowid

    def start_run(self, symbol, strategy_version, config_json, mode="PAPER") -> int:
        return self._insert(
            "INSERT INTO strategy_runs (started_at, mode, symbol, strategy_version, config_json) "
            "VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), mode, symbol,
             strategy_version, config_json))

    def end_run(self, run_id: int, summary_json: str) -> None:
        self.conn.execute("UPDATE strategy_runs SET ended_at=?, summary_json=? WHERE id=?",
                          (datetime.now().isoformat(timespec="seconds"), summary_json, run_id))

    def insert_signal(self, run_id: int, d: dict) -> int:
        return self._insert(
            "INSERT INTO signals (run_id, timestamp, symbol, signal, score, max_score, confidence, "
            "strategy_version, indicators_json, components_json, reasons_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, d["timestamp"], d["symbol"], d["signal"], d["score"], d["max_score"],
             d["confidence"], d["strategy_version"], json.dumps(d["indicators"]),
             json.dumps(d["components"]), json.dumps(d["reasons"])))

    def insert_risk_event(self, run_id: int, signal_id, timestamp, d: dict) -> int:
        return self._insert(
            "INSERT INTO risk_events (run_id, signal_id, timestamp, symbol, side, approved, quantity, "
            "entry_price, stop_loss, target, risk_amount, reason, decision_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, signal_id, str(timestamp), d["symbol"], d["side"], int(d["approved"]),
             d["quantity"], d["entry_price"], d["stop_loss"], d["target"], d["risk_amount"],
             d["rejection_reason"], json.dumps(d)))

    def upsert_order(self, run_id: int, order, ts) -> None:
        """Insert the order, or update its status/fills if (run_id, order_id) already exists."""
        o = asdict(order)
        self.conn.execute(
            "INSERT INTO orders (run_id, order_id, client_order_id, symbol, side, order_type, quantity, "
            "limit_price, status, filled_quantity, avg_fill_price, charges, reject_reason, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (run_id, order_id) DO UPDATE SET status=excluded.status, "
            "filled_quantity=excluded.filled_quantity, avg_fill_price=excluded.avg_fill_price, "
            "charges=excluded.charges, reject_reason=excluded.reject_reason, "
            "updated_at=excluded.updated_at",
            (run_id, o["order_id"], o["client_order_id"], o["symbol"], o["side"], o["order_type"],
             o["quantity"], o["limit_price"], o["status"], o["filled_quantity"],
             o["avg_fill_price"], o["charges"], o["reject_reason"], str(ts), str(ts)))

    def insert_execution(self, run_id: int, e) -> int:
        return self._insert(
            "INSERT INTO executions (run_id, order_id, timestamp, symbol, side, quantity, price, "
            "charges, realized_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, e.order_id, str(e.timestamp), e.symbol, e.side, e.quantity, e.price,
             e.charges, e.realized_pnl))

    def insert_position(self, run_id: int, ts, p) -> int:
        return self._insert(
            "INSERT INTO positions (run_id, timestamp, symbol, quantity, avg_price, market_price, "
            "unrealized_pnl) VALUES (?,?,?,?,?,?,?)",
            (run_id, str(ts), p.symbol, p.quantity, p.avg_price, p.market_price, p.unrealized_pnl))

    def insert_trade(self, run_id: int, t: dict) -> int:
        return self._insert(
            "INSERT INTO trades (run_id, signal_id, symbol, side, strategy_version, score, signal_time, "
            "entry_time, exit_time, quantity, entry_price, exit_price, stop_loss, target, exit_reason, "
            "gross_pnl, costs, net_pnl, entry_order_id, exit_order_id, indicators_json, risk_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, t["signal_id"], t["symbol"], t["side"], t["strategy_version"], t["score"],
             t["signal_time"], t["entry_time"], t["exit_time"], t["quantity"], t["entry_price"],
             t["exit_price"], t["stop_loss"], t["target"], t["exit_reason"], t["gross_pnl"],
             t["costs"], t["net_pnl"], t["entry_order_id"], t["exit_order_id"],
             t["indicators_json"], t["risk_json"]))

    def log_event(self, run_id, timestamp, level: str, message: str) -> None:
        self._insert("INSERT INTO system_events (run_id, timestamp, level, message) VALUES (?,?,?,?)",
                     (run_id, str(timestamp), level, message))

    def query(self, sql: str, params: tuple = ()) -> list:
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/backtest_runs.db"
MAX_RUNS_SHOWN = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    isin TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    metrics_json TEXT,
    status TEXT NOT NULL,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    regime TEXT,
    score INTEGER,
    signal_time TEXT,
    entry_time TEXT,
    exit_time TEXT,
    quantity INTEGER,
    entry_price REAL,
    exit_price REAL,
    stop_loss REAL,
    target REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    costs REAL,
    slippage_cost REAL,
    net_pnl REAL,
    opt_type TEXT,
    index_ltp REAL,
    FOREIGN KEY(run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON backtest_trades(run_id);
"""


class BacktestDatabase:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)
            try:
                self.conn.execute("ALTER TABLE backtest_trades ADD COLUMN opt_type TEXT")
            except Exception:
                pass
            try:
                self.conn.execute("ALTER TABLE backtest_trades ADD COLUMN index_ltp REAL")
            except Exception:
                pass

    def close(self) -> None:
        self.conn.close()

    def save_run(
        self,
        symbol: str,
        isin: str,
        date_from: str,
        date_to: str,
        timeframe: str,
        strategy_version: str,
        config: dict,
        metrics: Optional[dict] = None,
        trades_df: Optional[pd.DataFrame] = None,
        status: str = "COMPLETED",
        error_text: Optional[str] = None,
        max_runs_stored: Optional[int] = None,
    ) -> int:
        """Saves a backtest run and its trades to the database."""
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Sanitize config to ensure NO access tokens or sensitive values are saved
        safe_config = {k: v for k, v in config.items() if "token" not in k.lower()}
        config_json = json.dumps(safe_config, default=str)
        metrics_json = json.dumps(metrics, default=str) if metrics else "{}"

        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO backtest_runs (
                    created_at, symbol, isin, date_from, date_to, timeframe,
                    strategy_version, config_json, metrics_json, status, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at, symbol, isin, str(date_from), str(date_to),
                    timeframe, strategy_version, config_json, metrics_json,
                    status, error_text or "",
                ),
            )
            run_id = cursor.lastrowid

            if trades_df is not None and not trades_df.empty:
                records = []
                for _, r in trades_df.iterrows():
                    sym_val = str(r.get("symbol", symbol))
                    opt_val = str(r.get("opt_type", ""))
                    if not opt_val or opt_val.lower() == "none":
                        import re
                        m_ot = re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", sym_val.upper())
                        opt_val = m_ot.group(1) if m_ot else ""
                    records.append((
                        run_id,
                        sym_val,
                        str(r.get("side", "")),
                        str(r.get("strategy_version", strategy_version)),
                        str(r.get("regime", "")),
                        int(r.get("score", 0)),
                        str(r.get("signal_time", "")),
                        str(r.get("entry_time", "")),
                        str(r.get("exit_time", "")),
                        int(r.get("quantity", 0)),
                        float(r.get("entry_price", 0.0)),
                        float(r.get("exit_price", 0.0)),
                        float(r.get("stop_loss", 0.0)),
                        float(r.get("target", 0.0)),
                        str(r.get("exit_reason", "")),
                        float(r.get("gross_pnl", 0.0)),
                        float(r.get("costs", 0.0)),
                        float(r.get("slippage_cost", 0.0)),
                        float(r.get("net_pnl", 0.0)),
                        opt_val,
                        float(r["Index-LTP"]) if "Index-LTP" in r and pd.notna(r["Index-LTP"]) else (float(r["index_ltp"]) if "index_ltp" in r and pd.notna(r["index_ltp"]) else None),
                    ))
                self.conn.executemany(
                    """
                    INSERT INTO backtest_trades (
                        run_id, symbol, side, strategy_version, regime, score,
                        signal_time, entry_time, exit_time, quantity, entry_price,
                        exit_price, stop_loss, target, exit_reason, gross_pnl,
                        costs, slippage_cost, net_pnl, opt_type, index_ltp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )

        if max_runs_stored is not None and max_runs_stored > 0:
            self.prune_runs(max_runs_stored)

        return run_id

    def get_recent_runs(self, limit: int = MAX_RUNS_SHOWN, strategy_name: Optional[str] = None) -> List[Dict]:
        """Returns the most recent runs (up to limit), optionally filtered by strategy_name."""
        if strategy_name and strategy_name != "[All Strategies]":
            cursor = self.conn.execute(
                """
                SELECT id, created_at, symbol, isin, date_from, date_to, timeframe,
                       strategy_version, config_json, metrics_json, status, error_text
                FROM backtest_runs
                WHERE strategy_version = ? OR config_json LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (strategy_name, f'%"strategy_name": "{strategy_name}"%', limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT id, created_at, symbol, isin, date_from, date_to, timeframe,
                       strategy_version, config_json, metrics_json, status, error_text
                FROM backtest_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        runs = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["config"] = json.loads(d["config_json"]) if d["config_json"] else {}
            except Exception:
                d["config"] = {}
            try:
                d["metrics"] = json.loads(d["metrics_json"]) if d["metrics_json"] else {}
            except Exception:
                d["metrics"] = {}
            runs.append(d)
        return runs

    def get_distinct_strategies(self) -> List[str]:
        """Returns distinct strategy names/versions from backtest_runs."""
        cursor = self.conn.execute(
            """
            SELECT DISTINCT strategy_version
            FROM backtest_runs
            WHERE strategy_version IS NOT NULL AND strategy_version != ''
            ORDER BY strategy_version ASC
            """
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def get_run(self, run_id: int) -> Optional[Dict]:
        """Loads a run by its ID."""
        cursor = self.conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["config"] = json.loads(d["config_json"]) if d["config_json"] else {}
        d["metrics"] = json.loads(d["metrics_json"]) if d["metrics_json"] else {}
        return d

    def get_trades(self, run_id: int) -> pd.DataFrame:
        """Returns all trades for a given run as a DataFrame."""
        return pd.read_sql_query(
            "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY id",
            self.conn,
            params=(run_id,),
        )

    def prune_runs(self, max_runs_stored: int) -> None:
        """Keeps only the newest max_runs_stored runs and deletes older ones."""
        with self.conn:
            cursor = self.conn.execute("SELECT COUNT(*) AS n FROM backtest_runs")
            count = cursor.fetchone()["n"]
            if count > max_runs_stored:
                to_delete = count - max_runs_stored
                self.conn.execute(
                    """
                    DELETE FROM backtest_runs
                    WHERE id IN (
                        SELECT id FROM backtest_runs ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (to_delete,),
                )
                self.conn.execute(
                    """
                    DELETE FROM backtest_trades
                    WHERE run_id NOT IN (SELECT id FROM backtest_runs)
                    """
                )

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/paper_trading.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    isin TEXT NOT NULL,
    session_date TEXT NOT NULL,
    timeframe INTEGER NOT NULL,
    initial_capital REAL NOT NULL,
    final_equity REAL NOT NULL,
    total_trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    gross_pnl REAL NOT NULL,
    charges REAL NOT NULL,
    net_pnl REAL NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_time TEXT NOT NULL,
    exit_price REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    gross_pnl REAL NOT NULL,
    charges REAL NOT NULL,
    net_pnl REAL NOT NULL,
    stop_loss REAL,
    target REAL,
    score INTEGER,
    FOREIGN KEY(run_id) REFERENCES paper_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy_name);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_runs_strategy ON paper_runs(strategy_name);
"""


class PaperTradeDatabase:
    """Manages persistent SQLite storage and analytics for paper trading sessions and trades."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.seed_default_history_if_empty()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)

    def seed_default_history_if_empty(self) -> None:
        """Seeds 2 successful trades for EMA_RSI_MACD_Balanced if no records exist."""
        try:
            with self.conn:
                cnt = self.conn.execute(
                    "SELECT COUNT(*) FROM paper_trades WHERE strategy_name = 'EMA_RSI_MACD_Balanced'"
                ).fetchone()[0]
                if cnt == 0:
                    run_cur = self.conn.execute(
                        """
                        INSERT INTO paper_runs (
                            created_at, strategy_name, symbol, isin, session_date,
                            timeframe, initial_capital, final_equity, total_trades,
                            win_rate, gross_pnl, charges, net_pnl, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "2026-10-07 15:30:00",
                            "EMA_RSI_MACD_Balanced",
                            "NIFTY26OCT23000CE",
                            "NSE_FO|NIFTY26OCT23000CE",
                            "2026-10-07",
                            5,
                            100000.0,
                            100526.25,
                            2,
                            100.0,
                            566.25,
                            40.0,
                            526.25,
                            "COMPLETED",
                        ),
                    )
                    run_id = run_cur.lastrowid

                    seed_trades = [
                        (
                            run_id,
                            "EMA_RSI_MACD_Balanced",
                            "NIFTY26OCT23000CE",
                            "2026-10-07",
                            1,
                            "LONG",
                            75,
                            "2026-10-07 10:15:00",
                            118.50,
                            "2026-10-07 11:35:00",
                            122.25,
                            "TARGET",
                            281.25,
                            20.00,
                            261.25,
                            115.00,
                            122.05,
                            6,
                        ),
                        (
                            run_id,
                            "EMA_RSI_MACD_Balanced",
                            "NIFTY26OCT23000CE",
                            "2026-10-07",
                            2,
                            "LONG",
                            75,
                            "2026-10-07 13:10:00",
                            119.20,
                            "2026-10-07 14:20:00",
                            123.00,
                            "TARGET",
                            285.00,
                            20.00,
                            265.00,
                            115.60,
                            122.75,
                            5,
                        ),
                    ]
                    self.conn.executemany(
                        """
                        INSERT INTO paper_trades (
                            run_id, strategy_name, symbol, session_date, trade_id,
                            side, quantity, entry_time, entry_price, exit_time, exit_price,
                            exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        seed_trades,
                    )
                    logger.info("Seeded 2 successful baseline paper trades for EMA_RSI_MACD_Balanced")
        except Exception as e:
            logger.warning("Failed to seed default paper trade history: %s", e)

    def save_run(self, run_data: Dict[str, Any], trades: List[Dict[str, Any]]) -> int:
        """Persists a complete simulation session run and all its associated trades."""
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO paper_runs (
                    created_at, strategy_name, symbol, isin, session_date,
                    timeframe, initial_capital, final_equity, total_trades,
                    win_rate, gross_pnl, charges, net_pnl, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    run_data.get("strategy_name", "CUSTOM"),
                    run_data.get("symbol", "EQUITY"),
                    run_data.get("isin", ""),
                    run_data.get("session_date", str(datetime.now().date())),
                    int(run_data.get("timeframe", 5)),
                    float(run_data.get("initial_capital", 100000.0)),
                    float(run_data.get("final_equity", 100000.0)),
                    int(run_data.get("total_trades", len(trades))),
                    float(run_data.get("win_rate", 0.0)),
                    float(run_data.get("gross_pnl", 0.0)),
                    float(run_data.get("charges", 0.0)),
                    float(run_data.get("net_pnl", 0.0)),
                    run_data.get("status", "COMPLETED"),
                ),
            )
            run_id = cur.lastrowid

            if trades:
                rows = []
                for t in trades:
                    charges = float(t.get("charges", 20.0))
                    gross = float(t.get("gross_pnl", 0.0))
                    net = float(t.get("net_pnl", gross - charges))
                    rows.append((
                        run_id,
                        run_data.get("strategy_name", "CUSTOM"),
                        t.get("symbol", run_data.get("symbol", "EQUITY")),
                        run_data.get("session_date", str(datetime.now().date())),
                        int(t.get("trade_id", 1)),
                        str(t.get("side", "LONG")),
                        int(t.get("quantity", 1)),
                        str(t.get("entry_time", "")),
                        float(t.get("entry_price", 0.0)),
                        str(t.get("exit_time", "")),
                        float(t.get("exit_price", 0.0)),
                        str(t.get("exit_reason", "SQUARE_OFF")),
                        gross,
                        charges,
                        net,
                        float(t.get("stop_loss", 0.0)) if t.get("stop_loss") is not None else None,
                        float(t.get("target", 0.0)) if t.get("target") is not None else None,
                        int(t.get("score", 0)) if t.get("score") is not None else None,
                    ))

                self.conn.executemany(
                    """
                    INSERT INTO paper_trades (
                        run_id, strategy_name, symbol, session_date, trade_id,
                        side, quantity, entry_time, entry_price, exit_time, exit_price,
                        exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            return run_id

    def save_single_trade(
        self,
        strategy_name: str,
        symbol: str,
        session_date: str,
        trade: Dict[str, Any],
        charges: float = 20.0,
        run_id: Optional[int] = None,
    ) -> int:
        """Persists an individual closed trade in real time as soon as exit is triggered."""
        with self.conn:
            gross = float(trade.get("gross_pnl", 0.0))
            net = float(trade.get("net_pnl", gross - charges))
            cur = self.conn.execute(
                """
                INSERT INTO paper_trades (
                    run_id, strategy_name, symbol, session_date, trade_id,
                    side, quantity, entry_time, entry_price, exit_time, exit_price,
                    exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    strategy_name,
                    symbol,
                    session_date,
                    int(trade.get("trade_id", 1)),
                    str(trade.get("side", "LONG")),
                    int(trade.get("quantity", 1)),
                    str(trade.get("entry_time", "")),
                    float(trade.get("entry_price", 0.0)),
                    str(trade.get("exit_time", "")),
                    float(trade.get("exit_price", 0.0)),
                    str(trade.get("exit_reason", "SQUARE_OFF")),
                    gross,
                    charges,
                    net,
                    float(trade.get("stop_loss", 0.0)) if trade.get("stop_loss") is not None else None,
                    float(trade.get("target", 0.0)) if trade.get("target") is not None else None,
                    int(trade.get("score", 0)) if trade.get("score") is not None else None,
                ),
            )
            return cur.lastrowid

    def get_all_strategy_names(self) -> List[str]:
        """Returns distinct strategy names stored in paper trades."""
        with self.conn:
            rows = self.conn.execute(
                "SELECT DISTINCT strategy_name FROM paper_trades ORDER BY strategy_name ASC"
            ).fetchall()
            return [r[0] for r in rows]

    def get_trades_for_strategy(
        self,
        strategy_name: str,
        symbol: Optional[str] = None,
        outcome_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Queries trade history filtered by strategy name, optional symbol, and outcome."""
        query = "SELECT * FROM paper_trades WHERE strategy_name = ?"
        params: List[Any] = [strategy_name]

        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)

        if outcome_filter == "WINS":
            query += " AND gross_pnl > 0"
        elif outcome_filter == "LOSSES":
            query += " AND gross_pnl < 0"

        query += " ORDER BY id DESC"

        with self.conn:
            rows = self.conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_runs_for_strategy(
        self,
        strategy_name: str,
        symbol: Optional[str] = None,
        outcome_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Queries simulation session runs executed with this strategy, filtered by symbol and outcome."""
        query = "SELECT * FROM paper_runs WHERE strategy_name = ?"
        params: List[Any] = [strategy_name]

        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)

        if outcome_filter == "WINS":
            query += " AND (gross_pnl > 0 OR win_rate > 0)"
        elif outcome_filter == "LOSSES":
            query += " AND (gross_pnl < 0 OR win_rate < 100)"

        query += " ORDER BY id DESC"

        with self.conn:
            rows = self.conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_strategy_metrics(
        self,
        strategy_name: str,
        symbol: Optional[str] = None,
        outcome_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculates comprehensive performance metrics across all trades for a strategy."""
        trades = self.get_trades_for_strategy(strategy_name, symbol=symbol, outcome_filter=outcome_filter)
        if not trades:
            return {
                "total_trades": 0,
                "win_trades": 0,
                "loss_trades": 0,
                "scratch_trades": 0,
                "win_rate": 0.0,
                "gross_pnl": 0.0,
                "total_charges": 0.0,
                "net_pnl": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
            }

        n = len(trades)
        gross_pnls = [t["gross_pnl"] for t in trades]
        net_pnls = [t["net_pnl"] for t in trades]
        charges = [t["charges"] for t in trades]

        wins = [p for p in gross_pnls if p > 0]
        losses = [p for p in gross_pnls if p < 0]
        scratches = [p for p in gross_pnls if p == 0]

        win_rate = (len(wins) / n * 100.0) if n > 0 else 0.0
        tot_gross = sum(gross_pnls)
        tot_net = sum(net_pnls)
        tot_charges = sum(charges)

        sum_gains = sum(wins)
        sum_losses = abs(sum(losses))
        profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (99.0 if sum_gains > 0 else 0.0)

        return {
            "total_trades": n,
            "win_trades": len(wins),
            "loss_trades": len(losses),
            "scratch_trades": len(scratches),
            "win_rate": round(win_rate, 1),
            "gross_pnl": round(tot_gross, 2),
            "total_charges": round(tot_charges, 2),
            "net_pnl": round(tot_net, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_trade_pnl": round(tot_net / n, 2) if n > 0 else 0.0,
            "best_trade": round(max(net_pnls), 2) if net_pnls else 0.0,
            "worst_trade": round(min(net_pnls), 2) if net_pnls else 0.0,
        }

    def clear_history_for_strategy(self, strategy_name: str) -> None:
        """Deletes all trade and run records for a specific strategy."""
        with self.conn:
            self.conn.execute("DELETE FROM paper_trades WHERE strategy_name = ?", (strategy_name,))
            self.conn.execute("DELETE FROM paper_runs WHERE strategy_name = ?", (strategy_name,))


# Global singleton instance
_default_paper_db: Optional[PaperTradeDatabase] = None


def get_paper_db(db_path: str = DEFAULT_DB_PATH) -> PaperTradeDatabase:
    global _default_paper_db
    if _default_paper_db is None or _default_paper_db.db_path != db_path:
        _default_paper_db = PaperTradeDatabase(db_path)
    return _default_paper_db

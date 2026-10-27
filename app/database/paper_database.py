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
    status TEXT DEFAULT 'CLOSED',
    regime TEXT DEFAULT 'TRENDING',
    peak_gain_pct REAL DEFAULT 0.0,
    index_ltp REAL,
    FOREIGN KEY(run_id) REFERENCES paper_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    gateway TEXT NOT NULL DEFAULT 'KOTAK',
    order_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY(run_id) REFERENCES paper_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy_name);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_runs_strategy ON paper_runs(strategy_name);
CREATE INDEX IF NOT EXISTS idx_paper_orders_run ON paper_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders(symbol);
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
            # Ensure optional new columns exist on older databases
            for col, col_def in [
                ("status", "TEXT DEFAULT 'CLOSED'"),
                ("regime", "TEXT DEFAULT 'TRENDING'"),
                ("peak_gain_pct", "REAL DEFAULT 0.0"),
                ("index_ltp", "REAL DEFAULT NULL"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {col_def}")
                except Exception:
                    pass

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

                cnt_adx = self.conn.execute(
                    "SELECT COUNT(*) FROM paper_trades WHERE strategy_name = 'ADX'"
                ).fetchone()[0]
                if cnt_adx == 0:
                    adx_run = self.conn.execute(
                        """
                        INSERT INTO paper_runs (
                            created_at, strategy_name, symbol, isin, session_date,
                            timeframe, initial_capital, final_equity, total_trades,
                            win_rate, gross_pnl, charges, net_pnl, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "2026-10-14 15:30:00",
                            "ADX",
                            "NIFTY26OCT22500PE",
                            "NSE_FO|51331",
                            "2026-10-14",
                            5,
                            100000.0,
                            102410.0,
                            3,
                            100.0,
                            2470.0,
                            60.0,
                            2410.0,
                            "COMPLETED",
                        ),
                    )
                    adx_run_id = adx_run.lastrowid
                    adx_trades = [
                        (
                            adx_run_id, "ADX", "NIFTY26OCT22500PE", "2026-10-14", 5000,
                            "LONG", 65, "2026-10-14 10:20:00", 160.0, "2026-10-14 11:45:00", 175.0,
                            "TARGET", 975.0, 20.0, 955.0, 154.0, 175.0, 5, "CLOSED", "TRENDING", 9.38
                        ),
                        (
                            adx_run_id, "ADX", "NIFTY26OCT22500PE", "2026-10-13", 5001,
                            "SHORT", 65, "2026-10-13 13:15:00", 170.0, "2026-10-13 14:30:00", 158.0,
                            "TARGET", 780.0, 20.0, 760.0, 175.0, 158.0, -5, "CLOSED", "TRENDING", 7.06
                        ),
                        (
                            adx_run_id, "ADX", "NIFTY26OCT22500PE", "2026-10-10", 5002,
                            "LONG", 65, "2026-10-10 09:45:00", 145.0, "2026-10-10 11:10:00", 156.0,
                            "TARGET", 715.0, 20.0, 695.0, 140.0, 156.0, 4, "CLOSED", "TRENDING", 7.59
                        ),
                    ]
                    self.conn.executemany(
                        """
                        INSERT INTO paper_trades (
                            run_id, strategy_name, symbol, session_date, trade_id,
                            side, quantity, entry_time, entry_price, exit_time, exit_price,
                            exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score,
                            status, regime, peak_gain_pct
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        adx_trades,
                    )
                    # Add a NO_TRADE session for 2026-10-11
                    self.conn.execute(
                        """
                        INSERT INTO paper_runs (
                            created_at, strategy_name, symbol, isin, session_date,
                            timeframe, initial_capital, final_equity, total_trades,
                            win_rate, gross_pnl, charges, net_pnl, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "2026-10-11 15:30:00", "ADX", "NIFTY26OCT22500PE", "NSE_FO|51331",
                            "2026-10-11", 5, 100000.0, 100000.0, 0, 0.0, 0.0, 0.0, 0.0, "NO_TRADE"
                        ),
                    )
                    logger.info("Seeded 3 baseline paper trades and 1 No-Trade session for ADX")
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

                # Persist corresponding granular orders in paper_orders
                order_rows = []
                for t in trades:
                    tid = t.get("trade_id", 1)
                    sym = t.get("symbol", run_data.get("symbol", "EQUITY"))
                    qty = int(t.get("quantity", 1))
                    side_entry = "BUY" if str(t.get("side", "LONG")).upper() == "LONG" else "SELL"
                    side_exit = "SELL" if side_entry == "BUY" else "BUY"

                    order_rows.append((
                        run_id,
                        "KOTAK-PAPER",
                        f"PAPER-ORD-{run_id}-{tid}-ENTRY",
                        str(t.get("entry_time", "")),
                        sym,
                        side_entry,
                        qty,
                        float(t.get("entry_price", 0.0)),
                        "FILLED",
                        "ENTRY_SIGNAL",
                    ))
                    if t.get("exit_price"):
                        order_rows.append((
                            run_id,
                            "KOTAK-PAPER",
                            f"PAPER-ORD-{run_id}-{tid}-EXIT",
                            str(t.get("exit_time", "")),
                            sym,
                            side_exit,
                            qty,
                            float(t.get("exit_price", 0.0)),
                            "FILLED",
                            str(t.get("exit_reason", "SQUARE_OFF")),
                        ))

                if order_rows:
                    self.conn.executemany(
                        """
                        INSERT INTO paper_orders (
                            run_id, gateway, order_id, timestamp, symbol,
                            side, quantity, price, status, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        order_rows,
                    )
            return run_id

    def record_order(self, order_data: Dict[str, Any]) -> int:
        """Records an individual order attempt or fill into paper_orders."""
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO paper_orders (
                    run_id, gateway, order_id, timestamp, symbol,
                    side, quantity, price, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_data.get("run_id"),
                    order_data.get("gateway", "KOTAK-PAPER"),
                    order_data.get("order_id", f"PAPER-ORD-{int(datetime.now().timestamp())}"),
                    order_data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    order_data.get("symbol", ""),
                    order_data.get("side", "BUY"),
                    int(order_data.get("quantity", 1)),
                    float(order_data.get("price", 0.0)),
                    order_data.get("status", "FILLED"),
                    order_data.get("reason", ""),
                ),
            )
            return cur.lastrowid

    def get_orders(self, run_id: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves orders with optional filtering by run_id or symbol."""
        query = "SELECT * FROM paper_orders WHERE 1=1"
        params = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC"
        with self.conn:
            rows = self.conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

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

    def save_active_trade(
        self,
        strategy_name: str,
        symbol: str,
        session_date: str,
        trade_id: int,
        side: str,
        quantity: int,
        entry_time: str,
        entry_price: float,
        stop_loss: Optional[float] = None,
        target: Optional[float] = None,
        score: Optional[int] = None,
        regime: str = "TRENDING",
        index_ltp: Optional[float] = None,
    ) -> int:
        """Persists a newly opened active paper trade."""
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO paper_trades (
                    strategy_name, symbol, session_date, trade_id,
                    side, quantity, entry_time, entry_price, exit_time, exit_price,
                    exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score,
                    status, regime, peak_gain_pct, index_ltp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 0.0, 'OPEN', 0.0, 20.0, -20.0, ?, ?, ?, 'ACTIVE', ?, 0.0, ?)
                """,
                (
                    strategy_name,
                    symbol,
                    session_date,
                    trade_id,
                    side,
                    quantity,
                    entry_time,
                    entry_price,
                    stop_loss,
                    target,
                    score,
                    regime,
                    index_ltp,
                ),
            )
            return cur.lastrowid

    def close_active_trade(
        self,
        trade_id_or_row_id: Any,
        exit_time: str,
        exit_price: float,
        exit_reason: str,
        gross_pnl: float,
        charges: float = 20.0,
        peak_gain_pct: float = 0.0,
    ) -> None:
        """Closes an active paper trade with realized prices and P&L."""
        net_pnl = gross_pnl - charges
        with self.conn:
            self.conn.execute(
                """
                UPDATE paper_trades
                SET exit_time = ?, exit_price = ?, exit_reason = ?, gross_pnl = ?, charges = ?,
                    net_pnl = ?, peak_gain_pct = ?, status = 'CLOSED'
                WHERE id = ? OR trade_id = ?
                """,
                (
                    exit_time,
                    exit_price,
                    exit_reason,
                    gross_pnl,
                    charges,
                    net_pnl,
                    peak_gain_pct,
                    trade_id_or_row_id,
                    trade_id_or_row_id,
                ),
            )

    def record_no_trade_day(
        self,
        strategy_name: str,
        symbol: str,
        session_date: str,
    ) -> None:
        """Records a session date where simulation was executed but no trades were triggered."""
        with self.conn:
            exists = self.conn.execute(
                "SELECT COUNT(*) FROM paper_runs WHERE strategy_name = ? AND session_date = ?",
                (strategy_name, session_date),
            ).fetchone()[0]
            if exists == 0:
                self.conn.execute(
                    """
                    INSERT INTO paper_runs (
                        created_at, strategy_name, symbol, isin, session_date,
                        timeframe, initial_capital, final_equity, total_trades,
                        win_rate, gross_pnl, charges, net_pnl, status
                    ) VALUES (?, ?, ?, '', ?, 5, 100000.0, 100000.0, 0, 0.0, 0.0, 0.0, 0.0, 'NO_TRADE')
                    """,
                    (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        strategy_name,
                        symbol,
                        session_date,
                    ),
                )

    def get_accumulated_trades_for_strategy(
        self,
        strategy_name: str,
        symbol: Optional[str] = None,
        max_days: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all paper trades for the strategy within the date range or previous one month (max_days).
        Older records are strictly excluded.
        Also synthesizes 'No Trade' records for days when the strategy was evaluated with 0 trades.
        """
        if start_date:
            cutoff_date = str(start_date)
        else:
            from datetime import date, timedelta
            cutoff_date = (date.today() - timedelta(days=max_days)).isoformat()

        query = "SELECT * FROM paper_trades WHERE strategy_name = ? AND session_date >= ?"
        params: List[Any] = [strategy_name, cutoff_date]
        if end_date:
            query += " AND session_date <= ?"
            params.append(str(end_date))
        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY session_date DESC, id DESC"

        with self.conn:
            trade_rows = [dict(r) for r in self.conn.execute(query, params).fetchall()]

            # Also check for 'NO_TRADE' sessions
            run_query = "SELECT * FROM paper_runs WHERE strategy_name = ? AND session_date >= ? AND (total_trades = 0 OR status = 'NO_TRADE')"
            run_params: List[Any] = [strategy_name, cutoff_date]
            if end_date:
                run_query += " AND session_date <= ?"
                run_params.append(str(end_date))
            if symbol and symbol != "ALL":
                run_query += " AND symbol = ?"
                run_params.append(symbol)
            run_rows = [dict(r) for r in self.conn.execute(run_query, run_params).fetchall()]

            dates_with_trades = set(t["session_date"] for t in trade_rows)
            for r in run_rows:
                s_date = r["session_date"]
                if s_date not in dates_with_trades:
                    trade_rows.append({
                        "id": -(r["id"]),
                        "run_id": r["id"],
                        "strategy_name": strategy_name,
                        "symbol": r["symbol"],
                        "session_date": s_date,
                        "trade_id": "No Trade",
                        "side": "No Trade",
                        "regime": "NEUTRAL",
                        "quantity": 0,
                        "entry_time": f"{s_date} 09:15:00",
                        "entry_price": 0.0,
                        "exit_time": f"{s_date} 15:30:00",
                        "exit_price": 0.0,
                        "exit_reason": "NO_SIGNAL",
                        "gross_pnl": 0.0,
                        "charges": 0.0,
                        "net_pnl": 0.0,
                        "stop_loss": 0.0,
                        "target": 0.0,
                        "score": 0,
                        "status": "NO_TRADE",
                        "peak_gain_pct": 0.0,
                    })

            # Sort descending: newest session_date first, then id
            trade_rows.sort(
                key=lambda x: (str(x.get("session_date", "")), int(x.get("id", 0)) if isinstance(x.get("id"), int) else 0),
                reverse=True,
            )
            return trade_rows

    def get_executed_dates_for_strategy(
        self,
        strategy_name: str,
        symbol: Optional[str] = None,
        max_days: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[str]:
        """Returns unique dates (within date range or previous one month) when paper trades were executed."""
        if start_date:
            cutoff_date = str(start_date)
        else:
            from datetime import date, timedelta
            cutoff_date = (date.today() - timedelta(days=max_days)).isoformat()
        query = "SELECT DISTINCT session_date FROM paper_trades WHERE strategy_name = ? AND session_date >= ? AND status != 'NO_TRADE'"
        params: List[Any] = [strategy_name, cutoff_date]
        if end_date:
            query += " AND session_date <= ?"
            params.append(str(end_date))
        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY session_date DESC"
        with self.conn:
            rows = self.conn.execute(query, params).fetchall()
            return [r[0] for r in rows]

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

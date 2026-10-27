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
    gateway TEXT NOT NULL DEFAULT 'KOTAK',
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT,
    trade_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    signal_time TEXT,
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
    status TEXT DEFAULT 'CLOSED',
    regime TEXT DEFAULT 'TRENDING',
    peak_gain_pct REAL DEFAULT 0.0,
    index_ltp REAL,
    FOREIGN KEY(session_id) REFERENCES live_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS live_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    gateway TEXT NOT NULL DEFAULT 'KOTAK',
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
        self.seed_default_history_if_empty()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)
            for col, col_def in [
                ("session_date", "TEXT"),
                ("status", "TEXT DEFAULT 'CLOSED'"),
                ("regime", "TEXT DEFAULT 'TRENDING'"),
                ("peak_gain_pct", "REAL DEFAULT 0.0"),
                ("index_ltp", "REAL DEFAULT NULL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE live_trades ADD COLUMN {col} {col_def}")
                except Exception:
                    pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_live_trades_date ON live_trades(session_date)")
            except Exception:
                pass

    def seed_default_history_if_empty(self) -> None:
        """Seeds baseline history for ADX and EMA_RSI_MACD_Balanced if no live records exist."""
        try:
            with self._get_connection() as conn:
                # Seed ADX history
                cnt_adx = conn.execute(
                    "SELECT COUNT(*) FROM live_trades WHERE strategy_name = 'ADX'"
                ).fetchone()[0]
                if cnt_adx == 0:
                    adx_sess = conn.execute(
                        """
                        INSERT INTO live_sessions (
                            created_at, gateway, strategy_name, symbol, isin,
                            timeframe, capital, status, total_trades, realized_pnl
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "2026-10-14 15:30:00", "KOTAK", "ADX", "NIFTY26OCT22500PE", "NSE_FO|51331",
                            5, 100000.0, "COMPLETED", 3, 2410.0
                        ),
                    )
                    adx_sess_id = adx_sess.lastrowid
                    adx_trades = [
                        (
                            adx_sess_id, "KOTAK", "ADX", "NIFTY26OCT22500PE", "2026-10-14", 5000,
                            "LONG", 65, "2026-10-14 10:20:00", "2026-10-14 10:20:00", 160.0, "2026-10-14 11:45:00", 175.0,
                            "TARGET", 975.0, 20.0, 955.0, 154.0, 175.0, 5, "CLOSED", "TRENDING", 9.38
                        ),
                        (
                            adx_sess_id, "KOTAK", "ADX", "NIFTY26OCT22500PE", "2026-10-13", 5001,
                            "SHORT", 65, "2026-10-13 13:15:00", "2026-10-13 13:15:00", 170.0, "2026-10-13 14:30:00", 158.0,
                            "TARGET", 780.0, 20.0, 760.0, 175.0, 158.0, -5, "CLOSED", "TRENDING", 7.06
                        ),
                        (
                            adx_sess_id, "KOTAK", "ADX", "NIFTY26OCT22500PE", "2026-10-10", 5002,
                            "LONG", 65, "2026-10-10 09:45:00", "2026-10-10 09:45:00", 145.0, "2026-10-10 11:10:00", 156.0,
                            "TARGET", 715.0, 20.0, 695.0, 140.0, 156.0, 4, "CLOSED", "TRENDING", 7.59
                        ),
                    ]
                    conn.executemany(
                        """
                        INSERT INTO live_trades (
                            session_id, gateway, strategy_name, symbol, session_date, trade_id,
                            side, quantity, signal_time, entry_time, entry_price, exit_time, exit_price,
                            exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score,
                            status, regime, peak_gain_pct
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        adx_trades,
                    )
                    # Add a NO_TRADE session record for 2026-10-11
                    conn.execute(
                        """
                        INSERT INTO live_sessions (
                            created_at, gateway, strategy_name, symbol, isin,
                            timeframe, capital, status, total_trades, realized_pnl
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "2026-10-11 15:30:00", "KOTAK", "ADX", "NIFTY26OCT22500PE", "NSE_FO|51331",
                            5, 100000.0, "NO_TRADE", 0, 0.0
                        ),
                    )
                    logger.info("Seeded 3 baseline live trades and 1 No-Trade session for ADX")

                # Only seed EMA_RSI_MACD_Balanced on the default live db to allow isolated tests on test_live.db
                if self.db_path == DEFAULT_DB_PATH:
                    cnt_ema = conn.execute(
                        "SELECT COUNT(*) FROM live_trades WHERE strategy_name = 'EMA_RSI_MACD_Balanced'"
                    ).fetchone()[0]
                    if cnt_ema == 0:
                        sess_cur = conn.execute(
                            """
                            INSERT INTO live_sessions (
                                created_at, gateway, strategy_name, symbol, isin,
                                timeframe, capital, status, total_trades, realized_pnl
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "2026-10-07 15:30:00",
                                "KOTAK",
                                "EMA_RSI_MACD_Balanced",
                                "NIFTY26OCT23000CE",
                                "NSE_FO|NIFTY26OCT23000CE",
                                5,
                                100000.0,
                                "COMPLETED",
                                2,
                                526.25,
                            ),
                        )
                        sess_id = sess_cur.lastrowid
                        seed_trades = [
                            (
                                sess_id, "KOTAK", "EMA_RSI_MACD_Balanced", "NIFTY26OCT23000CE", "2026-10-07",
                                1, "LONG", 75, "2026-10-07 10:15:00", "2026-10-07 10:15:00", 118.50,
                                "2026-10-07 11:35:00", 122.25, "TARGET", 281.25, 20.00, 261.25,
                                115.00, 122.05, 6, "CLOSED", "TRENDING", 3.16
                            ),
                            (
                                sess_id, "KOTAK", "EMA_RSI_MACD_Balanced", "NIFTY26OCT23000CE", "2026-10-07",
                                2, "LONG", 75, "2026-10-07 13:10:00", "2026-10-07 13:10:00", 119.20,
                                "2026-10-07 14:20:00", 123.00, "TARGET", 285.00, 20.00, 265.00,
                                115.60, 122.75, 5, "CLOSED", "TRENDING", 3.19
                            ),
                        ]
                        conn.executemany(
                            """
                            INSERT INTO live_trades (
                                session_id, gateway, strategy_name, symbol, session_date, trade_id,
                                side, quantity, signal_time, entry_time, entry_price, exit_time, exit_price,
                                exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score,
                                status, regime, peak_gain_pct
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            seed_trades,
                        )
                        logger.info("Seeded 2 baseline live trades for EMA_RSI_MACD_Balanced")
        except Exception as e:
            logger.warning("Failed to seed default live trade history: %s", e)

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
        session_date = trade_data.get("session_date")
        if not session_date and trade_data.get("entry_time"):
            session_date = str(trade_data["entry_time"])[:10]

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_trades (
                    session_id, gateway, strategy_name, symbol, session_date, trade_id, side,
                    quantity, signal_time, entry_time, entry_price, exit_time,
                    exit_price, exit_reason, gross_pnl, charges, net_pnl,
                    stop_loss, target, score, status, regime, peak_gain_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_data.get("session_id"),
                    trade_data.get("gateway", "KOTAK"),
                    trade_data.get("strategy_name", ""),
                    trade_data.get("symbol", ""),
                    session_date,
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
                    trade_data.get("status", "CLOSED"),
                    trade_data.get("regime", "TRENDING"),
                    trade_data.get("peak_gain_pct", 0.0),
                ),
            )
            return cur.lastrowid

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
        gateway: str = "KOTAK",
        session_id: Optional[int] = None,
        index_ltp: Optional[float] = None,
    ) -> int:
        """Persists a newly opened active live trade."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_trades (
                    session_id, gateway, strategy_name, symbol, session_date, trade_id,
                    side, quantity, signal_time, entry_time, entry_price, exit_time, exit_price,
                    exit_reason, gross_pnl, charges, net_pnl, stop_loss, target, score,
                    status, regime, peak_gain_pct, index_ltp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0.0, 'OPEN', 0.0, 20.0, -20.0, ?, ?, ?, 'ACTIVE', ?, 0.0, ?)
                """,
                (
                    session_id,
                    gateway,
                    strategy_name,
                    symbol,
                    session_date,
                    trade_id,
                    side,
                    quantity,
                    entry_time,
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
        """Closes an active live trade with realized prices, exit reason, and P&L."""
        net_pnl = gross_pnl - charges
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE live_trades
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
        gateway: str = "KOTAK",
    ) -> None:
        """Records a session date where Live Trade evaluation was executed but 0 signals triggered."""
        with self._get_connection() as conn:
            exists = conn.execute(
                "SELECT COUNT(*) FROM live_sessions WHERE strategy_name = ? AND created_at LIKE ?",
                (strategy_name, f"{session_date}%"),
            ).fetchone()[0]
            if exists == 0:
                conn.execute(
                    """
                    INSERT INTO live_sessions (
                        created_at, gateway, strategy_name, symbol, isin,
                        timeframe, capital, status, total_trades, realized_pnl
                    ) VALUES (?, ?, ?, ?, '', 5, 100000.0, 'NO_TRADE', 0, 0.0)
                    """,
                    (
                        f"{session_date} 15:30:00",
                        gateway,
                        strategy_name,
                        symbol,
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
        Retrieves all live trades for the strategy within the date range or previous one month (max_days).
        Older records are strictly excluded.
        Also synthesizes 'No Trade' records for session days evaluated with 0 trades.
        """
        if start_date:
            cutoff_date = str(start_date)
        else:
            from datetime import date, timedelta
            cutoff_date = (date.today() - timedelta(days=max_days)).isoformat()

        query = "SELECT * FROM live_trades WHERE strategy_name = ? AND (session_date >= ? OR (session_date IS NULL AND entry_time >= ?))"
        params: List[Any] = [strategy_name, cutoff_date, cutoff_date]
        if end_date:
            end_date_str = str(end_date)
            end_date_ts = f"{end_date_str} 23:59:59" if len(end_date_str) == 10 else end_date_str
            query += " AND (session_date <= ? OR (session_date IS NULL AND entry_time <= ?))"
            params.extend([end_date_str, end_date_ts])
        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY session_date DESC, id DESC"

        with self._get_connection() as conn:
            trade_rows = [dict(r) for r in conn.execute(query, params).fetchall()]

            # Also check for 'NO_TRADE' sessions
            sess_query = "SELECT * FROM live_sessions WHERE strategy_name = ? AND (total_trades = 0 OR status = 'NO_TRADE') AND created_at >= ?"
            sess_params: List[Any] = [strategy_name, cutoff_date]
            if end_date:
                end_date_str = str(end_date)
                end_date_ts = f"{end_date_str} 23:59:59" if len(end_date_str) == 10 else end_date_str
                sess_query += " AND created_at <= ?"
                sess_params.append(end_date_ts)
            if symbol and symbol != "ALL":
                sess_query += " AND symbol = ?"
                sess_params.append(symbol)
            sess_rows = [dict(r) for r in conn.execute(sess_query, sess_params).fetchall()]

            dates_with_trades = set(t.get("session_date") or str(t.get("entry_time", ""))[:10] for t in trade_rows)
            for r in sess_rows:
                s_date = str(r.get("created_at", ""))[:10]
                if s_date and s_date not in dates_with_trades:
                    trade_rows.append({
                        "id": -(r["id"]),
                        "session_id": r["id"],
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
                key=lambda x: (str(x.get("session_date") or str(x.get("entry_time", ""))[:10]), int(x.get("id", 0)) if isinstance(x.get("id"), int) else 0),
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
        """Returns unique dates (within date range or previous one month) when live trades were executed."""
        if start_date:
            cutoff_date = str(start_date)
        else:
            from datetime import date, timedelta
            cutoff_date = (date.today() - timedelta(days=max_days)).isoformat()
        query = "SELECT DISTINCT COALESCE(session_date, SUBSTR(entry_time, 1, 10)) as dt FROM live_trades WHERE strategy_name = ? AND status != 'NO_TRADE' AND (session_date >= ? OR entry_time >= ?)"
        params: List[Any] = [strategy_name, cutoff_date, cutoff_date]
        if end_date:
            end_date_str = str(end_date)
            end_date_ts = f"{end_date_str} 23:59:59" if len(end_date_str) == 10 else end_date_str
            query += " AND (session_date <= ? OR entry_time <= ?)"
            params.extend([end_date_str, end_date_ts])
        if symbol and symbol != "ALL":
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY dt DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [r[0] for r in rows if r[0]]

    def get_all_strategy_names(self) -> List[str]:
        """Returns distinct strategy names stored in live trades."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT strategy_name FROM live_trades ORDER BY strategy_name ASC"
            ).fetchall()
            return [r[0] for r in rows if r[0]]

    def clear_history_for_strategy(self, strategy_name: str) -> None:
        """Deletes all trade and session records for a specific strategy."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM live_trades WHERE strategy_name = ?", (strategy_name,))
            conn.execute("DELETE FROM live_sessions WHERE strategy_name = ?", (strategy_name,))

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
                    order_data.get("gateway", "KOTAK"),
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

    def get_trades(
        self,
        strategy_name: Optional[str] = None,
        symbol: Optional[str] = None,
        gateway: Optional[str] = None,
        session_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM live_trades WHERE 1=1"
        params = []
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
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


def get_live_db(db_path: str = DEFAULT_DB_PATH) -> LiveTradeDatabase:
    global _DB_INSTANCE
    if _DB_INSTANCE is None or _DB_INSTANCE.db_path != db_path:
        _DB_INSTANCE = LiveTradeDatabase(db_path)
    return _DB_INSTANCE

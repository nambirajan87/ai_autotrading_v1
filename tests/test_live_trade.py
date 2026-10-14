import os
import pytest
import pandas as pd
from datetime import datetime
from app.database.live_database import LiveTradeDatabase
from app.execution.live_trade_engine import LiveTradeEngine
from app.gateways.gateway_factory import get_gateway


def test_live_database(tmp_path):
    db_file = str(tmp_path / "test_live.db")
    db = LiveTradeDatabase(db_file)

    session_id = db.start_session(
        gateway="KOTAK",
        strategy_name="EMA_RSI_MACD_Balanced",
        symbol="NIFTY26OCT22500PE",
        isin="INF000513319",
        timeframe=1,
        capital=100000.0,
    )
    assert session_id > 0

    trade_id = db.record_trade({
        "session_id": session_id,
        "gateway": "KOTAK",
        "strategy_name": "EMA_RSI_MACD_Balanced",
        "symbol": "NIFTY26OCT22500PE",
        "trade_id": 1,
        "side": "LONG",
        "quantity": 65,
        "signal_time": "2026-10-07 12:13:00",
        "entry_time": "2026-10-07 12:13:00",
        "entry_price": 184.89,
        "exit_time": "2026-10-07 13:10:00",
        "exit_price": 240.24,
        "exit_reason": "TARGET",
        "gross_pnl": 3597.75,
        "charges": 0.0,
        "net_pnl": 3597.75,
        "score": 2,
    })
    assert trade_id > 0

    trades = db.get_trades(strategy_name="EMA_RSI_MACD_Balanced")
    assert len(trades) == 1
    assert trades[0]["net_pnl"] == 3597.75
    assert trades[0]["entry_time"] == "2026-10-07 12:13:00"

    # Query with session_id
    trades_by_session = db.get_trades(session_id=session_id)
    assert len(trades_by_session) == 1
    assert trades_by_session[0]["session_id"] == session_id

    # Query with non-matching session_id
    empty_trades = db.get_trades(session_id=999999)
    assert len(empty_trades) == 0


def test_live_trade_engine_step():
    gw = get_gateway("KOTAK")
    engine = LiveTradeEngine(
        gateway=gw,
        strategy_name="EMA_RSI_MACD_Balanced",
        symbol="NIFTY26OCT22500PE",
        isin="INF000513319",
        timeframe=1,
        capital=100000.0,
        lot_size=65,
    )
    assert engine.is_active is True
    assert engine.session_id > 0

    # Simulate candle
    candle = pd.Series({
        "timestamp": pd.Timestamp("2026-10-07 12:13:00"),
        "open": 182.55,
        "high": 188.0,
        "low": 182.55,
        "close": 184.8,
        "volume": 14000,
    })
    res = engine.on_candle_close(candle)
    assert "status" in res
    assert res["status"] == "OK"

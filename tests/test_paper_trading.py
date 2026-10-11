import dataclasses
import json
from datetime import date

import pandas as pd

from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.database.database import Database
from app.execution.broker_interface import Order
from app.execution.paper_broker import PaperBroker
from app.execution.paper_trader import PaperTrader
from app.portfolio.portfolio import Portfolio

ZERO = CostConfig.zero()


def candle(ts, o, h, l, c, kind="neutral", atr=10.0):
    """kind 'bull' -> score +8 (LONG); 'neutral' -> score 0 (HOLD)."""
    row = dict(timestamp=pd.Timestamp(ts), open=o, high=h, low=l, close=c, volume=1000, atr14=atr)
    if kind == "bull":
        row.update(ema20=c + 5, ema50=c - 5, rsi14=60.0, macd=1.0, macd_signal=0.0,
                   macd_hist=1.0, vwap=c - 10, adx=25.0, plus_di=30.0, minus_di=15.0,
                   volume_ratio=1.5)
    else:
        row.update(ema20=c, ema50=c, rsi14=50.0, macd=0.0, macd_signal=0.0,
                   macd_hist=0.0, vwap=c, adx=10.0, plus_di=10.0, minus_di=10.0,
                   volume_ratio=1.0)
    return row


def run_rows(rows, risk_cfg=None):
    db = Database(":memory:")
    broker = PaperBroker(100_000.0, ZERO)
    cfg = risk_cfg if risk_cfg is not None else RiskConfig(cash_buffer_pct=0.0)
    trader = PaperTrader("TEST", StrategyConfig(), cfg, broker, db)
    trader.run(pd.DataFrame(rows))
    return trader, broker, db


def test_schema_and_event_roundtrip():
    db = Database(":memory:")
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"strategy_runs", "signals", "risk_events", "orders", "executions",
            "positions", "trades", "system_events"} <= names
    run_id = db.start_run("X", "strategy_v1", "{}")
    db.log_event(run_id, "2026-08-03 10:00", "INFO", "hello")
    assert db.query("SELECT * FROM system_events")[0]["message"] == "hello"


def test_order_upsert_updates_instead_of_duplicating():
    db = Database(":memory:")
    order = Order(order_id="O1", symbol="X", side="BUY", order_type="MARKET", quantity=10)
    db.upsert_order(1, order, "t1")
    db.upsert_order(1, dataclasses.replace(order, status="FILLED", filled_quantity=10), "t2")
    rows = db.query("SELECT * FROM orders")
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["created_at"], rows[0]["updated_at"]) == ("FILLED", "t1", "t2")


def test_portfolio_daily_pnl_and_loss_streak():
    p = Portfolio()
    ts = pd.Timestamp("2026-08-03 10:30")
    for pnl in (-100.0, 50.0, -30.0, -20.0):
        p.record_trade(ts, pnl)
    assert p.realized_today(date(2026, 8, 3)) == -100.0
    assert p.consecutive_losses(date(2026, 8, 3)) == 2
    assert p.realized_today(date(2026, 8, 4)) == 0.0
    assert p.consecutive_losses(date(2026, 8, 4)) == 0


def test_risk_state_includes_unrealized_pnl():
    b = PaperBroker(100_000.0, ZERO)
    b.place_order("X", "BUY", 10)
    b.on_candle("X", pd.Series({"timestamp": pd.Timestamp("2026-08-03 10:00"), "open": 100,
                                "high": 101, "low": 89, "close": 90, "volume": 1000}))
    state = Portfolio().risk_state(b, date(2026, 8, 3))
    assert state.daily_pnl == -100.0                      # 10 x (90 - 100), nothing realized yet
    assert state.open_positions[0].quantity == 10
    assert state.open_positions[0].side == "LONG"


def test_target_trade_full_audit_trail():
    trader, broker, db = run_rows([
        candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),   # signal
        candle("2026-08-03 10:05", 2500, 2532, 2499, 2528),           # entry fills @2500; high hits target
        candle("2026-08-03 10:10", 2528, 2530, 2526, 2528),           # exit fills @ this open
    ])
    trades = db.query("SELECT * FROM trades")
    assert len(trades) == 1
    r = trades[0]
    assert (r["side"], r["quantity"], r["exit_reason"]) == ("LONG", 40, "TARGET")
    assert (r["entry_price"], r["exit_price"]) == (2500.0, 2528.0)
    assert r["gross_pnl"] == 1120.0 and r["net_pnl"] == 1120.0     # (2528-2500) x 40
    assert (r["strategy_version"], r["score"]) == ("strategy_v1", 8)
    assert json.loads(r["indicators_json"])["atr"] == 10.0
    assert json.loads(r["risk_json"])["stop_loss"] == 2485.0
    assert [o["status"] for o in db.query("SELECT status FROM orders ORDER BY id")] == ["FILLED", "FILLED"]
    assert db.query("SELECT COUNT(*) AS n FROM executions")[0]["n"] == 2
    assert db.query("SELECT COUNT(*) AS n FROM signals WHERE signal='LONG'")[0]["n"] == 1
    assert db.query("SELECT approved FROM risk_events")[0]["approved"] == 1
    assert broker.get_balance()["net_realized_pnl"] == 1120.0      # broker and trades agree
    assert broker.get_positions() == []


def test_stop_exit_fills_next_open_not_stop_price():
    _, _, db = run_rows([
        candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
        candle("2026-08-03 10:05", 2500, 2505, 2480, 2500),           # low breaks the 2485 stop
        candle("2026-08-03 10:10", 2470, 2475, 2465, 2470),           # exit fills at THIS open
    ])
    r = db.query("SELECT * FROM trades")[0]
    assert r["exit_reason"] == "STOP"
    assert r["exit_price"] == 2470.0
    assert r["net_pnl"] == -1200.0        # the backtester would have exited at 2485 for -600


def test_square_off_exit():
    rows = [candle("2026-08-03 14:55", 2500, 2502, 2498, 2500, "bull")]
    for ts in ["15:00", "15:05", "15:10"]:
        rows.append(candle(f"2026-08-03 {ts}", 2500, 2505, 2495, 2500))
    rows.append(candle("2026-08-03 15:15", 2502, 2505, 2498, 2502))   # square-off decided here
    rows.append(candle("2026-08-03 15:20", 2503, 2505, 2500, 2503))   # ...and filled at this open
    r = run_rows(rows)[2].query("SELECT * FROM trades")[0]
    assert r["exit_reason"] == "SQUARE_OFF"
    assert (r["exit_price"], r["gross_pnl"]) == (2503.0, 120.0)


def test_rejected_signal_is_recorded_and_no_order_placed():
    _, _, db = run_rows([
        candle("2026-08-03 09:15", 2500, 2502, 2498, 2500, "bull"),   # before entry_start 09:20
        candle("2026-08-03 09:20", 2500, 2501, 2499, 2500),
    ])
    events = db.query("SELECT * FROM risk_events")
    assert len(events) == 1 and events[0]["approved"] == 0
    assert "entry hours" in events[0]["reason"]
    assert db.query("SELECT COUNT(*) AS n FROM orders")[0]["n"] == 0
    assert db.query("SELECT COUNT(*) AS n FROM trades")[0]["n"] == 0
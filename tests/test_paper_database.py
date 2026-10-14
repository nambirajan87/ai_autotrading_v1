import os
import pytest
from app.database.paper_database import PaperTradeDatabase


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_paper.db")
    return PaperTradeDatabase(db_path=db_file)


def test_paper_database_initialization_and_seed(temp_db):
    strats = temp_db.get_all_strategy_names()
    assert "EMA_RSI_MACD_Balanced" in strats

    trades = temp_db.get_trades_for_strategy("EMA_RSI_MACD_Balanced")
    assert len(trades) == 2
    assert trades[0]["side"] in ("LONG", "SHORT")
    assert trades[0]["net_pnl"] > 0

    metrics = temp_db.get_strategy_metrics("EMA_RSI_MACD_Balanced")
    assert metrics["total_trades"] == 2
    assert metrics["win_trades"] == 2
    assert metrics["win_rate"] == 100.0
    assert metrics["net_pnl"] > 0


def test_paper_database_save_run_and_trades(temp_db):
    run_data = {
        "strategy_name": "TEST_STRATEGY",
        "symbol": "TCS",
        "isin": "INE467B01029",
        "session_date": "2026-10-09",
        "timeframe": 5,
        "initial_capital": 100000.0,
        "final_equity": 101200.0,
        "total_trades": 1,
        "win_rate": 100.0,
        "gross_pnl": 1250.0,
        "charges": 50.0,
        "net_pnl": 1200.0,
        "status": "COMPLETED",
    }
    trade_data = [{
        "trade_id": 1,
        "symbol": "TCS",
        "side": "LONG",
        "quantity": 10,
        "entry_time": "2026-10-09 10:00:00",
        "entry_price": 3500.0,
        "exit_time": "2026-10-09 11:30:00",
        "exit_price": 3625.0,
        "exit_reason": "TARGET",
        "gross_pnl": 1250.0,
        "charges": 50.0,
        "net_pnl": 1200.0,
        "stop_loss": 3450.0,
        "target": 3625.0,
        "score": 5,
    }]

    run_id = temp_db.save_run(run_data, trade_data)
    assert run_id is not None

    trades = temp_db.get_trades_for_strategy("TEST_STRATEGY")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "TCS"
    assert trades[0]["net_pnl"] == 1200.0

    metrics = temp_db.get_strategy_metrics("TEST_STRATEGY")
    assert metrics["total_trades"] == 1
    assert metrics["win_rate"] == 100.0
    assert metrics["net_pnl"] == 1200.0


def test_paper_database_filters_and_clear(temp_db):
    # Test outcome filter
    wins = temp_db.get_trades_for_strategy("EMA_RSI_MACD_Balanced", outcome_filter="WINS")
    assert len(wins) == 2

    losses = temp_db.get_trades_for_strategy("EMA_RSI_MACD_Balanced", outcome_filter="LOSSES")
    assert len(losses) == 0

    # Clear history
    temp_db.clear_history_for_strategy("EMA_RSI_MACD_Balanced")
    empty_trades = temp_db.get_trades_for_strategy("EMA_RSI_MACD_Balanced")
    assert len(empty_trades) == 0


def test_paper_database_runs_and_metrics_filtering(temp_db):
    # Add a winning run and a losing run
    temp_db.save_run({
        "strategy_name": "MULTI_SYM",
        "symbol": "INFY",
        "isin": "INE009A01021",
        "session_date": "2026-10-08",
        "timeframe": 5,
        "initial_capital": 100000.0,
        "final_equity": 102000.0,
        "total_trades": 1,
        "win_rate": 100.0,
        "gross_pnl": 2040.0,
        "charges": 40.0,
        "net_pnl": 2000.0,
        "status": "COMPLETED",
    }, [{
        "trade_id": 1, "symbol": "INFY", "side": "LONG", "quantity": 20,
        "entry_time": "2026-10-08 10:00:00", "entry_price": 1500.0,
        "exit_time": "2026-10-08 11:00:00", "exit_price": 1602.0,
        "exit_reason": "TARGET", "gross_pnl": 2040.0, "charges": 40.0, "net_pnl": 2000.0,
    }])

    temp_db.save_run({
        "strategy_name": "MULTI_SYM",
        "symbol": "WIPRO",
        "isin": "INE075A01022",
        "session_date": "2026-10-08",
        "timeframe": 5,
        "initial_capital": 100000.0,
        "final_equity": 99000.0,
        "total_trades": 1,
        "win_rate": 0.0,
        "gross_pnl": -960.0,
        "charges": 40.0,
        "net_pnl": -1000.0,
        "status": "COMPLETED",
    }, [{
        "trade_id": 2, "symbol": "WIPRO", "side": "SHORT", "quantity": 50,
        "entry_time": "2026-10-08 12:00:00", "entry_price": 500.0,
        "exit_time": "2026-10-08 13:00:00", "exit_price": 519.2,
        "exit_reason": "STOP_LOSS", "gross_pnl": -960.0, "charges": 40.0, "net_pnl": -1000.0,
    }])

    # Test runs filtering by symbol
    all_runs = temp_db.get_runs_for_strategy("MULTI_SYM")
    assert len(all_runs) == 2

    infy_runs = temp_db.get_runs_for_strategy("MULTI_SYM", symbol="INFY")
    assert len(infy_runs) == 1
    assert infy_runs[0]["symbol"] == "INFY"

    wipro_runs = temp_db.get_runs_for_strategy("MULTI_SYM", symbol="WIPRO")
    assert len(wipro_runs) == 1
    assert wipro_runs[0]["symbol"] == "WIPRO"

    # Test runs filtering by outcome
    win_runs = temp_db.get_runs_for_strategy("MULTI_SYM", outcome_filter="WINS")
    assert len(win_runs) == 1
    assert win_runs[0]["symbol"] == "INFY"

    loss_runs = temp_db.get_runs_for_strategy("MULTI_SYM", outcome_filter="LOSSES")
    assert len(loss_runs) == 1
    assert loss_runs[0]["symbol"] == "WIPRO"

    # Test metrics filtering by outcome
    win_metrics = temp_db.get_strategy_metrics("MULTI_SYM", outcome_filter="WINS")
    assert win_metrics["total_trades"] == 1
    assert win_metrics["win_trades"] == 1
    assert win_metrics["win_rate"] == 100.0
    assert win_metrics["net_pnl"] == 2000.0

    loss_metrics = temp_db.get_strategy_metrics("MULTI_SYM", outcome_filter="LOSSES")
    assert loss_metrics["total_trades"] == 1
    assert loss_metrics["win_trades"] == 0
    assert loss_metrics["win_rate"] == 0.0
    assert loss_metrics["net_pnl"] == -1000.0


def test_paper_orders_persistence(temp_db):
    # Test manual order recording
    order_id = temp_db.record_order({
        "run_id": 99,
        "gateway": "KOTAK-PAPER",
        "symbol": "NIFTY26OCT22500PE",
        "side": "BUY",
        "quantity": 65,
        "price": 200.0,
        "status": "FILLED",
        "reason": "MOMENTUM_BREAKOUT",
    })
    assert order_id > 0

    orders = temp_db.get_orders(run_id=99)
    assert len(orders) == 1
    assert orders[0]["symbol"] == "NIFTY26OCT22500PE"
    assert orders[0]["side"] == "BUY"
    assert orders[0]["price"] == 200.0
    assert orders[0]["status"] == "FILLED"

    # Test auto-order creation during save_run
    run_id = temp_db.save_run(
        {"strategy_name": "ORDER_TEST", "symbol": "RELIANCE"},
        [{
            "trade_id": 1,
            "side": "LONG",
            "quantity": 50,
            "entry_time": "2026-10-09 10:00:00",
            "entry_price": 2500.0,
            "exit_time": "2026-10-09 11:00:00",
            "exit_price": 2550.0,
            "exit_reason": "TARGET",
            "gross_pnl": 2500.0,
            "charges": 40.0,
            "net_pnl": 2460.0,
        }],
    )
    run_orders = temp_db.get_orders(run_id=run_id)
    assert len(run_orders) == 2  # 1 entry order + 1 exit order
    assert run_orders[0]["side"] in ("BUY", "SELL")
    assert run_orders[1]["side"] in ("BUY", "SELL")


def test_accumulated_trades_and_active_trade_lifecycle(temp_db):
    from datetime import date, timedelta
    today_str = str(date.today())
    old_date_str = str(date.today() - timedelta(days=45))

    # 1. Save an active open trade
    t_id = temp_db.save_active_trade(
        strategy_name="TEST_ACCUM",
        symbol="NIFTY26OCT22500PE",
        session_date=today_str,
        trade_id=5000,
        side="LONG",
        quantity=65,
        entry_time=f"{today_str} 10:15:00",
        entry_price=160.0,
        stop_loss=154.0,
        target=175.0,
        score=5,
        regime="TRENDING",
    )
    assert t_id is not None

    accum = temp_db.get_accumulated_trades_for_strategy("TEST_ACCUM", max_days=30)
    assert len(accum) == 1
    assert accum[0]["status"] == "ACTIVE"
    assert accum[0]["side"] == "LONG"
    assert accum[0]["entry_price"] == 160.0

    # 2. Close the active trade
    temp_db.close_active_trade(
        trade_id_or_row_id=t_id,
        exit_time=f"{today_str} 11:30:00",
        exit_price=175.0,
        exit_reason="TARGET",
        gross_pnl=975.0,
        charges=20.0,
        peak_gain_pct=9.38,
    )
    accum_closed = temp_db.get_accumulated_trades_for_strategy("TEST_ACCUM", max_days=30)
    assert len(accum_closed) == 1
    assert accum_closed[0]["status"] == "CLOSED"
    assert accum_closed[0]["exit_reason"] == "TARGET"
    assert accum_closed[0]["net_pnl"] == 955.0

    # 3. Record a No-Trade session
    temp_db.record_no_trade_day("TEST_ACCUM", "NIFTY26OCT22500PE", str(date.today() - timedelta(days=2)))
    accum_with_no_trade = temp_db.get_accumulated_trades_for_strategy("TEST_ACCUM", max_days=30)
    assert len(accum_with_no_trade) == 2
    sides = [t["side"] for t in accum_with_no_trade]
    assert "No Trade" in sides
    assert "LONG" in sides

    # 4. Ensure records older than 30 days are omitted
    temp_db.save_single_trade(
        strategy_name="TEST_ACCUM",
        symbol="NIFTY26OCT22500PE",
        session_date=old_date_str,
        trade={"trade_id": 9999, "side": "LONG", "gross_pnl": 500.0, "net_pnl": 480.0, "entry_time": f"{old_date_str} 10:00:00"},
    )
    accum_30d = temp_db.get_accumulated_trades_for_strategy("TEST_ACCUM", max_days=30)
    dates_in_accum = [t["session_date"] for t in accum_30d]
    assert old_date_str not in dates_in_accum



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


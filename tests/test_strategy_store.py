import os
import json
import pytest
from app.strategy.strategy_store import (
    auto_suggest_strategy_name,
    save_strategy,
    get_strategy,
    list_strategy_names,
    delete_strategy,
    load_all_strategies,
    STRATEGIES_FILE,
)
from app.database.backtest_database import BacktestDatabase


def test_auto_suggest_strategy_name():
    # Only EMA
    ind1 = {"ema_enabled": True, "ema_fast": 20, "ema_slow": 50}
    assert auto_suggest_strategy_name(ind1) == "EMA20_50_Consensus"

    # EMA + RSI + MACD
    ind2 = {
        "ema_enabled": True, "ema_fast": 9, "ema_slow": 21,
        "rsi_enabled": True,
        "macd_enabled": True,
    }
    assert auto_suggest_strategy_name(ind2) == "EMA9_21_RSI_MACD_Consensus"

    # None enabled
    ind3 = {}
    assert auto_suggest_strategy_name(ind3) == "Custom_Strategy_v1"


def test_strategy_store_save_load_delete(tmp_path):
    test_name = "Unit_Test_Momentum_Strategy"
    test_ind = {"ema_enabled": True, "ema_fast": 10, "ema_slow": 30, "rsi_enabled": True}
    test_risk = {"capital": 50000.0, "fixed_target_pct": 2.5}
    test_cost = {"cost_mode": "FLAT_PER_ORDER", "flat_cost_per_order": 15.0}

    save_strategy(test_name, test_ind, test_risk, test_cost)

    names = list_strategy_names()
    assert test_name in names

    loaded = get_strategy(test_name)
    assert loaded is not None
    assert loaded["indicators"]["ema_fast"] == 10
    assert loaded["risk"]["capital"] == 50000.0

    # Delete
    deleted = delete_strategy(test_name)
    assert deleted is True
    assert test_name not in list_strategy_names()


def test_database_strategy_filtering(tmp_path):
    db_file = str(tmp_path / "test_strat_filter.db")
    db = BacktestDatabase(path=db_file)

    # Insert runs with different strategy versions
    id1 = db.save_run(
        symbol="RELIANCE", isin="INE002A01018", date_from="2026-09-01", date_to="2026-09-10",
        timeframe="5m", strategy_version="Strategy_Alpha", config={"strategy_name": "Strategy_Alpha"},
        metrics={"net_pnl": 1250.0}, trades_df=None, status="COMPLETED"
    )
    id2 = db.save_run(
        symbol="INFY", isin="INE009A01021", date_from="2026-09-01", date_to="2026-09-10",
        timeframe="5m", strategy_version="Strategy_Beta", config={"strategy_name": "Strategy_Beta"},
        metrics={"net_pnl": -450.0}, trades_df=None, status="COMPLETED"
    )

    all_strats = db.get_distinct_strategies()
    assert "Strategy_Alpha" in all_strats
    assert "Strategy_Beta" in all_strats

    alpha_runs = db.get_recent_runs(limit=20, strategy_name="Strategy_Alpha")
    assert len(alpha_runs) == 1
    assert alpha_runs[0]["id"] == id1

    beta_runs = db.get_recent_runs(limit=20, strategy_name="Strategy_Beta")
    assert len(beta_runs) == 1
    assert beta_runs[0]["id"] == id2

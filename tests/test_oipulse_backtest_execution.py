import os
from datetime import datetime
import pandas as pd
import pytest

from app.strategy.strategy_store import get_strategy
from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators
from app.backtest.engine import run_backtest, BacktestConfig
from app.config.risk_config import RiskConfig
from app.config.cost_config import CostConfig


def test_nifty_1m_csv_exists_and_populated():
    csv_path = "data/nifty_1m.csv"
    assert os.path.exists(csv_path), "data/nifty_1m.csv must exist"
    df = pd.read_csv(csv_path)
    assert len(df) >= 7000, f"data/nifty_1m.csv should have >= 7000 rows, found {len(df)}"
    for col in ["timestamp", "open", "high", "low", "close", "volume"]:
        assert col in df.columns, f"Column {col} missing in {csv_path}"


def test_oipulse_strategy_backtest_execution():
    strat = get_strategy("NIFTY26OCT22500PE_BOTH_PAPERTRADE_TEST_OI")
    assert strat is not None
    ind = strat["indicators"]
    risk_inp = strat["risk"]
    cost_inp = strat["cost"]

    raw_df = pd.read_csv("data/nifty_1m.csv")
    raw_df["timestamp"] = pd.to_datetime(raw_df["timestamp"])
    
    # Filter September 2026
    ts_d = raw_df["timestamp"].dt.date
    sep_df = raw_df[(ts_d >= datetime(2026, 9, 1).date()) & (ts_d <= datetime(2026, 9, 30).date())].copy()
    assert len(sep_df) >= 7000

    cfg = CustomStrategyConfig(
        name=strat["name"],
        ema_enabled=ind["ema_enabled"], ema_fast=ind["ema_fast"], ema_slow=ind["ema_slow"], ema_weight=ind["ema_weight"],
        rsi_enabled=ind["rsi_enabled"], rsi_period=ind["rsi_period"], rsi_bull_low=ind["rsi_bull_low"],
        rsi_bull_high=ind["rsi_bull_high"], rsi_bear_low=ind["rsi_bear_low"], rsi_bear_high=ind["rsi_bear_high"], rsi_weight=ind["rsi_weight"],
        macd_enabled=ind["macd_enabled"], macd_fast=ind["macd_fast"], macd_slow=ind["macd_slow"], macd_signal=ind["macd_signal"], macd_weight=ind["macd_weight"],
        vwap_enabled=ind["vwap_enabled"], vwap_weight=ind["vwap_weight"],
        adx_enabled=ind["adx_enabled"], adx_period=ind["adx_period"], adx_min=ind["adx_min"], adx_weight=ind["adx_weight"],
        volume_enabled=ind["volume_enabled"], volume_period=ind["volume_period"], volume_ratio_min=ind["volume_ratio_min"],
        volume_consecutive=int(ind.get("volume_consecutive", 2)), volume_weight=ind["volume_weight"],
        higher_high_enabled=bool(ind.get("higher_high_enabled", True)), higher_high_period=int(ind.get("higher_high_period", 35)),
        higher_high_weight=int(ind.get("higher_high_weight", 1)),
        lower_low_enabled=bool(ind.get("lower_low_enabled", True)), lower_low_period=int(ind.get("lower_low_period", 35)),
        lower_low_weight=int(ind.get("lower_low_weight", 1)),
        long_threshold=ind["long_threshold"], short_threshold=ind["short_threshold"],
        fixed_target_pct=float(ind.get("fixed_target_pct", 30)), fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 5)),
    )

    indicators_df = calculate_custom_indicators(sep_df, cfg)
    assert not indicators_df.empty

    risk_cfg = RiskConfig(
        capital=float(risk_inp["capital"]),
        risk_per_trade_pct=float(risk_inp["risk_per_trade_pct"]),
        max_daily_loss=float(risk_inp["max_daily_loss"]),
        max_open_positions=int(risk_inp["max_open_positions"]),
        max_exposure_pct=float(risk_inp["max_exposure_pct"]),
        max_quantity=int(risk_inp["max_quantity"]),
        max_consecutive_losses=int(risk_inp["max_consecutive_losses"]),
        lot_size=int(risk_inp["lot_size"]),
        tick_size=float(risk_inp["tick_size"]),
        trade_side=ind.get("trade_side", "BOTH"),
        short_lot_size=1,
        fixed_target_pct=float(ind.get("fixed_target_pct", 30)),
        fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 5)),
        entry_start=risk_inp["entry_start"],
        entry_end=risk_inp["entry_end"],
        square_off_time=risk_inp["square_off_time"],
    )

    cost_cfg = CostConfig(cost_mode=cost_inp["cost_mode"], flat_cost_per_order=float(cost_inp["flat_cost_per_order"]))

    bt_cfg = BacktestConfig(
        allow_short=bool(strat["instrument"]["allow_short"]),
        trade_side=ind.get("trade_side", "BOTH"),
        fo_mode="OIPulse Mode",
    )

    trades_df, bt = run_backtest(indicators_df, "NIFTY", cfg, risk_cfg, cost_cfg, bt_cfg)

    # Must execute trades
    assert len(trades_df) > 0, "Backtest in OIPulse Mode must execute trades"
    
    # Contract symbols must resolve dynamically with market weekly format (e.g., S for Sept)
    symbols = trades_df["symbol"].tolist()
    assert any("26S" in s or "26SEP" in s or "26O" in s for s in symbols), f"Expected resolved option symbols, got {symbols}"
    
    # Verify option contracts are traded
    assert any(s.endswith("CE") or s.endswith("PE") for s in symbols), "Expected option contract trades"
    if ind.get("trade_side") == "BOTH":
        assert any(s.endswith("CE") for s in symbols), "Expected CE trade when BOTH allowed"
        assert any(s.endswith("PE") for s in symbols), "Expected PE trade when BOTH allowed"

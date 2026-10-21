import sys
sys.path.insert(0, ".")
import os
import sqlite3
import pandas as pd
from datetime import datetime, date, time as dt_time

from app.strategy.strategy_store import get_strategy
from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators
from app.strategy.oipulse_resolver import parse_strength_value
from app.backtest.engine import run_backtest, BacktestConfig
from app.config.risk_config import RiskConfig
from app.config.cost_config import CostConfig
from app.backtest.metrics import compute_metrics
from app.backtest.runner import trading_days
from app.database.backtest_database import BacktestDatabase

def run_september_backtest():
    start_d = date(2026, 9, 1)
    end_d = date(2026, 9, 30)

    # 1. Load oi_strength table directly from data/backtest.db
    db_path = "data/backtest.db"
    conn = sqlite3.connect(db_path)
    df_oi = pd.read_sql("SELECT Date, Time, LTP, Strength FROM oi_strength WHERE Time != 'EOD' ORDER BY id ASC", conn)
    conn.close()

    records = []
    for _, row in df_oi.iterrows():
        try:
            d_str = row["Date"].strip()
            t_str = row["Time"].strip()
            dt = datetime.strptime(f"{d_str} {t_str}", "%d-%m-%Y %H:%M:%S")
            if start_d <= dt.date() <= end_d:
                ltp = float(row["LTP"])
                strength = parse_strength_value(row["Strength"])
                records.append({
                    "timestamp": dt,
                    "ltp": ltp,
                    "strength": strength,
                })
        except Exception:
            pass

    raw_df = pd.DataFrame(records).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    raw_df["open"] = raw_df["ltp"].shift(1).fillna(raw_df["ltp"])
    raw_df["high"] = raw_df[["open", "ltp"]].max(axis=1) + 0.5
    raw_df["low"] = raw_df[["open", "ltp"]].min(axis=1) - 0.5
    raw_df["close"] = raw_df["ltp"]
    raw_df["volume"] = 50000

    # 2. Load Strategy
    strat = get_strategy("NIFTY26OCT22500PE_BOTH_PAPERTRADE_TEST_OI")
    ind = strat["indicators"]
    risk_inp = strat["risk"]
    cost_inp = strat["cost"]

    def parse_time_str(val):
        if isinstance(val, dt_time):
            return val
        parts = str(val).strip().split(":")
        return dt_time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)

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

    indicators_df = calculate_custom_indicators(raw_df, cfg)

    trade_side = ind.get("trade_side", "BOTH")
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
        trade_side=trade_side,
        short_lot_size=1,
        fixed_target_pct=float(ind.get("fixed_target_pct", 30)),
        fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 5)),
        entry_start=parse_time_str(risk_inp["entry_start"]),
        entry_end=parse_time_str(risk_inp["entry_end"]),
        square_off_time=parse_time_str(risk_inp["square_off_time"]),
    )

    cost_cfg = CostConfig(cost_mode=cost_inp["cost_mode"], flat_cost_per_order=float(cost_inp["flat_cost_per_order"]))

    bt_cfg = BacktestConfig(
        allow_short=bool(strat["instrument"].get("allow_short", True)),
        trade_side=trade_side,
        fo_mode="OIPulse Mode",
    )

    trades_df, bt = run_backtest(indicators_df, "NIFTY", cfg, risk_cfg, cost_cfg, bt_cfg)
    metrics = compute_metrics(trades_df, risk_cfg.capital, trading_days(indicators_df))

    # Save to Backtest Database
    db = BacktestDatabase()
    full_config_save = {
        "strategy_name": strat["name"],
        "symbol": "NIFTY",
        "date_from": str(start_d),
        "date_to": str(end_d),
        "timeframe": 1,
        "fo_mode": "OIPulse Mode",
        "indicators": ind,
        "risk": {k: str(v) if isinstance(v, (dt_time, date)) else v for k, v in risk_inp.items()},
        "cost": cost_inp,
    }
    run_id = db.save_run(
        symbol="NIFTY",
        isin="INF204KB14I2",
        date_from=str(start_d),
        date_to=str(end_d),
        timeframe="1m",
        strategy_version=strat["name"],
        config=full_config_save,
        metrics=metrics,
        trades_df=trades_df,
        status="COMPLETED",
    )

    print("\n" + "="*70)
    print("BACKTEST AUDIT & RESULTS: 1-SEP-2026 TO 30-SEP-2026 (OIPULSE MODE)")
    print("="*70)
    print(f"Strategy Name            : {strat['name']}")
    print(f"Underlying               : NIFTY")
    print(f"Execution Mode           : OIPulse Mode (Dynamic Option Selection via oi_strength)")
    print(f"Date Range               : {start_d} to {end_d}")
    print(f"Total 1m Candles Checked : {len(indicators_df)}")
    print(f"Database Saved Run ID    : #{run_id}")
    print("-"*70)
    print(f"Signal Counts:")
    for k, v in bt.signal_counts.items():
        print(f"  • {k}: {v:,} candles")
    print(f"\nRejections / Hold Breakdown:")
    for k, v in bt.rejections.items():
        print(f"  • {k}: {v:,}")

    win_trades = trades_df[trades_df["net_pnl"] > 0] if not trades_df.empty else pd.DataFrame()
    loss_trades = trades_df[trades_df["net_pnl"] < 0] if not trades_df.empty else pd.DataFrame()
    breakeven_trades = trades_df[trades_df["net_pnl"] == 0] if not trades_df.empty else pd.DataFrame()

    total_profit_gain = float(win_trades["net_pnl"].sum()) if not win_trades.empty else 0.0
    total_losses = float(loss_trades["net_pnl"].sum()) if not loss_trades.empty else 0.0
    net_pnl = float(trades_df["net_pnl"].sum()) if not trades_df.empty else 0.0

    print("-"*70)
    print(f"TRADES EXECUTIVE SUMMARY:")
    print(f"  • Total Trades Taken in LONG : {len(trades_df)}")
    print(f"  • Profitable Trades (Gain)   : {len(win_trades)} trade(s) | Total Gain: +Rs. {total_profit_gain:,.2f}")
    print(f"  • Losing Trades (Losses)     : {len(loss_trades)} trade(s) | Total Loss: -Rs. {abs(total_losses):,.2f}")
    print(f"  • Breakeven Trades           : {len(breakeven_trades)} trade(s)")
    print(f"  • Net Realized PnL           : Rs. {net_pnl:,.2f}")
    print(f"  • Win Rate                   : {metrics.get('win_rate_pct', 0.0):.1f}%")
    print(f"  • Total Trading Days         : {len(trading_days(indicators_df))}")
    print(f"  • Total Charges & Slippage   : Rs. {float(metrics.get('total_costs', 0.0)) + float(metrics.get('total_slippage', 0.0)):,.2f}")
    print("-"*70)

    if not trades_df.empty:
        print("\nALL EXECUTED TRADES LIST:")
        t_display = trades_df[["symbol", "side", "entry_time", "exit_time", "entry_price", "exit_price", "gross_pnl", "net_pnl", "exit_reason"]].copy()
        print(t_display.to_string(index=False))

    return trades_df, metrics, run_id

if __name__ == "__main__":
    run_september_backtest()

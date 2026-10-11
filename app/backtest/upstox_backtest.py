import sys

from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.backtest.runner import trading_days
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.data.historical_data import load_csv
from app.indicators.indicator_engine import calculate_all
from app.monitoring.logging_setup import setup_logging

if __name__ == "__main__":
    setup_logging("WARNING")
    path = sys.argv[1] if len(sys.argv) > 1 else "data/upstox_5m.csv"
    df = calculate_all(load_csv(path))
    risk = RiskConfig()

    buy_hold = (df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100
    print(f"Buy-and-hold over the period: {buy_hold:.2f}%  (benchmark, not a recommendation)")

    for name, cost in [("WITH costs + slippage", CostConfig()), ("NO costs (comparison)", CostConfig.zero())]:
        trades, bt = run_backtest(df, "RELIANCE", StrategyConfig(), risk, cost)
        print(f"\n===== {name} =====")
        for k, v in compute_metrics(trades, risk.capital, trading_days(df)).items():
            print(f"{k:24s} {v}")
        print("Rejections:", dict(bt.rejections))
        if len(trades) and (trades["regime"] != "").any():
            print("\nBy regime at signal time:")
            print(trades.groupby("regime")["net_pnl"].agg(["count", "sum", "mean"]).round(2))
        if name.startswith("WITH"):
            trades.to_csv("data/upstox_backtest_trades.csv", index=False)
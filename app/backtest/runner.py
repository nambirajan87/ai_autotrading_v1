import pandas as pd

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.metrics import compute_metrics
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.data.regime_simulator import generate_regime_sample
from app.indicators.indicator_engine import calculate_all
from app.monitoring.logging_setup import setup_logging


def trading_days(df: pd.DataFrame) -> list:
    return sorted(df["timestamp"].dt.date.unique())


def split_by_days(df: pd.DataFrame, first_n_days: int):
    """(in-sample, out-of-sample). Indicators were computed on the full data
    beforehand; that is leak-free ONLY because they are causal (Phase 3 test)."""
    cut = trading_days(df)[first_n_days - 1]
    dates = df["timestamp"].dt.date
    return df[dates <= cut], df[dates > cut]


def report(name, df, cost_cfg, risk_cfg, strategy_cfg, save_to=None):
    trades, bt = run_backtest(df, "RELIANCE", strategy_cfg, risk_cfg, cost_cfg, BacktestConfig())
    metrics = compute_metrics(trades, risk_cfg.capital, trading_days(df))

    print(f"\n===== {name} =====")
    for key, value in metrics.items():
        print(f"{key:24s} {value}")

    if len(trades):
        print("\nBy exit reason:")
        print(trades["exit_reason"].value_counts().to_string())
        if (trades["regime"] != "").any():
            print("\nBy regime at signal time:")
            print(trades.groupby("regime")["net_pnl"].agg(["count", "sum", "mean"]).round(2))
    print("\nSignal candles:", dict(bt.signal_counts))
    print("Rejections:    ", dict(bt.rejections))
    if save_to:
        trades.to_csv(save_to, index=False)


if __name__ == "__main__":
    setup_logging("WARNING")      # keep the console readable (no per-trade INFO lines)
    strategy_cfg, risk_cfg = StrategyConfig(), RiskConfig()
    print("NOTE: synthetic random data. This demonstrates the machinery, not profitability.")

    data = calculate_all(generate_regime_sample())
    in_sample, out_sample = split_by_days(data, first_n_days=10)

    report("FULL (costs + slippage)", data, CostConfig(), risk_cfg, strategy_cfg,
           save_to="data/backtest_trades.csv")
    report("FULL (NO costs, NO slippage; comparison only)", data, CostConfig.zero(),
           risk_cfg, strategy_cfg)
    report("IN-SAMPLE (days 1-10)", in_sample, CostConfig(), risk_cfg, strategy_cfg)
    report("OUT-OF-SAMPLE (days 11-20)", out_sample, CostConfig(), risk_cfg, strategy_cfg)
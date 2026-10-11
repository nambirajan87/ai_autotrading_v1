import pandas as pd

from app.backtest.engine import run_backtest
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.data.regime_simulator import generate_regime_sample
from app.indicators.indicator_engine import calculate_all

rows = []
for seed in range(1, 11):
    df = calculate_all(generate_regime_sample(seed=seed))
    trades, _ = run_backtest(df, "RELIANCE", StrategyConfig(), RiskConfig(), CostConfig())
    if len(trades) == 0:
        continue
    by_regime = trades.groupby("regime")["net_pnl"].sum().round(0).to_dict()
    rows.append({"seed": seed, "trades": len(trades),
                 "net": round(trades["net_pnl"].sum()), **by_regime})

print(pd.DataFrame(rows).to_string(index=False))
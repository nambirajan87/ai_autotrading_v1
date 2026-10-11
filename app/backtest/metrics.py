import math

import pandas as pd


def _max_consecutive_losses(net: pd.Series) -> int:
    streak = best = 0
    for value in net:
        if value < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _sharpe(trades: pd.DataFrame, capital: float, trading_days: list):
    """Daily Sharpe, annualized with sqrt(252). Risk-free rate ignored.
    Days with no trades count as 0 return. None if undefined."""
    exit_day = pd.to_datetime(trades["exit_time"]).dt.date
    daily = trades.groupby(exit_day)["net_pnl"].sum().reindex(trading_days, fill_value=0.0)
    start_equity = capital + daily.cumsum() - daily
    returns = daily / start_equity
    if len(returns) < 2 or returns.std(ddof=1) == 0:
        return None
    return round(returns.mean() / returns.std(ddof=1) * math.sqrt(252), 2)


def compute_metrics(trades: pd.DataFrame, capital: float, trading_days: list) -> dict:
    if len(trades) == 0:
        return {"num_trades": 0, "net_pnl": 0.0}

    net = trades["net_pnl"]
    wins, losses = net[net > 0], net[net < 0]
    gross_wins, gross_losses = wins.sum(), -losses.sum()
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0

    # Equity after each closed trade (realized only), starting from initial capital
    equity = pd.concat([pd.Series([capital], dtype=float), capital + net.cumsum()],
                       ignore_index=True)
    peak = equity.cummax()
    drawdown = peak - equity

    return {
        "num_trades": len(trades),
        "net_pnl": round(float(net.sum()), 2),
        "gross_pnl": round(float(trades["gross_pnl"].sum()), 2),
        "total_costs": round(float(trades["costs"].sum()), 2),
        "total_slippage": round(float(trades["slippage_cost"].sum()), 2),
        "return_pct": round(float(net.sum()) / capital * 100, 2),
        "win_rate_pct": round(len(wins) / len(net) * 100, 1),
        "loss_rate_pct": round(len(losses) / len(net) * 100, 1),
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else None,
        "avg_trade": round(float(net.mean()), 2),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "risk_reward": round(avg_win / abs(avg_loss), 2) if avg_loss < 0 else None,
        "max_drawdown": round(float(drawdown.max()), 2),
        "max_drawdown_pct": round(float((drawdown / peak).max() * 100), 2),
        "max_consecutive_losses": _max_consecutive_losses(net),
        "sharpe": _sharpe(trades, capital, trading_days),
    }
import sys

import pandas as pd


def summarize(g: pd.DataFrame) -> pd.Series:
    net = g["net_pnl"]
    wins, losses = net[net > 0], net[net < 0]
    return pd.Series({
        "trades": len(g),
        "net_pnl": round(net.sum(), 2),
        "avg_trade": round(net.mean(), 2),
        "win_rate_%": round((net > 0).mean() * 100, 1),
        "avg_win": round(wins.mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses.mean(), 2) if len(losses) else 0.0,
        "gross_pnl": round(g["gross_pnl"].sum(), 2),
        "costs": round(g["costs"].sum(), 2),
    })


def by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    rows = {key: summarize(group) for key, group in df.groupby(column)}
    return pd.DataFrame(rows).T


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/backtest_trades.csv"
    df = pd.read_csv(path)
    print(f"{len(df)} trades loaded from {path}\n")

    for col in ["side", "regime", "exit_reason"]:
        print(f"--- by {col} ---")
        print(by(df, col).to_string(), "\n")

    print("--- trade count: side x regime ---")
    print(pd.crosstab(df["side"], df["regime"]).to_string(), "\n")
    print("--- trade count: side x exit_reason ---")
    print(pd.crosstab(df["side"], df["exit_reason"]).to_string())
    print("\nWARNING: buckets with fewer than ~30 trades are noise.")
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Largest of: high-low, |high-prev close|, |low-prev close|.
    Including prev close captures overnight gaps."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1)
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range = Wilder-smoothed true range (in price units)."""
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
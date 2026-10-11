import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average.

    alpha = 2 / (period + 1)
    EMA[t] = alpha * price[t] + (1 - alpha) * EMA[t-1]
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()
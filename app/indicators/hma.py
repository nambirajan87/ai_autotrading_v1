import numpy as np
import pandas as pd


def wma(series: pd.Series, period: int) -> pd.Series:
    """
    Computes the Weighted Moving Average (WMA) of a series with linear weights.
    Weights: 1, 2, ..., period.
    """
    if len(series) < period:
        return series.rolling(max(1, len(series)), min_periods=1).mean()

    weights = np.arange(1, period + 1, dtype=float)
    w_sum = weights.sum()

    def calc(window):
        return np.dot(window, weights) / w_sum

    return series.rolling(period).apply(calc, raw=True)


def hma(close: pd.Series, period: int = 100) -> pd.Series:
    """
    Hull Moving Average (HMA).
    Formula:
        WMA(2 * WMA(close, n/2) - WMA(close, n), sqrt(n))
    """
    if close is None or close.empty:
        return pd.Series(dtype=float)

    p = int(period)
    if p < 1:
        p = 100

    n_bars = len(close)
    if n_bars < 4:
        return close.copy()

    # If series length is smaller than period, adjust effective period
    effective_p = min(p, n_bars)
    half_p = max(1, int(effective_p / 2))
    sqrt_p = max(1, int(np.sqrt(effective_p)))

    wma_half = wma(close, half_p)
    wma_full = wma(close, effective_p)

    raw_diff = 2.0 * wma_half - wma_full
    result = wma(raw_diff, sqrt_p)

    # Backfill early NaN values using close price
    return result.bfill().fillna(close)

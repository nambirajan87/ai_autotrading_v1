import numpy as np
import pandas as pd


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Stochastic Oscillator (pure causal function).

    - %K: 100 * (Close - LowestLow) / (HighestHigh - LowestLow) over k_period
    - %D: SMA of %K over d_period
    """
    if k_period < 1 or d_period < 1:
        raise ValueError("k_period and d_period must be >= 1")

    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()

    hl_range = (high_max - low_min).replace(0, np.nan)
    stoch_k = 100.0 * (df["close"] - low_min) / hl_range
    stoch_d = stoch_k.rolling(d_period).mean()

    return pd.DataFrame({
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
    }, index=df.index)

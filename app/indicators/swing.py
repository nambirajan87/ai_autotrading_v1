import numpy as np
import pandas as pd


def swing_high_low(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    """Computes Previous Swing High and Previous Swing Low.

    - swing_high: maximum high of previous `period` candles (causal, shifted by 1).
    - swing_low: minimum low of previous `period` candles (causal, shifted by 1).
    - higher_high: True when current candle high > swing_high.
    - lower_low: True when current candle low < swing_low.
    """
    if period < 1:
        raise ValueError("period must be >= 1")

    min_p = min(period, 2)
    swing_high = df["high"].shift(1).rolling(window=period, min_periods=min_p).max()
    swing_low = df["low"].shift(1).rolling(window=period, min_periods=min_p).min()

    higher_high = (df["high"] > swing_high) & swing_high.notna()
    lower_low = (df["low"] < swing_low) & swing_low.notna()

    return pd.DataFrame({
        "swing_high": swing_high,
        "swing_low": swing_low,
        "higher_high": higher_high,
        "lower_low": lower_low,
    }, index=df.index)

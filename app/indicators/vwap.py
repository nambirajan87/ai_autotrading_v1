import numpy as np
import pandas as pd


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price, reset at the start of every trading day.

    typical price = (high + low + close) / 3
    VWAP[t] = cumulative(typical * volume) / cumulative(volume)   (within the day)
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df["timestamp"].dt.date

    cum_price_volume = (typical * df["volume"]).groupby(day).cumsum()
    cum_volume = df["volume"].groupby(day).cumsum()
    return cum_price_volume / cum_volume.replace(0, np.nan)
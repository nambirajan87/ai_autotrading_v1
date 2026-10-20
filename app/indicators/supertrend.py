import numpy as np
import pandas as pd

from app.indicators.atr import atr


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """
    Supertrend Indicator.
    Returns DataFrame with columns ['supertrend', 'trend']:
      - 'supertrend': Price level of the active trailing stop
      - 'trend': +1 for Bullish (Close > Supertrend), -1 for Bearish (Close < Supertrend)
    """
    if df is None or df.empty or len(df) == 0:
        return pd.DataFrame({"supertrend": [], "trend": []})

    n = len(df)
    p = max(1, int(period))
    m = float(multiplier)

    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values

    # Calculate ATR
    atr_series = atr(df, period=p)
    if atr_series.isna().any():
        atr_series = atr_series.fillna(df["high"] - df["low"]).bfill().fillna(1.0)
    atr_vals = atr_series.values

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (m * atr_vals)
    basic_lower = hl2 - (m * atr_vals)

    final_upper = np.zeros(n, dtype=float)
    final_lower = np.zeros(n, dtype=float)
    st = np.zeros(n, dtype=float)
    trend = np.zeros(n, dtype=int)

    # Initialize first bar
    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    trend[0] = 1 if close[0] >= hl2[0] else -1
    st[0] = final_lower[0] if trend[0] == 1 else final_upper[0]

    for i in range(1, n):
        # Final Upper Band
        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Final Lower Band
        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Trend & Supertrend line
        prev_st = st[i - 1]
        prev_upper = final_upper[i - 1]

        if prev_st == prev_upper:
            # Previous was in downtrend
            if close[i] > final_upper[i]:
                trend[i] = 1
                st[i] = final_lower[i]
            else:
                trend[i] = -1
                st[i] = final_upper[i]
        else:
            # Previous was in uptrend
            if close[i] < final_lower[i]:
                trend[i] = -1
                st[i] = final_upper[i]
            else:
                trend[i] = 1
                st[i] = final_lower[i]

    return pd.DataFrame(
        {"supertrend": st, "trend": trend},
        index=df.index,
    )

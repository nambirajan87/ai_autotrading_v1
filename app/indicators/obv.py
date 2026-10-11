import numpy as np
import pandas as pd


def on_balance_volume(df: pd.DataFrame, ma_period: int = 20) -> pd.DataFrame:
    """On-Balance Volume (OBV) and its moving average (pure causal function).

    - OBV: Cumulative sum of signed volume (+volume if close rises, -volume if close falls)
    - obv_ma: SMA of OBV over ma_period
    """
    if ma_period < 1:
        raise ValueError("ma_period must be >= 1")

    diff = df["close"].diff()
    sign = np.where(diff > 0, 1.0, np.where(diff < 0, -1.0, 0.0))
    # First candle has no diff, sign is 0.0
    volume_flow = pd.Series(sign * df["volume"].values, index=df.index)
    obv = volume_flow.cumsum()
    obv_ma = obv.rolling(ma_period).mean()

    return pd.DataFrame({
        "obv": obv,
        "obv_ma": obv_ma,
    }, index=df.index)

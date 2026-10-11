import numpy as np
import pandas as pd


def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands (pure causal function: uses only past and current candles).

    - bb_mid: SMA of close over period
    - bb_upper: bb_mid + std_dev * rolling_std
    - bb_lower: bb_mid - std_dev * rolling_std
    - bb_pct_b: (close - bb_lower) / (bb_upper - bb_lower)
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if std_dev <= 0:
        raise ValueError("std_dev must be positive")

    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + (std_dev * std)
    lower = mid - (std_dev * std)

    band_width = (upper - lower).replace(0, np.nan)
    pct_b = (close - lower) / band_width

    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_pct_b": pct_b,
    }, index=close.index)

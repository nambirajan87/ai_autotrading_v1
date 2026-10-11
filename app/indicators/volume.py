import numpy as np
import pandas as pd


def volume_features(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Current volume vs its recent average.
    volume_ratio > 1 means above-average participation."""
    volume_ma = df["volume"].rolling(window=period, min_periods=period).mean()
    return pd.DataFrame({
        "volume_ma": volume_ma,
        "volume_ratio": df["volume"] / volume_ma.replace(0, np.nan),
    })
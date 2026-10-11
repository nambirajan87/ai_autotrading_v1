import numpy as np
import pandas as pd


def detect_regime(df: pd.DataFrame, adx_threshold: float = 20.0) -> pd.Series:
    """Classifies market regime for each candle as 'UP', 'DOWN', or 'SIDEWAYS'.

    - 'SIDEWAYS': ADX < adx_threshold or unformed indicators (weak trend / chop)
    - 'UP': ADX >= adx_threshold and EMA20 > EMA50
    - 'DOWN': ADX >= adx_threshold and EMA20 < EMA50
    """
    adx_col = "adx" if "adx" in df.columns else "adx14"
    if adx_col not in df.columns or "ema20" not in df.columns or "ema50" not in df.columns:
        return pd.Series("SIDEWAYS", index=df.index, name="regime")

    conditions = [
        (df[adx_col] >= adx_threshold) & (df["ema20"] > df["ema50"]),
        (df[adx_col] >= adx_threshold) & (df["ema20"] < df["ema50"]),
    ]
    choices = ["UP", "DOWN"]
    regime = np.select(conditions, choices, default="SIDEWAYS")
    return pd.Series(regime, index=df.index, name="regime")


def detect_regime_row(row: pd.Series, adx_threshold: float = 20.0) -> str:
    adx_val = row.get("adx", row.get("adx14", np.nan))
    ema20_val = row.get("ema20", np.nan)
    ema50_val = row.get("ema50", np.nan)
    if pd.isna(adx_val) or pd.isna(ema20_val) or pd.isna(ema50_val):
        return "SIDEWAYS"
    if adx_val >= adx_threshold:
        if ema20_val > ema50_val:
            return "UP"
        elif ema20_val < ema50_val:
            return "DOWN"
    return "SIDEWAYS"

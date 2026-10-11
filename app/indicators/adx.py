import numpy as np
import pandas as pd

from app.indicators.atr import atr


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index: trend STRENGTH (not direction), 0-100.

    +DM / -DM : directional movement (today's high/low vs yesterday's)
    +DI / -DI : smoothed DM as a % of ATR
    DX        : 100 * |+DI - -DI| / (+DI + -DI)
    ADX       : Wilder-smoothed DX
    """
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    valid = up_move.notna()  # first row has no previous candle

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0).where(valid)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0).where(valid)

    alpha = 1 / period
    smooth_plus = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    atr_value = atr(df, period)

    plus_di = 100 * smooth_plus / atr_value
    minus_di = 100 * smooth_minus / atr_value

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_value = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_value})
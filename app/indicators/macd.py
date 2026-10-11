import pandas as pd

from app.indicators.ema import ema


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line = EMA(fast) - EMA(slow)
    Signal line = EMA of the MACD line
    Histogram   = MACD line - Signal line
    """
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": macd_line - signal_line,
    })
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing), range 0-100.

    gain/loss = size of up/down moves between consecutive closes
    RS  = average gain / average loss
    RSI = 100 - 100 / (1 + RS)
    """
    delta = close.diff()
    gain = delta.clip(lower=0)       # keep only up moves
    loss = -delta.clip(upper=0)      # keep only down moves (as positive numbers)

    # Wilder smoothing is an EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    result = 100 - 100 / (1 + avg_gain / avg_loss)
    result = result.where(avg_loss != 0, 100.0)                          # no losses -> 100
    result = result.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)    # no movement -> 50
    return result
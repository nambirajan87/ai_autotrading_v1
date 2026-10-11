import logging

import pandas as pd

logger = logging.getLogger(__name__)

IST = "Asia/Kolkata"
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
COLUMN_ALIASES = {"date": "timestamp", "datetime": "timestamp", "time": "timestamp"}


class DataValidationError(Exception):
    """Raised when market data is not safe to use."""


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn a raw DataFrame into the standard OHLCV shape."""
    df = raw.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.rename(columns=COLUMN_ALIASES)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Missing columns: {missing}")
    df = df[REQUIRED_COLUMNS]

    # Timestamps: always timezone-aware, always IST
    ts = pd.to_datetime(df["timestamp"])
    ts = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
    df["timestamp"] = ts

    # Numbers: bad text becomes NaN, which validation will then catch
    for col in PRICE_COLUMNS + ["volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.sort_values("timestamp")
        .drop_duplicates(subset="timestamp", keep="last")
        .reset_index(drop=True)
    )
    return df


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Raise DataValidationError listing every problem found."""
    if df.empty:
        raise DataValidationError("DataFrame is empty")

    problems = []

    nan_count = int(df[REQUIRED_COLUMNS].isna().sum().sum())
    if nan_count:
        problems.append(f"{nan_count} missing/non-numeric values")

    if (df[PRICE_COLUMNS] <= 0).any().any():
        problems.append("non-positive price found")

    bad_high = df["high"] < df[["open", "close", "low"]].max(axis=1)
    if bad_high.any():
        problems.append(f"{int(bad_high.sum())} candles where high is below open/close/low")

    bad_low = df["low"] > df[["open", "close", "high"]].min(axis=1)
    if bad_low.any():
        problems.append(f"{int(bad_low.sum())} candles where low is above open/close/high")

    if (df["volume"] < 0).any():
        problems.append("negative volume found")

    if df["timestamp"].duplicated().any():
        problems.append("duplicate timestamps")
    if not df["timestamp"].is_monotonic_increasing:
        problems.append("timestamps not in increasing order")

    if problems:
        raise DataValidationError("; ".join(problems))


def find_gaps(df: pd.DataFrame, candle_minutes: int = 5) -> pd.DataFrame:
    """Return rows where a candle is missing *within the same trading day*.
    Overnight/weekend gaps are normal and are ignored."""
    day = df["timestamp"].dt.date
    diff = df["timestamp"].diff()
    same_day = day == day.shift()
    gap_mask = same_day & (diff > pd.Timedelta(minutes=candle_minutes))
    return df.loc[gap_mask, ["timestamp"]].assign(gap=diff[gap_mask])


# Re-exports for convenient top-level access
try:
    from app.data.historical_data import load_csv
    from app.data.instrument_loader import (
        find_equity_by_isin,
        find_equity_by_symbol,
        load_nse_equities,
        search_equities,
        validate_isin,
    )
    from app.data.upstox_loader import fetch_range
except ImportError:
    pass
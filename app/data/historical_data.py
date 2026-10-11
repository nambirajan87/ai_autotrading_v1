import logging

import pandas as pd

from app.data.market_data import find_gaps, normalize_ohlcv, validate_ohlcv

logger = logging.getLogger(__name__)


def load_csv(path: str, candle_minutes: int = 5) -> pd.DataFrame:
    raw = pd.read_csv(path)
    df = normalize_ohlcv(raw)
    validate_ohlcv(df)

    gaps = find_gaps(df, candle_minutes)
    if len(gaps):
        logger.warning("%d intra-day gaps found in %s", len(gaps), path)

    logger.info("Loaded %d candles from %s (%s -> %s)",
                len(df), path, df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
    return df


if __name__ == "__main__":
    from app.monitoring.logging_setup import setup_logging

    setup_logging("INFO")
    data = load_csv("data/sample_RELIANCE_5m.csv")
    print(data.head())
    print(data.dtypes)
    print("shape:", data.shape)
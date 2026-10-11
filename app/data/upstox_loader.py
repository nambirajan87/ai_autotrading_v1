import logging
import os
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv

from app.data.market_data import normalize_ohlcv, validate_ohlcv

load_dotenv()
logger = logging.getLogger(__name__)

BASE = "https://api.upstox.com/v3/historical-candle"


def fetch_candles(instrument_key: str, unit: str, interval: int,
                  from_date: date, to_date: date) -> pd.DataFrame:
    """One API call. Keep the range within the limit for this unit/interval."""
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is not set in .env")

    url = (f"{BASE}/{quote(instrument_key, safe='')}/{unit}/{interval}/"
           f"{to_date.isoformat()}/{from_date.isoformat()}")
    resp = requests.get(url, headers={"Accept": "application/json",
                                      "Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code != 200:
        # never log the token; the body usually carries the error code
        raise RuntimeError(f"Upstox HTTP {resp.status_code}: {resp.text[:300]}")

    candles = resp.json()["data"]["candles"]
    raw = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low",
                                         "close", "volume", "oi"]).drop(columns="oi")
    return raw


def fetch_range(instrument_key: str, unit: str, interval: int,
                start: date, end: date, chunk_days: int = 28) -> pd.DataFrame:
    """Walks a long range in chunks, then normalizes and validates (Phase 2 gate)."""
    parts, cursor = [], start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        parts.append(fetch_candles(instrument_key, unit, interval, cursor, chunk_end))
        logger.info("fetched %s -> %s", cursor, chunk_end)
        cursor = chunk_end + timedelta(days=1)

    df = normalize_ohlcv(pd.concat(parts, ignore_index=True))
    validate_ohlcv(df)
    return df


if __name__ == "__main__":
    from app.monitoring.logging_setup import setup_logging

    setup_logging("INFO")
    # KEY = "PUT_REAL_INSTRUMENT_KEY_HERE"      # from Upstox's instrument list
    KEY = "NSE_EQ|INE002A01018"      # likely RELIANCE; verify with Option B
    df = fetch_range(KEY, "minutes", 5, date(2026, 9, 1), date(2026, 9, 30))
    print(df.head(), "\nshape:", df.shape)
    df.to_csv("data/upstox_5m.csv", index=False)
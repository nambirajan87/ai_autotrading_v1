import csv
import gzip
import io
import logging
import os
import time
import urllib.request
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
DEFAULT_CACHE_PATH = "data/nse_equities_cache.csv"
CACHE_TTL_SECONDS = 86400  # 24 hours


def validate_isin(isin: str) -> bool:
    """Validates an Indian ISIN (ISO 6166).
    - Exactly 12 characters, alphanumeric.
    - Must start with 'IN'.
    - Check digit verified using the modified Luhn algorithm.
    """
    if not isinstance(isin, str) or not isin.strip():
        return False
    isin = isin.strip().upper()
    # Direct Upstox instrument key support (e.g. NSE_FO|51330)
    if isin.startswith("NSE_FO|") and isin[7:].isdigit():
        return True
    if len(isin) != 12:
        return False
    if not isin.startswith("IN") or not isin.isalnum():
        return False

    digits = []
    for char in isin:
        if char.isdigit():
            digits.append(int(char))
        elif "A" <= char <= "Z":
            val = ord(char) - ord("A") + 10
            digits.append(val // 10)
            digits.append(val % 10)
        else:
            return False

    # Luhn test: from right to left, double every second digit
    total = 0
    reversed_digits = digits[::-1]
    for idx, digit in enumerate(reversed_digits):
        if idx % 2 == 1:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit

    return total % 10 == 0


DEFAULT_INDEX_EXPIRIES = {
    "NIFTY": [
        "2026-10-13",
        "2026-10-19",
        "2026-10-27",
        "2026-11-03",
        "2026-11-09",
        "2026-11-23",
        "2026-12-29",
        "2027-03-30",
        "2027-06-29",
        "2027-09-28",
    ],
    "BANKNIFTY": [
        "2026-10-27",
        "2026-11-23",
        "2026-12-29",
        "2027-03-30",
        "2027-06-29",
        "2027-09-28",
    ],
    "FINNIFTY": [
        "2026-10-27",
        "2026-11-23",
        "2026-12-29",
    ],
    "MIDCPNIFTY": [
        "2026-10-27",
        "2026-11-23",
        "2026-12-29",
    ],
}

INDEX_LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 75,
}


def parse_expiry_info(exp_str: str) -> tuple[str, str]:
    """
    Parses expiry string into:
    1. display_dt: human-readable date, e.g. '27-OCT-2026', '23-NOV-2026'
    2. sym_code: compact NSE FO symbol expiry code, e.g. '26OCT', '26NOV', '26O13'
    """
    if not exp_str:
        return "27-OCT-2026", "26OCT"
    from datetime import datetime
    exp_str_clean = str(exp_str).strip()
    dt = None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(exp_str_clean, fmt)
            break
        except Exception:
            pass
    if not dt:
        clean = exp_str_clean.replace("-", "").upper()
        return exp_str_clean, clean[:5] if len(clean) >= 5 else clean

    display_dt = dt.strftime("%d-%b-%Y").upper()
    yy = dt.strftime("%y")
    mmm = dt.strftime("%b").upper()
    month_codes = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "S", 10: "O", 11: "N", 12: "D"}
    # If date >= 23, treat as monthly contract code (e.g. 26OCT, 26NOV, 26DEC, 27MAR)
    if dt.day >= 23:
        sym_code = f"{yy}{mmm}"
    else:
        # Weekly contract format: e.g. 26S03, 26O13, 26O19
        m_code = month_codes.get(dt.month, str(dt.month))
        sym_code = f"{yy}{m_code}{dt.day:02d}"
    return display_dt, sym_code


NIFTY_STANDARD_INSTRUMENTS = [
    {
        "instrument_key": "NSE_EQ|INF204KB14I2",
        "tradingsymbol": "NIFTY",
        "name": "Nifty 50 Index (NIP IND ETF NIFTY BEES)",
        "isin": "INF204KB14I2",
        "tick_size": 0.05,
        "lot_size": 1,
    },
    {
        "instrument_key": "NSE_EQ|INF204KB14I2",
        "tradingsymbol": "NIFTY 50",
        "name": "Nifty 50 Benchmark Index (NIP IND ETF NIFTY BEES)",
        "isin": "INF204KB14I2",
        "tick_size": 0.05,
        "lot_size": 1,
    },
    {
        "instrument_key": "NSE_EQ|INF200KA1FS1",
        "tradingsymbol": "NIFTY50",
        "name": "Nifty 50 ETF (SBI-ETF NIFTY 50)",
        "isin": "INF200KA1FS1",
        "tick_size": 0.05,
        "lot_size": 1,
    },
    {
        "instrument_key": "NSE_EQ|INF204KB14I2",
        "tradingsymbol": "NIFTYBEES",
        "name": "NIP IND ETF NIFTY BEES",
        "isin": "INF204KB14I2",
        "tick_size": 0.05,
        "lot_size": 1,
    },
    {
        "instrument_key": "NSE_FO|51330",
        "tradingsymbol": "NIFTY26OCT22500CE",
        "name": "NIFTY 27-OCT-2026 22500 CE",
        "isin": "INF000513301",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|51331",
        "tradingsymbol": "NIFTY26OCT22500PE",
        "name": "NIFTY 27-OCT-2026 22500 PE",
        "isin": "INF000513319",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|44632",
        "tradingsymbol": "NIFTY26O1323000CE",
        "name": "NIFTY 13-OCT-2026 23000 CE",
        "isin": "INF000446320",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|44599",
        "tradingsymbol": "NIFTY26O1322300PE",
        "name": "NIFTY 13-OCT-2026 22300 PE",
        "isin": "INF000445991",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|51372",
        "tradingsymbol": "NIFTY26OCT23500CE",
        "name": "NIFTY 27-OCT-2026 23500 CE",
        "isin": "INF000513723",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|51352",
        "tradingsymbol": "NIFTY26OCT23000CE",
        "name": "NIFTY 27-OCT-2026 23000 CE",
        "isin": "INF000513525",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|51440",
        "tradingsymbol": "NIFTY26OCT25000CE",
        "name": "NIFTY 27-OCT-2026 25000 CE",
        "isin": "INF000514408",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_FO|51441",
        "tradingsymbol": "NIFTY26OCT25000PE",
        "name": "NIFTY 27-OCT-2026 25000 PE",
        "isin": "INF000514416",
        "tick_size": 0.05,
        "lot_size": 65,
    },
    {
        "instrument_key": "NSE_EQ|INF174KA1ZB7",
        "tradingsymbol": "BANKNIFTY",
        "name": "Nifty Bank Index (Kotak Bank Nifty ETF)",
        "isin": "INF174KA1ZB7",
        "tick_size": 0.05,
        "lot_size": 1,
    },
]


def ensure_nifty_instruments(df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Ensures primary NIFTY and Bank Nifty benchmark options are included."""
    nifty_df = pd.DataFrame(NIFTY_STANDARD_INSTRUMENTS)
    if df is None or df.empty:
        return nifty_df
    combined = pd.concat([nifty_df, df], ignore_index=True)
    combined.drop_duplicates(subset=["tradingsymbol"], keep="first", inplace=True)
    return combined


def load_nse_equities(cache_path: str = DEFAULT_CACHE_PATH,
                      force_reload: bool = False) -> pd.DataFrame:
    """Loads NSE_EQ equities from Upstox's complete instruments file.
    Caches the parsed result locally and re-downloads at most once per 24 hours.
    """
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)

    # Check local cache
    if not force_reload and os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        if time.time() - mtime < CACHE_TTL_SECONDS:
            try:
                df = pd.read_csv(cache_path)
                if not df.empty and "isin" in df.columns:
                    logger.info("Loaded %d NSE equities from cache (%s)", len(df), cache_path)
                    return ensure_nifty_instruments(df)
            except Exception as e:
                logger.warning("Failed to read cache %s: %s; re-downloading", cache_path, e)

    # Download and parse
    logger.info("Downloading instruments from Upstox: %s", UPSTOX_INSTRUMENTS_URL)
    req = urllib.request.Request(UPSTOX_INSTRUMENTS_URL, headers={"User-Agent": "ai_autotrading/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            reader = csv.DictReader(io.TextIOWrapper(gz, encoding="utf-8"))
            rows = []
            for item in reader:
                if item.get("exchange") == "NSE_EQ" and item.get("instrument_type") == "EQUITY":
                    inst_key = item.get("instrument_key", "")
                    isin = inst_key.split("|")[1] if "|" in inst_key else ""
                    rows.append({
                        "instrument_key": inst_key,
                        "tradingsymbol": item.get("tradingsymbol", ""),
                        "name": item.get("name", ""),
                        "isin": isin,
                        "tick_size": float(item.get("tick_size") or 0.05),
                        "lot_size": int(item.get("lot_size") or 1),
                    })

    df = pd.DataFrame(rows)
    df = ensure_nifty_instruments(df)
    df.sort_values(by="tradingsymbol", inplace=True)
    df.to_csv(cache_path, index=False)
    logger.info("Cached %d NSE equities to %s", len(df), cache_path)
    return df


def find_equity_by_isin(isin: str, df: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """Finds an equity or instrument by its ISIN, instrument key, or token."""
    if df is None:
        df = load_nse_equities()
    else:
        df = ensure_nifty_instruments(df)
    isin = isin.strip().upper()
    match = df[(df["isin"].str.upper() == isin) | (df["instrument_key"].str.upper() == isin)]
    if not match.empty:
        return match.iloc[0].to_dict()
    # Support token matching e.g. "51330" matching "NSE_FO|51330"
    match_token = df[df["instrument_key"].str.upper().str.endswith(f"|{isin}", na=False)]
    if not match_token.empty:
        return match_token.iloc[0].to_dict()
    return None


def find_equity_by_symbol(symbol: str, df: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """Finds an equity by exact trading symbol."""
    if df is None:
        df = load_nse_equities()
    else:
        df = ensure_nifty_instruments(df)
    symbol = symbol.strip().upper()
    match = df[df["tradingsymbol"] == symbol]
    if not match.empty:
        return match.iloc[0].to_dict()
    return None


def search_equities(query: str, df: Optional[pd.DataFrame] = None, limit: int = 50) -> pd.DataFrame:
    """Searches equities by symbol or name with relevance ranking."""
    if df is None:
        df = load_nse_equities()
    else:
        df = ensure_nifty_instruments(df)
    if not query:
        return df.head(limit)
    q = query.strip().upper()
    mask = (df["tradingsymbol"].str.upper().str.contains(q, na=False) |
            df["name"].str.upper().str.contains(q, na=False) |
            df["isin"].str.upper().str.contains(q, na=False) |
            df["instrument_key"].str.upper().str.contains(q, na=False))
    matched = df[mask].copy()
    if matched.empty:
        return matched

    def _relevance(row):
        sym = str(row["tradingsymbol"]).upper()
        name = str(row["name"]).upper()
        isin = str(row["isin"]).upper()
        inst_key = str(row.get("instrument_key", "")).upper()
        # 1. Exact match on symbol, ISIN, or instrument key
        if sym == q or isin == q or inst_key == q or inst_key.endswith(f"|{q}"):
            return 100
        # 2. Key Nifty benchmark and option choices when searching for Nifty
        if q in ("NIFTY", "NIFTY 50", "NIFTY50", "NIFTY BEES", "NIFTYBEES"):
            if sym == "NIFTY":
                return 100
            if sym in ("NIFTY 50", "NIFTY50", "NIFTYBEES"):
                return 98
            if any(opt in sym for opt in ("22500", "23000", "22300", "25000")):
                return 96
            if sym in ("SETFNIF50", "NIFTY1", "HDFCNIFTY", "ICICINIFTY", "NIFTYETF"):
                return 95
            if "NIFTY" in sym and ("50" in sym or "50" in name or "BEES" in sym):
                return 92
        # 3. Direct strike or token searches
        if q in ("22500", "22500CE", "22500PE", "51330", "51331", "23000", "23000CE", "44632", "22300", "22300PE", "44599", "25000", "25000CE", "25000PE", "51440", "51441", "CE", "PE"):
            if any(k in sym for k in ("22500", "23000", "22300", "25000")):
                return 97
            if inst_key.endswith(f"|{q}"):
                return 97
        # 4. Exact prefix matches
        if sym.startswith(q):
            return 90
        if name.startswith(q):
            return 80
        if q in sym:
            return 70
        if q in name:
            return 60
        if q in isin or q in inst_key:
            return 50
        return 10

    matched["_rank"] = matched.apply(_relevance, axis=1)
    matched.sort_values(by=["_rank", "tradingsymbol"], ascending=[False, True], inplace=True)
    matched.drop(columns=["_rank"], inplace=True)
    return matched.head(limit)

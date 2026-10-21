import logging
import math
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from scipy.stats import norm

from app.data.instrument_loader import parse_expiry_info

logger = logging.getLogger(__name__)

WEEKDAY_ADJUSTMENTS = {
    "Wednesday": 350,
    "Thursday": 250,
    "Friday": 150,
    "Monday": 0,
    "Tuesday": -100,
}


def calculate_oipulse_strike_and_type(
    ltp: float,
    current_day: str,
    oi_strength: float,
    underlying: str = "NIFTY",
) -> Tuple[Optional[int], str]:
    """
    Implements OIPulse Strike and Option Type Resolution:
    1. Calculate lower bound based on underlying strike step:
       - NIFTY / FINNIFTY: 50 points -> ((int(LTP) - 1) // 50) * 50 + 1
       - BANKNIFTY: 100 points -> ((int(LTP) - 1) // 100) * 100 + 1
       - MIDCPNIFTY: 25 points -> ((int(LTP) - 1) // 25) * 25 + 1
    2. Adjust based on weekday:
       Wednesday: +350, Thursday: +250, Friday: +150, Monday: 0, Tuesday: -100
    3. Signal based on OIStrength:
       > 50  -> CE: strike = previous_bound + adjustment - 1
       < -50 -> PE: strike = previous_bound - adjustment - 1
       otherwise -> No Signal
    """
    if ltp is None or oi_strength is None:
        return None, "No Signal"

    day_clean = current_day.strip().capitalize()
    if day_clean not in WEEKDAY_ADJUSTMENTS:
        logger.debug("Day '%s' not in Monday-Friday adjustments", current_day)
        return None, "No Signal"

    # Step 1: Calculate lower bound
    und = underlying.strip().upper()
    step = 100 if und == "BANKNIFTY" else (25 if und == "MIDCPNIFTY" else 50)
    previous_bound = ((int(float(ltp)) - 1) // step) * step + 1

    # Step 2 & 3: Determine optionTypeResult
    if oi_strength > 50:
        option_type = "CE"
    elif oi_strength < -50:
        option_type = "PE"
    else:
        option_type = "No Signal"

    # Step 4: Calculate strike
    adjustment = WEEKDAY_ADJUSTMENTS[day_clean]
    if option_type == "CE":
        strike = int(previous_bound + adjustment - 1)
    elif option_type == "PE":
        strike = int(previous_bound - adjustment - 1)
    else:
        strike = None

    return strike, option_type


def resolve_nearest_expiry(
    candle_date: date,
    underlying: str = "NIFTY",
    available_expiries: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Resolves the nearest upcoming weekly Tuesday expiry for a given date.
    Returns (display_dt, sym_code), e.g. ("01-SEP-2026", "26S01").
    Every week Tuesday is expiry day:
    - September 2026: 01-Sep, 08-Sep, 15-Sep, 22-Sep, 29-Sep
    """
    _, info = resolve_nearest_expiry_date(
        candle_date, underlying=underlying, available_expiries=available_expiries
    )
    return info


def resolve_nearest_expiry_date(
    candle_date: date,
    underlying: str = "NIFTY",
    available_expiries: Optional[List[str]] = None,
) -> Tuple[date, Tuple[str, str]]:
    """
    Resolves the nearest upcoming weekly Tuesday expiry for a given date.
    Returns (expiry_date, (display_dt, sym_code)).
    """
    # 1. If list of available expiries provided, search for earliest >= candle_date
    if available_expiries:
        valid_expiries = []
        for exp in available_expiries:
            exp_clean = str(exp).strip()
            exp_dt = None
            for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y"):
                try:
                    exp_dt = datetime.strptime(exp_clean, fmt).date()
                    break
                except Exception:
                    pass
            if exp_dt and exp_dt >= candle_date:
                valid_expiries.append((exp_dt, exp_clean))

        if valid_expiries:
            valid_expiries.sort(key=lambda x: x[0])
            best_dt, best_str = valid_expiries[0]
            return best_dt, parse_expiry_info(best_str)

    # 2. Weekly Tuesday expiry calculation:
    # Every week Tuesday (weekday 1) is expiry day.
    # Mon(0), Tue(1) -> this week Tuesday (days_ahead = 1 - weekday)
    # Wed(2), Thu(3), Fri(4), Sat(5), Sun(6) -> next week Tuesday (days_ahead = 8 - weekday)
    weekday = candle_date.weekday()
    if weekday <= 1:
        days_ahead = 1 - weekday
    else:
        days_ahead = 8 - weekday

    exp_dt = candle_date + timedelta(days=days_ahead)
    exp_str = exp_dt.strftime("%d-%b-%Y").upper()
    return exp_dt, parse_expiry_info(exp_str)


def validate_oipulse_contract_symbol(
    symbol: str, underlying: str = "NIFTY"
) -> Tuple[bool, str]:
    """
    Validates dynamically constructed contract symbol:
    - Symbol string is non-empty
    - Starts with underlying (e.g. NIFTY, BANKNIFTY)
    - Ends with CE or PE
    - Strike price is positive integer and matches strike step
    - Expiry code is formatted properly
    """
    if not symbol or not isinstance(symbol, str):
        return False, "Empty or invalid contract symbol"

    und = underlying.strip().upper()
    sym = symbol.strip().upper()

    if not sym.startswith(und):
        return False, f"Symbol '{sym}' does not start with underlying '{und}'"

    if not (sym.endswith("CE") or sym.endswith("PE")):
        return False, f"Symbol '{sym}' must end with 'CE' or 'PE'"

    rem = sym[len(und):-2]  # e.g., '26S0124000' or '26SEP22900'

    match = re.search(r"(\d{4,6})$", rem)
    if not match:
        return False, f"Unable to extract strike price from symbol '{sym}'"

    strike_str = match.group(1)
    strike = int(strike_str)
    step = 100 if und == "BANKNIFTY" else (25 if und == "MIDCPNIFTY" else 50)
    if strike % step != 0:
        return False, f"Strike {strike} is not a valid multiple of {step} for {und}"

    expiry_code = rem[:-len(strike_str)]
    if len(expiry_code) < 4:
        return False, f"Invalid expiry code '{expiry_code}' in symbol '{sym}'"

    return True, f"Valid contract symbol: {sym}"


def calculate_option_price(
    underlying_price: float,
    strike_price: float,
    option_type: str,
    candle_dt: datetime,
    expiry_dt: date,
    iv: float = 0.14,
    r: float = 0.07,
) -> float:
    """
    Calculates realistic option premium using Black-Scholes formula:
    - S: underlying LTP
    - K: strike price
    - T: time to expiry in years (targeting 15:30 on expiry date)
    - iv: implied volatility (default 14% for NIFTY)
    - r: risk-free rate (default 7%)
    Returns option premium in standard range (e.g. ₹70 to ₹135).
    """
    if underlying_price is None or strike_price is None or strike_price <= 0:
        return 50.0

    exp_datetime = datetime.combine(expiry_dt, datetime.min.time()).replace(hour=15, minute=30)
    time_diff = (exp_datetime - candle_dt).total_seconds()
    # Floor T at 1 minute to avoid negative or zero division near/after 15:30 on expiry day
    T = max(time_diff / (365.25 * 86400), 1 / (365.25 * 1440))
    S = float(underlying_price)
    K = float(strike_price)

    try:
        d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
        if option_type == "CE":
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return round(max(price, 0.5), 2)
    except Exception as e:
        logger.debug("BSM option pricing calculation error: %s", e)
        return round(max(abs(S - K), 0.5), 2)


def fetch_option_market_candles(
    contract_symbol: str,
    start_date: Any,
    end_date: Any,
    timeframe_minutes: int = 1,
    gateway: Optional[Any] = None,
) -> Optional[pd.DataFrame]:
    """
    Retrieves market historical data / option chain candles for the contract:
    1. Check local cache (data/options/{symbol}.csv)
    2. Check broker gateway (Kotak Neo historical candles)
    Returns DataFrame if found, else None.
    """
    # 1. Local options cache check
    local_path = os.path.join("data", "options", f"{contract_symbol}.csv")
    if os.path.exists(local_path):
        try:
            df = pd.read_csv(local_path)
            if not df.empty and "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df
        except Exception as e:
            logger.debug("Error loading local option CSV %s: %s", local_path, e)

    # 2. Broker Gateway check
    if gateway and hasattr(gateway, "fetch_historical_candles"):
        try:
            df_g = gateway.fetch_historical_candles(
                symbol=contract_symbol,
                instrument_key=contract_symbol,
                timeframe_minutes=timeframe_minutes,
                start_date=start_date,
                end_date=end_date,
            )
            if df_g is not None and not df_g.empty:
                return df_g
        except Exception as e:
            logger.debug("Gateway historical candles for %s note: %s", contract_symbol, e)

    return None


def parse_strength_value(val) -> Optional[float]:
    """Parses raw strength string like '-109%', '+8%', '55%' to float."""
    if val is None:
        return None
    val_str = str(val).replace("%", "").replace("+", "").strip()
    try:
        return float(val_str)
    except Exception:
        return None


def get_oipulse_record_from_db(
    candle_dt: datetime, db_path: str = "data/backtest.db"
) -> Tuple[Optional[float], Optional[float]]:
    """
    Queries oi_strength table in SQLite DB for matching Date and Time.
    Date in DB is formatted as DD-MM-YYYY (e.g. 01-09-2026).
    Time in DB is formatted as HH:MM:SS (e.g. 13:21:00).
    Returns (ltp, oi_strength).
    """
    if not os.path.exists(db_path):
        return None, None

    date_str = candle_dt.strftime("%d-%m-%Y")
    time_str = candle_dt.strftime("%H:%M:%S")

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Check table existence
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oi_strength'")
        if not cur.fetchone():
            conn.close()
            return None, None

        # 1. Exact match on Date and Time
        cur.execute(
            "SELECT LTP, Strength FROM oi_strength WHERE Date = ? AND Time = ? LIMIT 1",
            (date_str, time_str),
        )
        row = cur.fetchone()

        # 2. Fallback to latest reading <= candle time on that date
        if not row:
            cur.execute(
                "SELECT LTP, Strength FROM oi_strength WHERE Date = ? AND Time <= ? ORDER BY Time DESC LIMIT 1",
                (date_str, time_str),
            )
            row = cur.fetchone()

        # 3. Fallback to first reading on that date
        if not row:
            cur.execute(
                "SELECT LTP, Strength FROM oi_strength WHERE Date = ? ORDER BY Time ASC LIMIT 1",
                (date_str,),
            )
            row = cur.fetchone()

        conn.close()

        if row:
            ltp = float(row[0]) if row[0] is not None else None
            oi_strength = parse_strength_value(row[1])
            return ltp, oi_strength

    except Exception as e:
        logger.debug("Error reading oi_strength from DB: %s", e)

    return None, None


def resolve_oipulse_option(
    underlying: str,
    candle_dt: datetime,
    db_path: str = "data/backtest.db",
    available_expiries: Optional[List[str]] = None,
    live_ltp: Optional[float] = None,
    live_strength: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Comprehensive dynamic resolver for OIPulse Mode:
    - Fetches LTP and OI Strength (from DB or live parameters)
    - Resolves optionTypeResult (CE/PE/No Signal) and strikePriceResult
    - Resolves nearest Tuesday expiry and formatted NSE symbol code
    - Validates dynamically constructed contract symbol
    - Builds the active contract tradingsymbol (e.g. NIFTY26S0124000CE)
    """
    und = underlying.strip().upper()

    # Get LTP and Strength
    ltp = live_ltp
    oi_strength = live_strength
    if ltp is None or oi_strength is None:
        db_ltp, db_strength = get_oipulse_record_from_db(candle_dt, db_path=db_path)
        if ltp is None:
            ltp = db_ltp
        if oi_strength is None:
            oi_strength = db_strength

    current_day = candle_dt.strftime("%A")
    strike, option_type = calculate_oipulse_strike_and_type(ltp, current_day, oi_strength, underlying=und)
    expiry_date, (expiry_display, expiry_code) = resolve_nearest_expiry_date(
        candle_dt.date(), underlying=und, available_expiries=available_expiries
    )

    if strike is not None and option_type in ("CE", "PE"):
        candidate_symbol = f"{und}{expiry_code}{strike}{option_type}"
        is_valid, reason = validate_oipulse_contract_symbol(candidate_symbol, underlying=und)
        if is_valid:
            resolved_symbol = candidate_symbol
            has_signal = True
            signal = "LONG" if option_type == "CE" else "SHORT"
        else:
            resolved_symbol = und
            has_signal = False
            signal = "HOLD"
    else:
        candidate_symbol = und
        resolved_symbol = und
        has_signal = False
        signal = "HOLD"
        is_valid = True
        reason = "Neutral/No Signal"

    return {
        "underlying": und,
        "candle_dt": candle_dt,
        "current_day": current_day,
        "ltp": ltp,
        "oi_strength": oi_strength,
        "strike": strike,
        "option_type": option_type,
        "expiry_date": expiry_date,
        "expiry_display": expiry_display,
        "expiry_code": expiry_code,
        "resolved_symbol": resolved_symbol,
        "candidate_symbol": candidate_symbol,
        "is_valid_symbol": is_valid,
        "validation_reason": reason,
        "has_signal": has_signal,
        "signal": signal,
    }

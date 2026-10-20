import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.data.instrument_loader import parse_expiry_info

logger = logging.getLogger(__name__)

WEEKDAY_ADJUSTMENTS = {
    "Wednesday": 300,
    "Thursday": 200,
    "Friday": 100,
    "Monday": 0,
    "Tuesday": -100,
}


def calculate_oipulse_strike_and_type(
    ltp: float, current_day: str, oi_strength: float
) -> Tuple[Optional[int], str]:
    """
    Implements OIPulse Strike and Option Type Resolution:
    1. Calculate 50-point lower bound: ((int(LTP) - 1) // 50) * 50 + 1
    2. Adjust based on weekday:
       Wednesday: +300, Thursday: +200, Friday: +100, Monday: 0, Tuesday: -100
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

    # Step 1: Calculate 50-point lower bound
    previous_bound = ((int(float(ltp)) - 1) // 50) * 50 + 1

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
    Resolves the nearest upcoming expiry for a given date.
    Returns (display_dt, sym_code), e.g. ("03-SEP-2026", "26S03").
    Uses market standard weekly format (S for September, O for October, N for November, D for December).
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
            return parse_expiry_info(best_str)

    # 2. Historical fallback or default calculation:
    # In NSE, standard weekly expiry is Thursday (weekday 3).
    # If candle is Mon(0), Tue(1), Wed(2), Thu(3) -> this week Thursday
    # If candle is Fri(4), Sat(5), Sun(6) -> next week Thursday
    weekday = candle_date.weekday()
    if weekday <= 3:
        days_ahead = 3 - weekday
    else:
        days_ahead = 10 - weekday

    exp_dt = candle_date + timedelta(days=days_ahead)
    exp_str = exp_dt.strftime("%d-%b-%Y").upper()
    return parse_expiry_info(exp_str)


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
) -> Dict:
    """
    Comprehensive dynamic resolver for OIPulse Mode:
    - Fetches LTP and OI Strength (from DB or live parameters)
    - Resolves optionTypeResult (CE/PE/No Signal) and strikePriceResult
    - Resolves nearest expiry and formatted NSE symbol code
    - Builds the active contract tradingsymbol (e.g. NIFTY26S0324050PE)
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
    strike, option_type = calculate_oipulse_strike_and_type(ltp, current_day, oi_strength)
    expiry_display, expiry_code = resolve_nearest_expiry(
        candle_dt.date(), underlying=und, available_expiries=available_expiries
    )

    if strike is not None and option_type in ("CE", "PE"):
        resolved_symbol = f"{und}{expiry_code}{strike}{option_type}"
        has_signal = True
        signal = "LONG" if option_type == "CE" else "SHORT"
    else:
        resolved_symbol = und
        has_signal = False
        signal = "HOLD"

    return {
        "underlying": und,
        "candle_dt": candle_dt,
        "current_day": current_day,
        "ltp": ltp,
        "oi_strength": oi_strength,
        "strike": strike,
        "option_type": option_type,
        "expiry_display": expiry_display,
        "expiry_code": expiry_code,
        "resolved_symbol": resolved_symbol,
        "has_signal": has_signal,
        "signal": signal,
    }

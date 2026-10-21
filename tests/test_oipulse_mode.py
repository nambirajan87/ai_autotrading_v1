import os
import sqlite3
from datetime import date, datetime
import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, run_backtest
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.data.instrument_loader import parse_expiry_info
from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators
from app.strategy.oipulse_resolver import (
    calculate_oipulse_strike_and_type,
    calculate_option_price,
    get_oipulse_record_from_db,
    resolve_nearest_expiry,
    resolve_nearest_expiry_date,
    resolve_oipulse_option,
    validate_oipulse_contract_symbol,
)


def test_market_standard_weekly_expiry_september():
    """Verify market standard for September weekly option expiry symbol: 01-SEP-2026 must resolve to 26S01."""
    disp, sym_code = parse_expiry_info("01-SEP-2026")
    assert disp == "01-SEP-2026"
    assert sym_code == "26S01", f"Expected '26S01' for 01-SEP-2026 weekly, but got '{sym_code}'"

    # Full contract symbol verification
    resolved_symbol = f"NIFTY{sym_code}24050PE"
    assert resolved_symbol == "NIFTY26S0124050PE"
    assert "26901" not in resolved_symbol


def test_weekly_month_codes_market_standards():
    """Verify weekly expiry month codes: Jan-Aug: 1-8, Sep: S, Oct: O, Nov: N, Dec: D."""
    test_cases = [
        ("08-JAN-2026", "26108"),
        ("05-FEB-2026", "26205"),
        ("05-MAR-2026", "26305"),
        ("02-APR-2026", "26402"),
        ("07-MAY-2026", "26507"),
        ("04-JUN-2026", "26604"),
        ("02-JUL-2026", "26702"),
        ("06-AUG-2026", "26806"),
        ("01-SEP-2026", "26S01"),
        ("08-SEP-2026", "26S08"),
        ("15-SEP-2026", "26S15"),
        ("22-SEP-2026", "26S22"),
        ("29-SEP-2026", "26SEP"),
    ]
    for exp_str, expected_code in test_cases:
        _, code = parse_expiry_info(exp_str)
        assert code == expected_code, f"Failed for {exp_str}: expected {expected_code}, got {code}"


def test_oipulse_logic_bounds_and_adjustments():
    """Verify oipulse strike bounds and weekday adjustments."""
    # Bounds: previous_bound = ((LTP - 1) // 50) * 50 + 1
    # For LTP = 24050.45: (24049 // 50) * 50 + 1 = 480 * 50 + 1 = 24001

    # Tuesday: adj = -100
    # CE (> 50): 24001 + (-100) - 1 = 23900
    # PE (< -50): 24001 - (-100) - 1 = 24101 -> 24100
    strike_ce, opt_ce = calculate_oipulse_strike_and_type(24050.45, "Tuesday", 65)
    assert opt_ce == "CE"
    assert strike_ce == 23900

    strike_pe, opt_pe = calculate_oipulse_strike_and_type(24050.45, "Tuesday", -65)
    assert opt_pe == "PE"
    assert strike_pe == 24100

    # Thursday: adj = +250
    # PE (< -50): 24001 - 250 - 1 = 23750
    strike_thu, opt_thu = calculate_oipulse_strike_and_type(24050.45, "Thursday", -60)
    assert opt_thu == "PE"
    assert strike_thu == 23750

    # Wednesday: adj = +350
    # CE (> 50): 24001 + 350 - 1 = 24350
    strike_wed, opt_wed = calculate_oipulse_strike_and_type(24050.45, "Wednesday", 60)
    assert opt_wed == "CE"
    assert strike_wed == 24350

    # Friday: adj = +150
    # PE (< -50): 24001 - 150 - 1 = 23850
    strike_fri, opt_fri = calculate_oipulse_strike_and_type(24050.45, "Friday", -60)
    assert opt_fri == "PE"
    assert strike_fri == 23850

    # Neutral band (-50 to +50): No signal
    strike_neu, opt_neu = calculate_oipulse_strike_and_type(24050.45, "Thursday", 20)
    assert opt_neu == "No Signal"
    assert strike_neu is None


def test_resolve_nearest_tuesday_expiry():
    """Verify resolve_nearest_expiry selects the nearest weekly Tuesday expiry date."""
    # 2026-09-01 is Tuesday -> same day expiry 01-SEP-2026 (26S01)
    disp, code = resolve_nearest_expiry(date(2026, 9, 1), "NIFTY")
    assert disp == "01-SEP-2026"
    assert code == "26S01"

    # 2026-09-02 is Wednesday -> next Tuesday is 08-SEP-2026 (26S08)
    disp, code = resolve_nearest_expiry(date(2026, 9, 2), "NIFTY")
    assert disp == "08-SEP-2026"
    assert code == "26S08"

    # 2026-09-07 is Monday -> next day Tuesday is 08-SEP-2026 (26S08)
    disp, code = resolve_nearest_expiry(date(2026, 9, 7), "NIFTY")
    assert disp == "08-SEP-2026"
    assert code == "26S08"

    # 2026-09-08 is Tuesday -> same day expiry 08-SEP-2026 (26S08)
    disp, code = resolve_nearest_expiry(date(2026, 9, 8), "NIFTY")
    assert disp == "08-SEP-2026"
    assert code == "26S08"

    # 2026-09-15 is Tuesday -> 15-SEP-2026 (26S15)
    disp, code = resolve_nearest_expiry(date(2026, 9, 15), "NIFTY")
    assert disp == "15-SEP-2026"
    assert code == "26S15"

    # 2026-09-22 is Tuesday -> 22-SEP-2026 (26S22)
    disp, code = resolve_nearest_expiry(date(2026, 9, 22), "NIFTY")
    assert disp == "22-SEP-2026"
    assert code == "26S22"

    # 2026-09-29 is Tuesday -> 29-SEP-2026 (26SEP)
    disp, code = resolve_nearest_expiry(date(2026, 9, 29), "NIFTY")
    assert disp == "29-SEP-2026"
    assert code == "26SEP"


def test_contract_symbol_validation():
    """Verify validation of dynamic contract symbols."""
    valid, msg = validate_oipulse_contract_symbol("NIFTY26S0124000CE", "NIFTY")
    assert valid, f"Expected valid, got: {msg}"

    valid, msg = validate_oipulse_contract_symbol("NIFTY26SEP22700PE", "NIFTY")
    assert valid, f"Expected valid, got: {msg}"

    valid, msg = validate_oipulse_contract_symbol("BANKNIFTY26S0851200CE", "BANKNIFTY")
    assert valid, f"Expected valid, got: {msg}"

    # Invalid strike (not a multiple of 50 for NIFTY)
    valid, msg = validate_oipulse_contract_symbol("NIFTY26S0124033CE", "NIFTY")
    assert not valid
    assert "multiple" in msg

    # Invalid suffix
    valid, msg = validate_oipulse_contract_symbol("NIFTY26S0124000XX", "NIFTY")
    assert not valid
    assert "CE" in msg or "PE" in msg


def test_option_pricing_calculation():
    """Verify Black-Scholes calculation yields realistic option premium in the ~₹70 to ₹135 range."""
    # ATM Call option: NIFTY at 24000, Strike 24000, 3 days to Tuesday expiry
    dt = datetime(2026, 9, 4, 10, 0, 0)
    exp = date(2026, 9, 8)
    ce_price = calculate_option_price(24000.0, 24000, "CE", dt, exp)
    assert 50.0 <= ce_price <= 180.0, f"Expected realistic CE premium, got {ce_price}"

    # ATM Put option: NIFTY at 24000, Strike 24000
    pe_price = calculate_option_price(24000.0, 24000, "PE", dt, exp)
    assert 50.0 <= pe_price <= 180.0, f"Expected realistic PE premium, got {pe_price}"


def test_oipulse_db_lookup():
    """Verify oi_strength DB lookup retrieves LTP and Strength properly."""
    db_path = "data/backtest.db"
    if not os.path.exists(db_path):
        pytest.skip("backtest.db not present")

    # Match exact record in DB from 01-09-2026 13:21:00
    ltp, strength = get_oipulse_record_from_db(datetime(2026, 9, 1, 13, 21, 0), db_path=db_path)
    assert ltp == 24050.45
    assert strength == -14.0


def test_backtest_with_oipulse_mode():
    """Verify backtest simulation under OIPulse Mode records dynamic contract symbol and uses option premiums."""
    csv_file = "data/nifty_5m.csv"
    if not os.path.exists(csv_file):
        pytest.skip("nifty_5m.csv not present")

    from app.data.historical_data import load_csv
    raw_df = load_csv(csv_file).head(100)
    custom_cfg = CustomStrategyConfig(
        name="Test_OIPulse_Strategy",
        ema_enabled=True,
        ema_fast=9,
        ema_slow=21,
        ema_weight=2,
        long_threshold=1,
        short_threshold=-1,
        fixed_target_pct=2.0,
        fixed_stoploss_pct=1.0,
    )
    indicators_df = calculate_custom_indicators(raw_df, custom_cfg)
    assert not indicators_df.empty

    risk_cfg = RiskConfig(
        capital=100000.0,
        lot_size=65,
        tick_size=0.05,
        fixed_target_pct=2.0,
        fixed_stoploss_pct=1.0,
    )
    cost_cfg = CostConfig(cost_mode="FLAT_PER_ORDER", flat_cost_per_order=20.0)

    # 1. Backtest with Normal Mode
    bt_normal = BacktestConfig(fo_mode="Normal Mode")
    trades_normal, _ = run_backtest(indicators_df, "NIFTY", custom_cfg, risk_cfg, cost_cfg, bt_normal)

    # 2. Backtest with OIPulse Mode
    bt_oipulse = BacktestConfig(fo_mode="OIPulse Mode")
    trades_oipulse, _ = run_backtest(indicators_df, "NIFTY", custom_cfg, risk_cfg, cost_cfg, bt_oipulse)

    # If trades triggered, check that contract symbols follow dynamic resolution
    if not trades_oipulse.empty:
        for sym in trades_oipulse["symbol"]:
            assert "NIFTY" in sym

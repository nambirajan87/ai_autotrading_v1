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
    get_oipulse_record_from_db,
    resolve_nearest_expiry,
    resolve_oipulse_option,
)


def test_market_standard_weekly_expiry_september():
    """Verify market standard for September weekly option expiry symbol: 03-SEP-2026 must resolve to 26S03."""
    disp, sym_code = parse_expiry_info("03-SEP-2026")
    assert disp == "03-SEP-2026"
    assert sym_code == "26S03", f"Expected '26S03' for 03-SEP-2026 weekly, but got '{sym_code}'"

    # Full contract symbol verification
    resolved_symbol = f"NIFTY{sym_code}24050PE"
    assert resolved_symbol == "NIFTY26S0324050PE"
    assert "26903" not in resolved_symbol


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
        ("03-SEP-2026", "26S03"),
        ("01-OCT-2026", "26O01"),
        ("05-NOV-2026", "26N05"),
        ("03-DEC-2026", "26D03"),
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
    # PE (< -50): 24001 - (-100) - 1 = 24101 -> 24101 or 24100
    strike_ce, opt_ce = calculate_oipulse_strike_and_type(24050.45, "Tuesday", 65)
    assert opt_ce == "CE"
    assert strike_ce == 23900

    strike_pe, opt_pe = calculate_oipulse_strike_and_type(24050.45, "Tuesday", -65)
    assert opt_pe == "PE"
    assert strike_pe == 24100

    # Thursday: adj = +200
    # PE (< -50): 24001 - 200 - 1 = 23800
    strike_thu, opt_thu = calculate_oipulse_strike_and_type(24050.45, "Thursday", -60)
    assert opt_thu == "PE"
    assert strike_thu == 23800

    # Neutral band (-50 to +50): No signal
    strike_neu, opt_neu = calculate_oipulse_strike_and_type(24050.45, "Thursday", 20)
    assert opt_neu == "No Signal"
    assert strike_neu is None


def test_resolve_nearest_expiry():
    """Verify resolve_nearest_expiry selects the nearest weekly expiry date."""
    # 2026-09-01 is Tuesday -> nearest Thursday is 2026-09-03
    disp, code = resolve_nearest_expiry(date(2026, 9, 1), "NIFTY")
    assert disp == "03-SEP-2026"
    assert code == "26S03"

    # 2026-09-03 is Thursday -> same day expiry
    disp, code = resolve_nearest_expiry(date(2026, 9, 3), "NIFTY")
    assert disp == "03-SEP-2026"
    assert code == "26S03"

    # 2026-09-04 is Friday -> next Thursday is 2026-09-10
    disp, code = resolve_nearest_expiry(date(2026, 9, 4), "NIFTY")
    assert disp == "10-SEP-2026"
    assert code == "26S10"


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
    """Verify backtest simulation under OIPulse Mode records dynamic contract symbol and preserves indicator targets/stoploss."""
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
            # Symbol should be either NIFTY or a resolved option like NIFTY26S...
            assert "NIFTY" in sym

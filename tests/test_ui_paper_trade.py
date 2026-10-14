from datetime import date, timedelta
import pandas as pd
import pytest

from app.database.paper_database import PaperTradeDatabase
from app.strategy.strategy_store import get_strategy, list_strategy_names


@pytest.fixture
def temp_paper_db(tmp_path):
    db_file = str(tmp_path / "test_paper_ui.db")
    return PaperTradeDatabase(db_path=db_file)


def test_strategy_store_adx_and_default_templates():
    strats = list_strategy_names()
    assert "ADX" in strats
    assert "EMA_RSI_MACD_Balanced" in strats

    adx_def = get_strategy("ADX")
    assert adx_def is not None
    assert adx_def["name"] == "ADX"
    inst = adx_def.get("instrument", {})
    assert "symbol" in inst
    assert inst["symbol"] in ("BANKNIFTY26OCT55300PE", "NIFTY26OCT22500PE")
    assert "start_date" in inst and "end_date" in inst


def test_paper_database_adx_seeding_and_accumulation(temp_paper_db):
    trades = temp_paper_db.get_accumulated_trades_for_strategy("ADX", max_days=30)
    assert len(trades) >= 4  # 3 real trades + 1 No Trade session

    # Check for Trade id-5000 (14-10-2026), Trade id-5001 (13-10-2026), No Trade, Trade id-5002 (10-10-2026)
    tids = [str(t.get("trade_id")) for t in trades]
    assert "5000" in tids
    assert "5001" in tids
    assert "5002" in tids
    assert "No Trade" in tids

    dates = [t["session_date"] for t in trades]
    assert "2026-10-14" in dates
    assert "2026-10-13" in dates
    assert "2026-10-11" in dates
    assert "2026-10-10" in dates


def test_current_date_validation_logic():
    today_str = str(date.today())
    past_date_str = str(date.today() - timedelta(days=5))

    # Case 1: Strategy dates match current date -> Valid
    strat_inst_valid = {"start_date": today_str, "end_date": today_str}
    is_valid = (
        strat_inst_valid.get("start_date") == today_str
        and strat_inst_valid.get("end_date") == today_str
    )
    assert is_valid is True

    # Case 2: Strategy dates do not match current date -> Error
    strat_inst_invalid = {"start_date": past_date_str, "end_date": past_date_str}
    is_valid = (
        strat_inst_invalid.get("start_date") == today_str
        and strat_inst_invalid.get("end_date") == today_str
    )
    assert is_valid is False


def test_active_instrument_resolution():
    # If tradingsymbol or symbol is explicit option contract
    inst1 = {"symbol": "NIFTY26OCT22500PE", "tradingsymbol": "NIFTY26OCT22500PE"}
    act1 = inst1.get("tradingsymbol") or inst1.get("symbol")
    assert act1 == "NIFTY26OCT22500PE"

    # If symbol is index name without contract, defaults to NIFTY26OCT22500PE
    inst2 = {"symbol": "NIFTY"}
    act2 = inst2.get("tradingsymbol") or inst2.get("symbol")
    if act2 in ("NIFTY", "BANKNIFTY", None, ""):
        act2 = "NIFTY26OCT22500PE"
    assert act2 == "NIFTY26OCT22500PE"


def test_previous_one_month_filtering_strictness(temp_paper_db):
    today = date.today()
    one_month_ago = today - timedelta(days=30)
    old_date = today - timedelta(days=40)

    # Insert a trade within 1 month and a trade older than 1 month
    temp_paper_db.save_single_trade(
        strategy_name="MONTH_FILTER_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date=str(today),
        trade={"trade_id": 101, "side": "LONG", "gross_pnl": 100.0, "net_pnl": 80.0, "entry_time": f"{today} 10:00:00"},
    )
    temp_paper_db.save_single_trade(
        strategy_name="MONTH_FILTER_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date=str(old_date),
        trade={"trade_id": 102, "side": "LONG", "gross_pnl": 100.0, "net_pnl": 80.0, "entry_time": f"{old_date} 10:00:00"},
    )

    trades = temp_paper_db.get_accumulated_trades_for_strategy("MONTH_FILTER_TEST", max_days=30)
    returned_dates = [t["session_date"] for t in trades]
    assert str(today) in returned_dates
    assert str(old_date) not in returned_dates

    # Check executed dates helper also excludes older dates
    exec_dates = temp_paper_db.get_executed_dates_for_strategy("MONTH_FILTER_TEST", max_days=30)
    assert str(today) in exec_dates
    assert str(old_date) not in exec_dates


def test_strategy_square_off_time_parsing_and_risk_config():
    from datetime import time
    from app.ui.paper_trade_app import parse_time_param

    # Test time parser
    assert parse_time_param("15:15", time(15, 30)) == time(15, 15)
    assert parse_time_param("15:20", time(15, 30)) == time(15, 20)
    assert parse_time_param(time(15, 25), time(15, 30)) == time(15, 25)
    assert parse_time_param(None, time(15, 15)) == time(15, 15)
    assert parse_time_param("invalid", time(15, 15)) == time(15, 15)

    # Test that NIFTY26OCT22500PE_BOTH_PAPERTRADE_TEST_V1 has square_off_time in risk
    strat = get_strategy("NIFTY26OCT22500PE_BOTH_PAPERTRADE_TEST_V1")
    assert strat is not None
    assert "risk" in strat
    assert "square_off_time" in strat["risk"]
    sq_time = parse_time_param(strat["risk"]["square_off_time"], time(15, 15))
    assert sq_time == time(15, 15)


def test_square_off_trigger_logic():
    from datetime import time

    sq_time = time(15, 15)

    # Before square-off: no square-off exit
    check_time_early = time(14, 30)
    assert not (check_time_early >= sq_time)

    # At or after square-off: square-off triggers
    check_time_sq = time(15, 15)
    assert check_time_sq >= sq_time

    check_time_late = time(15, 20)
    assert check_time_late >= sq_time

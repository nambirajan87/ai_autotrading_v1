import os
import sqlite3
import pytest
from datetime import datetime, date
from pathlib import Path

from scripts.schedule_oipulse_strength import (
    ensure_db_schema,
    save_record_to_db,
    get_latest_oipulse_row,
    ActiveSchedulerConfig,
)
from app.strategy.oipulse_resolver import (
    get_live_oipulse_status,
    calculate_oipulse_strike_and_type,
    resolve_oipulse_option,
    resolve_nearest_expiry_date,
    parse_strength_value,
)


@pytest.fixture
def temp_test_db(tmp_path):
    """Provides a fresh test SQLite DB with oi_strength table."""
    db_file = tmp_path / "test_oipulse.db"
    ensure_db_schema(db_file)
    return db_file


class TestTask1OIPulseSection:
    """Verifies Task 1: 6 dynamic metrics from 1-min OI Pulse backend triggers."""

    def test_oipulse_data_extraction_and_formatting(self, temp_test_db):
        sample_record = {
            "Trigger_Count": 27,
            "Trigger_Time": "2026-10-25 18:37:10",
            "Date": "23-10-2026",
            "Time": "12:15",
            "LTP": 22600.00,
            "Strength": "-117%",
        }
        row_id = save_record_to_db(sample_record, temp_test_db)
        assert row_id is not None

        latest_row = get_latest_oipulse_row(temp_test_db)
        assert latest_row is not None
        assert latest_row["Trigger_Count"] == 27
        assert latest_row["Trigger_Time"] == "2026-10-25 18:37:10"
        assert latest_row["Date"] == "23-10-2026"
        assert latest_row["Time"] == "12:15"
        assert latest_row["LTP"] == 22600.00
        assert latest_row["Strength"] == "-117%"

    def test_live_oipulse_status_structure(self, temp_test_db):
        sample_record = {
            "Trigger_Count": 42,
            "Trigger_Time": "2026-10-25 19:15:10",
            "Date": "23-10-2026",
            "Time": "EOD",
            "LTP": 22600.00,
            "Strength": "-117%",
        }
        save_record_to_db(sample_record, temp_test_db)

        status = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status["has_data"] is True
        assert status["trigger_no"] == 42
        assert status["trigger_time"] == "2026-10-25 19:15:10"
        assert status["date"] == "23-10-2026"
        assert status["time"] == "EOD"
        assert status["ltp"] == 22600.00
        assert status["strength_str"] == "-117%"
        assert status["strength_val"] == -117.0


class TestTask2ActiveInstrumentConstructionAndRotation:
    """Verifies Task 2: Active instrument construction via OI Pulse logic and rotation."""

    def test_bearish_strength_resolves_pe(self, temp_test_db):
        """Strength < -50% resolves PE strike and active contract."""
        sample_record = {
            "Trigger_Count": 27,
            "Trigger_Time": "2026-10-25 18:37:10",
            "Date": "23-10-2026",  # Friday
            "Time": "12:15",
            "LTP": 22600.00,
            "Strength": "-117%",
        }
        save_record_to_db(sample_record, temp_test_db)

        status = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status["option_type"] == "PE"
        assert status["strike"] == 22400
        assert status["has_active_instrument"] is True
        assert status["active_instrument"] == "NIFTY26OCT22400PE"
        assert status["resolved_symbol"] == "NIFTY26OCT22400PE"

    def test_bullish_strength_resolves_ce(self, temp_test_db):
        """Strength > 50% resolves CE strike and active contract."""
        sample_record = {
            "Trigger_Count": 28,
            "Trigger_Time": "2026-10-25 18:38:10",
            "Date": "23-10-2026",  # Friday
            "Time": "12:16",
            "LTP": 22600.00,
            "Strength": "+85%",
        }
        save_record_to_db(sample_record, temp_test_db)

        status = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status["option_type"] == "CE"
        assert status["strike"] == 22700
        assert status["has_active_instrument"] is True
        assert status["active_instrument"] == "NIFTY26OCT22700CE"

    def test_neutral_strength_suppresses_active_instrument(self, temp_test_db):
        """Neutral strength (-50% to +50%) yields No Signal and suppresses active instrument."""
        sample_record = {
            "Trigger_Count": 29,
            "Trigger_Time": "2026-10-25 18:39:10",
            "Date": "23-10-2026",
            "Time": "12:17",
            "LTP": 22600.00,
            "Strength": "+12%",
        }
        save_record_to_db(sample_record, temp_test_db)

        status = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status["option_type"] == "No Signal"
        assert status["strike"] is None
        assert status["has_active_instrument"] is False
        assert status["active_instrument"] is None

    def test_dynamic_instrument_rolling(self, temp_test_db):
        """Simulates consecutive 1-min triggers rolling from PE to CE."""
        # 1st trigger: Bearish PE
        rec1 = {
            "Trigger_Count": 1,
            "Trigger_Time": "2026-10-25 09:20:10",
            "Date": "23-10-2026",
            "Time": "09:20",
            "LTP": 22600.00,
            "Strength": "-90%",
        }
        save_record_to_db(rec1, temp_test_db)
        status1 = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status1["active_instrument"] == "NIFTY26OCT22400PE"

        # 2nd trigger: Momentum shifts to Bullish CE
        rec2 = {
            "Trigger_Count": 2,
            "Trigger_Time": "2026-10-25 09:21:10",
            "Date": "23-10-2026",
            "Time": "09:21",
            "LTP": 22620.00,
            "Strength": "+75%",
        }
        save_record_to_db(rec2, temp_test_db)
        status2 = get_live_oipulse_status(underlying="NIFTY", db_path=str(temp_test_db))
        assert status2["active_instrument"] == "NIFTY26OCT22750CE"
        assert status2["active_instrument"] != status1["active_instrument"]


class TestTask3HistoricalRawDataRemoved:
    """Verifies Task 3: Removal of 'Historical Raw Data (All Records)' section across all 3 apps."""

    def test_paper_trade_app_has_no_historical_raw_data(self):
        paper_file = Path(__file__).resolve().parent.parent / "app" / "ui" / "paper_trade_app.py"
        content = paper_file.read_text(encoding="utf-8")
        assert "Historical Raw Data (All Records)" not in content
        assert "btn_dl_raw_candles_paper" not in content

    def test_live_trade_app_has_no_historical_raw_data(self):
        live_file = Path(__file__).resolve().parent.parent / "app" / "ui" / "live_trade_app.py"
        content = live_file.read_text(encoding="utf-8")
        assert "Historical Raw Data (All Records)" not in content
        assert "btn_dl_raw_candles_live" not in content

    def test_backtest_app_has_no_historical_raw_data(self):
        backtest_file = Path(__file__).resolve().parent.parent / "app" / "ui" / "backtest_app.py"
        content = backtest_file.read_text(encoding="utf-8")
        assert "Historical Raw Data (All Records)" not in content
        assert "Truncated Date Alert" not in content


class TestCustomCESPEStrengthDropdowns:
    """Verifies configurable CE/PE strength thresholds in UI and logic."""

    def test_custom_ce_pe_strength_thresholds(self):
        # Default thresholds (50 and -50): +35% is No Signal, -45% is No Signal
        _, opt_def1 = calculate_oipulse_strike_and_type(22500, "Tuesday", 35)
        assert opt_def1 == "No Signal"

        _, opt_def2 = calculate_oipulse_strike_and_type(22500, "Tuesday", -45)
        assert opt_def2 == "No Signal"

        # Custom lower thresholds (ce_strength=30, pe_strength=-40):
        # +35% is now CE, -45% is now PE
        strike_ce, opt_ce = calculate_oipulse_strike_and_type(
            22500, "Tuesday", 35, ce_strength=30, pe_strength=-40
        )
        assert opt_ce == "CE"
        assert strike_ce is not None

        strike_pe, opt_pe = calculate_oipulse_strike_and_type(
            22500, "Tuesday", -45, ce_strength=30, pe_strength=-40
        )
        assert opt_pe == "PE"
        assert strike_pe is not None

        # Custom higher thresholds (ce_strength=70, pe_strength=-70):
        # +60% is No Signal, -60% is No Signal
        _, opt_high1 = calculate_oipulse_strike_and_type(
            22500, "Tuesday", 60, ce_strength=70, pe_strength=-70
        )
        assert opt_high1 == "No Signal"

        _, opt_high2 = calculate_oipulse_strike_and_type(
            22500, "Tuesday", -60, ce_strength=70, pe_strength=-70
        )
        assert opt_high2 == "No Signal"

    def test_oipulse_logic_find_value_custom_thresholds(self):
        from oipulse_logic import find_value

        # Default: 40 is No Signal
        _, opt1 = find_value(22500, "Tuesday", 40)
        assert opt1 == "No Signal"

        # Custom: ce_strength=30 -> 40 is CE
        _, opt2 = find_value(22500, "Tuesday", 40, ce_strength=30)
        assert opt2 == "CE"

        # Custom: pe_strength=-30 -> -40 is PE
        _, opt3 = find_value(22500, "Tuesday", -40, pe_strength=-30)
        assert opt3 == "PE"

    def test_saved_strategy_contains_ce_and_pe_strength(self):
        import json
        strat_file = Path(__file__).resolve().parent.parent / "data" / "saved_strategies.json"
        with open(strat_file, "r", encoding="utf-8") as f:
            strats = json.load(f)

        oi_v2 = strats.get("PAPERTRADE_TEST_OI_V2")
        assert oi_v2 is not None
        inst = oi_v2.get("instrument", {})
        assert inst.get("ce_strength") == 50
        assert inst.get("pe_strength") == -50


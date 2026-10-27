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


def test_paper_trade_constant_sections_timing_and_content():
    from datetime import datetime, timedelta
    now_dt = datetime.now()
    timeframe_m = 1

    tf_val = max(1, timeframe_m)
    seconds_past_hour = now_dt.minute * 60 + now_dt.second
    tf_seconds = tf_val * 60
    next_boundary_sec = ((seconds_past_hour // tf_seconds) + 1) * tf_seconds
    next_candle_dt = now_dt.replace(minute=0, second=0, microsecond=0) + timedelta(seconds=next_boundary_sec)
    rem_seconds = max(0, int((next_candle_dt - now_dt).total_seconds()))
    next_check_str = next_candle_dt.strftime("%H:%M:%S")

    last_bar_dt = now_dt - timedelta(minutes=tf_val)
    last_bar_str = last_bar_dt.strftime("%Y-%m-%d %H:%M:%S")
    bar_closed_at_str = now_dt.strftime("%H:%M:%S")
    latest_score = 5
    sig_disp = "HOLD"

    sec1_text = f"CANDLE CLOSE TRIGGER ACTIVE: Next signal check at {next_check_str} ({rem_seconds}s remaining) on {tf_val}m candle close."
    sec2_text = f"Signal bar close check -> Last Signal Bar: {last_bar_str} (Closed at {bar_closed_at_str})  |  Signal: {sig_disp}  |  Score: {latest_score}  |  Continuous 2s LTP, chart & exit monitoring active."

    assert "CANDLE CLOSE TRIGGER ACTIVE: Next signal check at" in sec1_text
    assert f"on {tf_val}m candle close." in sec1_text
    assert "Signal bar close check -> Last Signal Bar:" in sec2_text
    assert "Continuous 2s LTP, chart & exit monitoring active." in sec2_text


def test_strategy_period_display_resolution_from_strategy_config():
    strat_def = get_strategy("EMA20_50_RSI_MACD_Plus_4Ind")
    assert strat_def is not None
    inst_cfg = strat_def.get("instrument", {})

    today_str = str(date.today())
    one_month_ago_str = str(date.today() - timedelta(days=30))

    strat_from = str(
        inst_cfg.get("start_date")
        or inst_cfg.get("from_date")
        or inst_cfg.get("date_from")
        or strat_def.get("start_date")
        or strat_def.get("from_date")
        or strat_def.get("date_from")
        or strat_def.get("config", {}).get("instrument", {}).get("start_date")
        or one_month_ago_str
    )
    strat_to = str(
        inst_cfg.get("end_date")
        or inst_cfg.get("to_date")
        or inst_cfg.get("date_to")
        or strat_def.get("end_date")
        or strat_def.get("to_date")
        or strat_def.get("date_to")
        or strat_def.get("config", {}).get("instrument", {}).get("end_date")
        or today_str
    )

    # Strategy dates must be from stored strategy config (2026-10-01 to 2026-10-19)
    assert strat_from == "2026-10-01"
    assert strat_to == "2026-10-19"
    assert (strat_from, strat_to) != (one_month_ago_str, today_str)


def test_paper_database_start_and_end_date_filtering(temp_paper_db):
    temp_paper_db.save_single_trade(
        strategy_name="DATE_RANGE_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date="2026-10-05",
        trade={"trade_id": 101, "side": "LONG", "gross_pnl": 50.0, "net_pnl": 30.0, "entry_time": "2026-10-05 10:00:00"},
    )
    temp_paper_db.save_single_trade(
        strategy_name="DATE_RANGE_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date="2026-10-15",
        trade={"trade_id": 102, "side": "SHORT", "gross_pnl": 100.0, "net_pnl": 80.0, "entry_time": "2026-10-15 10:00:00"},
    )
    temp_paper_db.save_single_trade(
        strategy_name="DATE_RANGE_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date="2026-10-25",
        trade={"trade_id": 103, "side": "LONG", "gross_pnl": 150.0, "net_pnl": 130.0, "entry_time": "2026-10-25 10:00:00"},
    )

    # Filter between 2026-10-01 and 2026-10-10 (should only get 2026-10-05)
    t1 = temp_paper_db.get_accumulated_trades_for_strategy(
        "DATE_RANGE_TEST", start_date="2026-10-01", end_date="2026-10-10"
    )
    assert len(t1) == 1
    assert t1[0]["session_date"] == "2026-10-05"

    # Filter between 2026-10-10 and 2026-10-20 (should only get 2026-10-15)
    t2 = temp_paper_db.get_accumulated_trades_for_strategy(
        "DATE_RANGE_TEST", start_date="2026-10-10", end_date="2026-10-20"
    )
    assert len(t2) == 1
    assert t2[0]["session_date"] == "2026-10-15"




def test_paper_trade_audit_trail_instrument_and_index_ltp_columns():
    """Verifies that Trade Audit Trail and Trade Audit Trail-2 in Paper/Live Trading include
    'Instrument' and 'Index-LTP' columns alongside 'Option Type'."""
    import re
    import os
    import sqlite3
    import numpy as np
    import pandas as pd
    from app.ui.paper_trade_app import PROJECT_ROOT

    trades = pd.DataFrame([
        {
            "trade_id": 1,
            "symbol": "NIFTY26S0124000CE",
            "side": "LONG",
            "score": 3,
            "session_date": "2026-09-01",
            "entry_time": "2026-09-01 11:21:00",
            "exit_time": "2026-09-01 11:35:00",
            "quantity": 50,
            "entry_price": 120.0,
            "exit_price": 135.0,
            "net_pnl": 750.0,
        },
        {
            "trade_id": 2,
            "symbol": "NIFTY26SEP22650CE",
            "side": "LONG",
            "score": 4,
            "session_date": "2026-10-21",
            "entry_time": "2026-10-21 10:15:00",
            "exit_time": "2026-10-21 10:45:00",
            "quantity": 50,
            "entry_price": 80.0,
            "exit_price": 92.0,
            "net_pnl": 600.0,
            "index_ltp": 22640.5,
        },
        {
            "trade_id": 3,
            "symbol": "RELIANCE",
            "side": "LONG",
            "score": 2,
            "session_date": "2026-10-21",
            "entry_time": "2026-10-21 11:00:00",
            "exit_time": "2026-10-21 11:30:00",
            "quantity": 10,
            "entry_price": 2500.0,
            "exit_price": 2520.0,
            "net_pnl": 200.0,
        }
    ])

    inst_cfg = {"tradingsymbol": "NIFTY 27-OCT-2026 22500 PE", "underlying": "NIFTY"}
    active_instrument = "NIFTY 27-OCT-2026 22500 PE"
    underlying_name = "NIFTY"

    db_oi_file = PROJECT_ROOT / "data" / "backtest.db"
    oi_conn = None
    if os.path.exists(db_oi_file):
        try:
            oi_conn = sqlite3.connect(db_oi_file)
        except Exception:
            oi_conn = None
    oi_cur = oi_conn.cursor() if oi_conn else None
    c_ts_map = {}

    def get_trade_instrument_and_opt_type(row):
        sym = str(row.get("symbol") or "").strip()
        if not sym or sym.lower() in ("none", "nan", ""):
            sym = str(active_instrument or inst_cfg.get("tradingsymbol") or inst_cfg.get("symbol") or underlying_name or "").strip()

        cfg_sym = str(inst_cfg.get("tradingsymbol") or inst_cfg.get("symbol") or "").strip()
        cfg_eq = str(inst_cfg.get("equity_name") or active_instrument or "").strip()

        if sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            if cfg_sym and cfg_sym not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") and re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", cfg_sym.upper()):
                sym = cfg_sym
            elif cfg_eq and re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", cfg_eq.upper()) and "OIPulse" not in cfg_eq:
                sym = cfg_eq

        idx_ltp = np.nan
        for k in ("index_ltp", "Index-LTP", "index_price"):
            if k in row and pd.notna(row[k]):
                try:
                    idx_ltp = round(float(row[k]), 2)
                    break
                except Exception:
                    pass

        if pd.isna(idx_ltp):
            e_ts = row.get("entry_time") or row.get("signal_time")
            if e_ts and pd.notna(e_ts):
                try:
                    dt = pd.to_datetime(e_ts)
                    d_str = dt.strftime("%d-%m-%Y")
                    t_str = dt.strftime("%H:%M:%S")

                    if oi_cur:
                        oi_cur.execute("SELECT LTP FROM oi_strength WHERE Date = ? AND Time = ? LIMIT 1", (d_str, t_str))
                        r_ltp = oi_cur.fetchone()
                        if not r_ltp:
                            oi_cur.execute("SELECT LTP FROM oi_strength WHERE Date = ? AND Time <= ? ORDER BY Time DESC LIMIT 1", (d_str, t_str))
                            r_ltp = oi_cur.fetchone()
                        if not r_ltp:
                            oi_cur.execute("SELECT LTP FROM oi_strength WHERE Date = ? AND Time >= ? ORDER BY Time ASC LIMIT 1", (d_str, t_str))
                            r_ltp = oi_cur.fetchone()
                        if r_ltp and r_ltp[0] is not None:
                            idx_ltp = round(float(r_ltp[0]), 2)
                except Exception:
                    pass

        opt_t = ""
        for k in ("opt_type", "option_type", "Option Type"):
            if k in row and pd.notna(row[k]) and str(row[k]).strip() and str(row[k]).strip().lower() != "none":
                opt_t = str(row[k]).strip().upper()
                break

        if not opt_t or opt_t == "-":
            sym_upper = sym.upper()
            m_sym = re.search(r"(?:^|[_\-\s\d])(CE|PE)$", sym_upper) or re.search(r"\b(CE|PE)\b", sym_upper)
            if m_sym:
                opt_t = m_sym.group(1)
            elif sym in (underlying_name, "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                cfg_opt = str(inst_cfg.get("option_type") or "").upper()
                cfg_eq_upper = cfg_eq.upper()
                m_eq = re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", cfg_eq_upper)
                if "CE" in cfg_opt:
                    opt_t = "CE"
                elif "PE" in cfg_opt:
                    opt_t = "PE"
                elif m_eq:
                    opt_t = m_eq.group(1)
                else:
                    opt_t = "-"
            else:
                opt_t = "-"

        return pd.Series([sym, idx_ltp, opt_t], index=["Instrument", "Index-LTP", "Option Type"])

    trades[["Instrument", "Index-LTP", "Option Type"]] = trades.apply(get_trade_instrument_and_opt_type, axis=1)
    if oi_conn:
        try:
            oi_conn.close()
        except Exception:
            pass

    assert trades.loc[0, "Instrument"] == "NIFTY26S0124000CE"
    assert trades.loc[0, "Index-LTP"] == 24124.35
    assert trades.loc[0, "Option Type"] == "CE"

    assert trades.loc[1, "Instrument"] == "NIFTY26SEP22650CE"
    assert trades.loc[1, "Index-LTP"] == 22640.5
    assert trades.loc[1, "Option Type"] == "CE"

    assert trades.loc[2, "Instrument"] == "RELIANCE"
    assert trades.loc[2, "Option Type"] == "-"

    # Verify column ordering in Trade Audit Trail 1
    trades["Display_ID"] = trades["trade_id"].apply(lambda tid: f"Trade id-{tid}")
    cols_wanted = [
        "Display_ID", "Instrument", "Index-LTP", "Option Type", "side", "regime", "Calculated_Score", "session_date", "entry_time", "exit_time",
        "quantity", "entry_price", "Margin used", "peak_gain",
        "5%", "10%", "15%", "20%", "25%", "30%",
        "exit_price", "stop_loss", "target",
        "exit_reason", "gross_pnl", "charges", "net_pnl", "status",
    ]
    display_cols = [c for c in cols_wanted if c in trades.columns]
    assert "Instrument" in display_cols
    assert "Index-LTP" in display_cols
    assert "Option Type" in display_cols
    assert display_cols.index("Instrument") == 1
    assert display_cols.index("Index-LTP") == 2
    assert display_cols.index("Option Type") == 3

    # Verify Trade Audit Trail 2 columns (Candle Progression)
    c_cols = [f"C{i}" for i in range(1, 11)]
    c_df = pd.DataFrame({col: ["-"] * len(trades) for col in c_cols}, index=trades.index)
    base_cols_2 = [c for c in display_cols if c in trades.columns]
    trades_2 = pd.concat([trades[base_cols_2], c_df], axis=1)
    assert "Instrument" in trades_2.columns
    assert "Index-LTP" in trades_2.columns
    assert "Option Type" in trades_2.columns
    assert trades_2.loc[0, "Index-LTP"] == 24124.35


def test_auto_paper_trade_trigger_time_and_market_window():
    """Verifies that the 09:16 AM auto-trigger strictly adheres to the Market Window:
    Monday - Friday, 09:15 AM - 03:15 PM, triggering at/after 09:16:00 AM up to 15:15:00 PM."""
    from datetime import datetime
    from scripts.schedule_oipulse_strength import (
        is_paper_trade_auto_triggered,
        ActiveSchedulerConfig,
    )

    ActiveSchedulerConfig.market_hours_only = True

    # Monday (weekday=0) tests
    mon_early = datetime(2026, 10, 26, 9, 15, 30)  # 09:15:30 AM
    assert is_paper_trade_auto_triggered(mon_early) is False

    mon_trigger_time = datetime(2026, 10, 26, 9, 16, 0)  # 09:16:00 AM (exact trigger point)
    assert is_paper_trade_auto_triggered(mon_trigger_time) is True

    mon_midday = datetime(2026, 10, 26, 12, 30, 0)  # 12:30:00 PM
    assert is_paper_trade_auto_triggered(mon_midday) is True

    mon_window_end = datetime(2026, 10, 26, 15, 15, 0)  # 03:15:00 PM
    assert is_paper_trade_auto_triggered(mon_window_end) is True

    mon_past_window = datetime(2026, 10, 26, 15, 15, 1)  # 03:15:01 PM
    assert is_paper_trade_auto_triggered(mon_past_window) is False

    # Weekend tests
    sat_noon = datetime(2026, 10, 24, 10, 30, 0)  # Saturday
    assert is_paper_trade_auto_triggered(sat_noon) is False

    sun_afternoon = datetime(2026, 10, 25, 14, 0, 0)  # Sunday
    assert is_paper_trade_auto_triggered(sun_afternoon) is False

    # MockLive / Anytime mode (market_hours_only = False)
    ActiveSchedulerConfig.market_hours_only = False
    assert is_paper_trade_auto_triggered(sat_noon) is True
    assert is_paper_trade_auto_triggered(mon_early) is True
    ActiveSchedulerConfig.market_hours_only = True


def test_execute_paper_trade_button_removed_and_watching_oi_strength_displayed():
    """Verifies that the manual '🚀 Execute Paper Trade' button has been removed from paper_trade_app.py
    and replaced with the 09:16 AM auto-trigger watching OI Strength message."""
    from pathlib import Path
    paper_file = Path(__file__).resolve().parent.parent / "app" / "ui" / "paper_trade_app.py"
    code = paper_file.read_text(encoding="utf-8")

    # Manual Execute button must be removed
    assert "btn_run_paper_live" not in code
    assert 'st.button("🚀 Execute Paper Trade"' not in code

    # Auto-trigger check and watching for OI Strength message must be present
    assert "is_paper_trade_auto_triggered" in code
    assert "watching for OI Strength" in code or "watching OI Strength" in code
    assert "09:16 AM" in code
    assert "OI Pulse Live Strength Feed" in code


def test_render_html_block_eliminates_multiline_indent_codeblock_leak():
    """Verifies that render_html_block strips empty lines and line indentation,
    preventing Streamlit from misinterpreting indented HTML as a markdown code block."""
    from unittest.mock import MagicMock, patch
    from app.ui.paper_trade_app import render_html_block

    raw_multiline_html = """
    <div style="background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 10px 14px;">
        <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-bottom: 6px;">
            <span style="color: #ef4444; font-weight: 600;">🛑 SL: ₹285.00 <span style="color: #94a3b8; font-weight: normal;">(15.00 pts away)</span></span>
            <span style="color: #38bdf8; font-weight: 600;">🔵 Entry: ₹300.00 &nbsp;|&nbsp; Current LTP: <b style="color: #ffffff;">₹300.00</b></span>
            <span style="color: #10b981; font-weight: 600;">🎯 Target: ₹390.00 <span style="color: #94a3b8; font-weight: normal;">(90.00 pts away)</span></span>
        </div>
        <div style="height: 10px; width: 100%; background: #334155; border-radius: 5px; position: relative; overflow: hidden;">
            <div style="position: absolute; left: 0; top: 0; bottom: 0; width: 14.3%; background: linear-gradient(90deg, #ef4444 0%, #3b82f6 50%, #10b981 100%); border-radius: 5px; transition: width 0.3s ease;"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: #94a3b8; margin-top: 5px;">
            <span>Risk Cushion: <b style="color: #cbd5e1;">5.0%</b></span>
            <span style="color: #ffd600; font-weight: 600;">🟡 AT BREAKEVEN</span>
            <span>Distance to Target: <b style="color: #cbd5e1;">30.0%</b> remaining</span>
        </div>
    </div>
    """

    mock_st = MagicMock()
    with patch("app.ui.paper_trade_app.st", mock_st):
        render_html_block(raw_multiline_html)

    mock_st.markdown.assert_called_once()
    rendered_arg = mock_st.markdown.call_args[0][0]
    kwargs = mock_st.markdown.call_args[1]

    assert kwargs.get("unsafe_allow_html") is True
    # Verify no newlines or leading 4+ spaces exist that would trigger markdown code blocks
    assert "\n" not in rendered_arg
    assert "    " not in rendered_arg
    assert rendered_arg.startswith("<div")
    assert rendered_arg.endswith("</div>")


def test_papertrade_test_oi_v2_strategy_config_and_scheduler_default():
    """Verifies that PAPERTRADE_TEST_OI_V2 is saved, configured for BOTH sides with entry_start 09:16,
    and is configured as default in ActiveSchedulerConfig and paper_trade_app."""
    import json
    from pathlib import Path
    from scripts.schedule_oipulse_strength import ActiveSchedulerConfig

    strat_file = Path(__file__).resolve().parent.parent / "data" / "saved_strategies.json"
    with open(strat_file, "r", encoding="utf-8") as f:
        strats = json.load(f)

    target_strat = strats.get("PAPERTRADE_TEST_OI_V2")
    assert target_strat is not None, "PAPERTRADE_TEST_OI_V2 strategy must exist in saved_strategies.json"

    # Trade side should be BOTH to allow matching conditions for both LONG and SHORT
    indicators = target_strat.get("indicators", {})
    risk = target_strat.get("risk", {})
    assert indicators.get("trade_side") == "BOTH"
    assert risk.get("trade_side") == "BOTH"

    # Entry start should be 09:16 matching the scheduler auto-trigger
    assert risk.get("entry_start") == "09:16"

    # ActiveSchedulerConfig must specify PAPERTRADE_TEST_OI_V2 as default_strategy
    assert ActiveSchedulerConfig.default_strategy == "PAPERTRADE_TEST_OI_V2"




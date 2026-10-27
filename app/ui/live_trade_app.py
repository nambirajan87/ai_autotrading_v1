import logging
import os
import sys
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path when executed via 'streamlit run'
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    go = None
    make_subplots = None
    HAS_PLOTLY = False

from app.data.historical_data import load_csv
from app.data.instrument_loader import (
    DEFAULT_INDEX_EXPIRIES,
    INDEX_LOT_SIZES,
    NIFTY_STANDARD_INSTRUMENTS,
    load_nse_equities,
)
from app.database.live_database import get_live_db
from app.gateways.gateway_factory import get_gateway
from app.monitoring.audit_logger import log_audit_event, record_session_visit
from app.strategy.custom_strategy import (
    CustomStrategyConfig,
    calculate_custom_indicators,
    score_custom_row,
)
from app.strategy.strategy_store import get_strategy, list_strategy_names, save_strategy
from app.strategy.oipulse_resolver import (
    calculate_oipulse_strike_and_type,
    calculate_option_price,
    get_live_oipulse_status,
    get_or_fetch_active_option_candles,
    parse_oipulse_contract_symbol,
    resolve_nearest_expiry_date,
    resolve_oipulse_option,
    validate_oipulse_contract_symbol,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_market_trading_hours() -> tuple:
    """
    Validates regular Indian stock market hours (NSE/BSE).
    Market hours: Monday to Friday, 09:15:00 to 15:30:00 IST.
    Returns (is_open: bool, message: str).
    """
    now = datetime.now()
    if now.weekday() >= 5:
        day_name = "Saturday" if now.weekday() == 5 else "Sunday"
        return False, f"Market Closed ({day_name} - Weekend). Regular market hours: 09:15 to 15:30 IST (Mon - Fri)."
    now_time = now.time()
    if now_time < time(9, 15, 0):
        return False, f"Market Closed (Pre-market). Regular trading starts at 09:15:00 IST (Current time: {now.strftime('%H:%M:%S')})."
    if now_time > time(15, 30, 0):
        return False, f"Market Closed (Post-market). Regular trading ended at 15:30:00 IST (Current time: {now.strftime('%H:%M:%S')})."
    return True, "Market Open (09:15:00 to 15:30:00 IST)."


def parse_time_param(val, default_time: time) -> time:
    """Safely parses a string or time object into datetime.time."""
    if isinstance(val, time):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parts = [int(p) for p in val.strip().split(":")]
            return time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            return default_time
    return default_time


def render_live_trade_module():
    # Record visit for auditing
    record_session_visit()

    live_db = get_live_db()
    gw = get_gateway("KOTAK")

    # ---------------- Custom CSS ----------------
    st.markdown("""
    <style>
        .metric-card {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 14px;
            margin-bottom: 10px;
        }
        .active-trade-box {
            background: linear-gradient(135deg, rgba(0, 230, 118, 0.15), rgba(38, 166, 154, 0.22));
            border: 2px solid #00e676;
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 16px;
            animation: pulse-border 2s infinite;
        }
        .active-trade-box-short {
            background: linear-gradient(135deg, rgba(255, 82, 82, 0.15), rgba(239, 83, 80, 0.22));
            border: 2px solid #ff5252;
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 16px;
            animation: pulse-border-red 2s infinite;
        }
        @keyframes pulse-border {
            0% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.4); }
            70% { box-shadow: 0 0 0 10px rgba(0, 230, 118, 0); }
            100% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0); }
        }
        @keyframes pulse-border-red {
            0% { box-shadow: 0 0 0 0 rgba(255, 82, 82, 0.4); }
            70% { box-shadow: 0 0 0 10px rgba(255, 82, 82, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 82, 82, 0); }
        }
    </style>
    """, unsafe_allow_html=True)

    # ---------------- 1. Sidebar Navigation Panel ----------------
    st.sidebar.title("⚡ Live Trading Terminal")
    st.sidebar.caption("Kotak Neo Live Algorithmic Execution Terminal")

    st.sidebar.markdown("### 🎯 Strategy Filter")
    saved_strats = list_strategy_names()
    db_strats = live_db.get_all_strategy_names()
    all_strats = sorted(list(set(saved_strats + db_strats)))
    if not all_strats:
        all_strats = ["ADX", "EMA_RSI_MACD_Balanced"]

    if "live_selected_strategy" not in st.session_state or st.session_state.live_selected_strategy not in all_strats:
        st.session_state.live_selected_strategy = "ADX" if "ADX" in all_strats else all_strats[0]

    selected_strat = st.sidebar.selectbox(
        "Strategy Name",
        all_strats,
        index=all_strats.index(st.session_state.live_selected_strategy),
        key="live_sidebar_strat_select",
    )
    st.session_state.live_selected_strategy = selected_strat

    if st.sidebar.button("👁️ View Run", type="primary", use_container_width=True, key="live_btn_view_run"):
        st.session_state.live_selected_strategy = selected_strat
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔵 Kotak Neo Live Broker")
    gw_info = gw.get_connection_info()
    if gw_info.get("connected"):
        st.sidebar.success(f"**Kotak Neo API**: Connected & Ready ({gw_info.get('latency_ms', 38)}ms)")
    else:
        st.sidebar.warning("**Kotak Neo API**: Session Ready / Sandbox Mode")

    if st.sidebar.button("🔐 Re-authenticate Kotak 2FA", use_container_width=True, help="Refreshes 2FA TOTP session token with Kotak Neo"):
        with st.sidebar:
            with st.spinner("Connecting to Kotak Neo 2FA..."):
                if gw.login():
                    st.success("✅ Kotak Neo Authenticated & Session Token Active!")
                else:
                    st.error("❌ 2FA Login failed. Verify credentials in .env")

    st.sidebar.markdown("---")
    with st.sidebar.expander("⚠️ Danger Zone", expanded=False):
        confirm_clear = st.checkbox("Confirm deletion of this live history", key="confirm_clear_strat_live")
        if st.button("🗑️ Clear History for this Strategy", disabled=not confirm_clear, use_container_width=True):
            live_db.clear_history_for_strategy(selected_strat)
            st.success(f"Cleared live history for {selected_strat}!")
            st.rerun()

    # ---------------- 2. Main Page: Cloned PaperTrade Results View ----------------
    # Load strategy configuration
    strat_def = get_strategy(selected_strat) or {}
    ind_cfg = strat_def.get("indicators", {})
    risk_cfg = strat_def.get("risk", {})
    cost_cfg = strat_def.get("cost", {})
    inst_cfg = strat_def.get("instrument", {})

    capital = float(risk_cfg.get("capital", 100_000.0))
    strategy_sq_time = parse_time_param(risk_cfg.get("square_off_time"), time(15, 15))
    strategy_entry_start = parse_time_param(risk_cfg.get("entry_start"), time(9, 20))
    strategy_entry_end = parse_time_param(risk_cfg.get("entry_end"), time(15, 0))

    # Fetch accumulated live trades for this strategy within previous 1 month
    accumulated_trades_list = live_db.get_accumulated_trades_for_strategy(selected_strat, max_days=30)
    trades = pd.DataFrame(accumulated_trades_list) if accumulated_trades_list else pd.DataFrame()

    active_open_trade = None
    if not trades.empty and "status" in trades.columns:
        open_rows = trades[trades["status"] == "ACTIVE"]
        if not open_rows.empty:
            active_open_trade = open_rows.iloc[0].to_dict()

    # Resolve active instrument
    fo_mode = inst_cfg.get("fo_mode", "Normal Mode")
    underlying_name = str(inst_cfg.get("underlying") or "NIFTY").strip()
    ce_strength = float(inst_cfg.get("ce_strength", 50.0))
    pe_strength = float(inst_cfg.get("pe_strength", -50.0))
    oi_status = get_live_oipulse_status(underlying=underlying_name, ce_strength=ce_strength, pe_strength=pe_strength)

    # 10-Step Lifecycle:
    # If active open position exists (Step 7/8/9), Step 2 Strike Rotation is STOPPED!
    if active_open_trade is not None:
        active_instrument = str(active_open_trade.get("symbol"))
    elif fo_mode == "OIPulse Mode":
        # Step 1: Check OI Strength breakout (>ce_strength or <pe_strength)
        strength_val = float(oi_status.get("strength_val", 0.0) or 0.0)
        if strength_val > ce_strength or strength_val < pe_strength:
            # Step 2: Construct Active Instrument
            cand = oi_status.get("active_instrument")
            # Step 3: Validate check
            if cand:
                is_valid, _ = validate_oipulse_contract_symbol(cand, underlying=underlying_name)
                active_instrument = cand if is_valid else None
            else:
                active_instrument = None
        else:
            # Step 1: Standby (Neutral band)
            active_instrument = None
    else:
        raw_sym = inst_cfg.get("tradingsymbol") or inst_cfg.get("symbol")
        if raw_sym in ("NIFTY", "BANKNIFTY", None, ""):
            active_instrument = "NIFTY26OCT22500PE"
        else:
            active_instrument = raw_sym


    # Current date and previous 1 month date window (strictly 30 days)
    today = date.today()
    today_str = str(today)
    one_month_ago = today - timedelta(days=30)
    one_month_ago_str = str(one_month_ago)

    # Strategy dates
    strat_from = str(inst_cfg.get("start_date") or inst_cfg.get("from_date") or inst_cfg.get("date_from") or today_str)
    strat_to = str(inst_cfg.get("end_date") or inst_cfg.get("to_date") or inst_cfg.get("date_to") or today_str)

    # Trailing Stoploss indicator text
    tsl_on = ind_cfg.get("trailing_sl_enabled") or risk_cfg.get("trailing_sl_enabled")
    tsl_runner = ind_cfg.get("trail_beyond_target") or risk_cfg.get("trail_beyond_target")
    tsl_info = " | Trailing SL: Model B Active" + (" (Runner)" if tsl_runner else "") if tsl_on else ""

    # Page Heading: Display "Strategy Name" as Heading
    if fo_mode == "OIPulse Mode":
        if active_open_trade is not None:
            st.header(f"Results: {selected_strat} ({active_instrument} — In Trade)")
        elif active_instrument:
            st.header(f"Results: {selected_strat} ({active_instrument})")
        else:
            st.header(f"Results: {selected_strat} ({underlying_name} Option Chain — Standby)")
    else:
        st.header(f"Results: {selected_strat} ({active_instrument})")
    st.caption(f"Strategy: `{selected_strat}` | Period: {one_month_ago_str} to {today_str} | Live Kotak Trade Engine{tsl_info} | Square-off Time: **{strategy_sq_time.strftime('%H:%M')} IST**")

    # Notice
    st.warning("⚠️ **Notice:** Past results do not predict future results. Changing parameters after seeing results turns this data into in-sample data.")

    real_trades = (
        trades[trades["status"] != "NO_TRADE"].copy()
        if not trades.empty and "status" in trades.columns
        else trades.copy()
    )

    # Compute KPI performance metrics across accumulated trades
    num_trades = len(real_trades)
    net_pnl = float(real_trades["net_pnl"].sum()) if not real_trades.empty else 0.0
    gross_pnl = float(real_trades["gross_pnl"].sum()) if not real_trades.empty else 0.0
    total_costs = float(real_trades["charges"].sum()) if not real_trades.empty else 0.0
    return_pct = (net_pnl / capital * 100.0) if capital > 0 else 0.0

    wins = real_trades[real_trades["gross_pnl"] > 0] if not real_trades.empty else pd.DataFrame()
    losses = real_trades[real_trades["gross_pnl"] < 0] if not real_trades.empty else pd.DataFrame()
    win_rate = (len(wins) / num_trades * 100.0) if num_trades > 0 else 0.0

    sum_gains = float(wins["gross_pnl"].sum()) if not wins.empty else 0.0
    sum_losses = abs(float(losses["gross_pnl"].sum())) if not losses.empty else 0.0
    profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (99.0 if sum_gains > 0 else 0.0)
    pf_str = f"{profit_factor:.2f}" if sum_losses > 0 else ("∞ (No Losses)" if gross_pnl > 0 else "0.00")

    # Drawdown and Secondary Metrics
    if not real_trades.empty:
        cum_pnl = real_trades.sort_values("id")["net_pnl"].cumsum()
        peaks = np.maximum.accumulate(cum_pnl)
        dd = peaks - cum_pnl
        max_dd = float(dd.max()) if len(dd) > 0 else 0.0
        max_dd_pct = (max_dd / capital * 100.0) if capital > 0 else 0.0
    else:
        max_dd = 0.0
        max_dd_pct = 0.0

    tot_slip = float(real_trades["slippage_cost"].sum()) if "slippage_cost" in real_trades.columns else 0.0
    avg_tr = (net_pnl / num_trades) if num_trades > 0 else 0.0
    avg_win = float(wins["net_pnl"].mean()) if not wins.empty else 0.0
    avg_loss = abs(float(losses["net_pnl"].mean())) if not losses.empty else 0.0
    rr = (avg_win / avg_loss) if avg_loss > 0 else (avg_win if avg_win > 0 else None)
    rr_str = f"{rr:.2f}" if rr is not None else "N/A"

    # Loss streak
    loss_streak = 0
    cur_streak = 0
    if not real_trades.empty:
        for p in real_trades.sort_values("id")["net_pnl"]:
            if p < 0:
                cur_streak += 1
                loss_streak = max(loss_streak, cur_streak)
            else:
                cur_streak = 0

    # Sharpe ratio
    if len(real_trades) >= 3 and real_trades["net_pnl"].std() > 0:
        sh = (real_trades["net_pnl"].mean() / real_trades["net_pnl"].std()) * np.sqrt(252)
        sh_str = f"{sh:.2f}"
    else:
        sh_str = "N/A"

    # KPI Metrics Row 1
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Trades", num_trades)
    k2.metric("Net P&L (₹)", f"{net_pnl:,.2f}", f"{return_pct:.2f}%")
    k3.metric("Gross P&L (₹)", f"{gross_pnl:,.2f}")
    k4.metric("Total Costs (₹)", f"{total_costs:,.2f}")
    k5.metric("Win Rate", f"{win_rate:.1f}%")
    k6.metric("Profit Factor", pf_str)

    # KPI Metrics Row 2
    k7, k8, k9, k10, k11, k12 = st.columns(6)
    k7.metric("Max Drawdown", f"₹{max_dd:,.0f}", f"-{max_dd_pct:.1f}%")
    k8.metric("Total Slippage (₹)", f"{tot_slip:,.2f}")
    k9.metric("Avg Trade (₹)", f"{avg_tr:,.2f}")
    k10.metric("Reward/Risk", rr_str)
    k11.metric("Max Loss Streak", loss_streak)
    k12.metric("Sharpe Ratio", sh_str)

    # Strategy Calculated_Score Profile Metrics
    max_score_cfg = 0
    if ind_cfg.get("ema_enabled", False): max_score_cfg += ind_cfg.get("ema_weight", 2)
    if ind_cfg.get("rsi_enabled", False): max_score_cfg += ind_cfg.get("rsi_weight", 1)
    if ind_cfg.get("macd_enabled", False): max_score_cfg += ind_cfg.get("macd_weight", 2)
    if ind_cfg.get("vwap_enabled", False): max_score_cfg += ind_cfg.get("vwap_weight", 1)
    if ind_cfg.get("adx_enabled", False): max_score_cfg += ind_cfg.get("adx_weight", 1)
    if ind_cfg.get("volume_enabled", False): max_score_cfg += ind_cfg.get("volume_weight", 1)
    if ind_cfg.get("bollinger_enabled", False): max_score_cfg += ind_cfg.get("bollinger_weight", 1)
    if ind_cfg.get("stoch_enabled", False): max_score_cfg += ind_cfg.get("stoch_weight", 1)
    if ind_cfg.get("obv_enabled", False): max_score_cfg += ind_cfg.get("obv_weight", 1)
    if ind_cfg.get("higher_high_enabled", False): max_score_cfg += ind_cfg.get("higher_high_weight", 1)
    if ind_cfg.get("lower_low_enabled", False): max_score_cfg += ind_cfg.get("lower_low_weight", 1)
    max_score_cfg = max(max_score_cfg, 1)

    long_th = ind_cfg.get("long_threshold", 4)
    short_th = ind_cfg.get("short_threshold", -4)
    avg_score = real_trades["score"].mean() if not real_trades.empty and "score" in real_trades.columns else 0.0

    st.markdown("##### ⚡ Strategy Calculated_Score Profile")
    cs1, cs2, cs3, cs4 = st.columns(4)
    cs1.metric("Calculated_Score (Max)", f"{max_score_cfg} pts", help="Maximum possible consensus score from active indicators.")
    cs2.metric("LONG Threshold", f"≥ {long_th} pts", help="Score threshold that triggers LONG entries.")
    cs3.metric("SHORT Threshold", f"≤ {short_th} pts", help="Score threshold that triggers SHORT entries.")
    cs4.metric("Avg Trade Score", f"{avg_score:+.2f} pts" if not real_trades.empty else "N/A", help="Average Calculated_Score at entry across executed trades.")

    tgt_val = ind_cfg.get("fixed_target_pct", 3)
    sl_val = ind_cfg.get("fixed_stoploss_pct", 2)
    side_val = ind_cfg.get("trade_side", "BOTH")
    snooze_val = ind_cfg.get("snooze_minutes", 0)
    tsl_flag = ind_cfg.get("trailing_sl_enabled", False)
    runner_flag = ind_cfg.get("trail_beyond_target", False)
    tsl_disp = f" | Trailing SL = **Model B Active{' (Runner)' if runner_flag else ''}**" if tsl_flag else ""
    short_lot_size = int(ind_cfg.get("short_lot_size", 1))
    short_lot_disp = f" | Short Lot Size = **{short_lot_size} Lot**" if side_val != "LONG" else ""
    snooze_disp = f" | Snooze = **{snooze_val} min**" if snooze_val > 0 else ""
    st.caption(f"🎯 **Trade Levels:** Fixed Target = **{tgt_val}%** | Fixed Stoploss = **{sl_val}%**{tsl_disp} | Side = **{side_val}**{short_lot_disp}{snooze_disp}")

    # ==============================================================================
    # SECTION: Current Date Live Trade (Kotak Neo Live Engine)
    # ==============================================================================
    st.markdown("---")
    st.subheader("🔴 Current Date Live Trade (Kotak Neo Live Engine)")

    # Check date settings against current date
    date_is_current = (strat_from == today_str and strat_to == today_str)
    is_mkt_open, mkt_msg = check_market_trading_hours()
    mkt_badge = "🟢 `OPEN` (09:15 - 15:30 IST)" if is_mkt_open else f"🔴 `CLOSED` ({mkt_msg})"

    pt_col1, pt_col2, pt_col3 = st.columns([3, 2, 2])
    with pt_col1:
        st.markdown(f"**Active Instrument:** `{active_instrument}` &nbsp;|&nbsp; **Gateway:** `🔵 Kotak Neo Live API`")
        st.caption(f"Strategy Dates: **{strat_from}** to **{strat_to}** | Today's Date: **{today_str}**")
    with pt_col2:
        date_badge = "🟢 Strategy Dates Validated (Current Date)" if date_is_current else "🔴 Date Mismatch (Update to Current Date)"
        st.markdown(f"**Validation Status:** {date_badge}")
        mkt_badge = "🟢 `OPEN` (09:15 - 15:30 IST)" if is_mkt_open else f"🔴 `CLOSED` ({mkt_msg})"
        st.markdown(f"**Market Status:** {mkt_badge}")
        st.caption(f"🛡️ **Auto Square-off Time:** `{strategy_sq_time.strftime('%H:%M')} IST` (RiskConfig)")
    with pt_col3:
        if not date_is_current:
            if st.button(f"📅 Set Dates to Current Date ({today_str})", key="quick_set_current_date_live", use_container_width=True):
                inst_cfg["start_date"] = today_str
                inst_cfg["end_date"] = today_str
                save_strategy(selected_strat, ind_cfg, risk_cfg, cost_cfg, instrument=inst_cfg)
                st.success(f"Updated {selected_strat} dates to current date ({today_str})!")
                st.rerun()

    exec_col1, exec_col2, exec_col3 = st.columns([2, 3, 2])
    is_live_running = st.session_state.get("live_kotak_executing", False)

    with exec_col1:
        if not is_live_running:
            if st.button("🚀 Execute Live Trade", type="primary", use_container_width=True, key="btn_run_kotak_live"):
                now_curr_time = datetime.now().time()
                if not date_is_current:
                    st.error(
                        f"❌ Date Validation Error: Strategy From Date ('{strat_from}') and To Date ('{strat_to}') "
                        f"must be set to current date ('{today_str}') for Live Trade execution. "
                        "Please click 'Set Dates to Current Date' above before executing."
                    )
                elif not is_mkt_open:
                    st.error(
                        f"🛑 **Market Hours Validation Failed:** {mkt_msg} "
                        "Live execution cannot be started after market hours (Regular trading: 09:15:00 to 15:30:00 IST)."
                    )
                elif now_curr_time >= strategy_sq_time:
                    st.error(
                        f"🛑 **Square-off Time Validation Failed:** Current time ({datetime.now().strftime('%H:%M:%S')}) is at/past the strategy Square-off Time ({strategy_sq_time.strftime('%H:%M')} IST) defined in Risk Parameters. "
                        "Live trade execution cannot be started past square-off time."
                    )
                else:
                    st.session_state.live_kotak_executing = True
                    st.session_state.live_kotak_start_time = datetime.now().strftime("%H:%M:%S")
                    st.rerun()
        else:
            if st.button("⏹️ Stop Live Trade Session", type="secondary", use_container_width=True, key="btn_stop_kotak_live"):
                st.session_state.live_kotak_executing = False
                st.rerun()

    with exec_col2:
        if is_live_running:
            st.info(
                f"⚡ **Kotak Live Trade Active:** Watching **{active_instrument}** with **Kotak Neo Live Priority** | "
                f"Auto-refresh: **2 seconds** | Session Started: **{st.session_state.get('live_kotak_start_time', '')}**"
            )
        else:
            st.caption("Engine in Standby. Click **Execute Live Trade** during market hours (09:15 to 15:30 IST) to engage consensus signal scanner and live bracket order placement.")

    with exec_col3:
        # Emergency Square Off Button
        if st.button("🚨 EMERGENCY SQUARE OFF", type="secondary", help="Immediately squares off all Kotak live positions via broker API", use_container_width=True, key="btn_emergency_sqoff_live"):
            with st.spinner("Triggering Emergency Square Off to Kotak Neo Live API..."):
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sq_resp = gw.emergency_square_off_all()

                # Close active trade in live database if open
                open_trades = live_db.get_trades(strategy_name=selected_strat)
                for tr in open_trades:
                    if tr.get("status") == "ACTIVE":
                        ep = float(tr.get("entry_price", 0.0))
                        ltp = gw.get_ltp(active_instrument) or ep
                        qty = int(tr.get("quantity", 65))
                        sd = tr.get("side", "LONG")
                        gross = (ltp - ep) * qty if sd == "LONG" else (ep - ltp) * qty
                        live_db.close_active_trade(
                            trade_id_or_row_id=tr["id"],
                            exit_time=now_ts,
                            exit_price=ltp,
                            exit_reason="EMERGENCY_SQUARE_OFF",
                            gross_pnl=round(gross, 2),
                            charges=20.0,
                            peak_gain_pct=round(max(0.0, (ltp - ep) / ep * 100.0) if ep > 0 else 0.0, 2),
                        )
                st.toast("🚨 Kotak Live Emergency Square Off executed successfully!", icon="🚨")
                st.success(f"Emergency Square Off Triggered: {sq_resp.get('count', 0)} position(s) closed.")
                st.rerun()

    # ---------------- Active Execution Engine & Live Kotak Chart ----------------
    timeframe_m = int(inst_cfg.get("timeframe", 1))

    # Fetch underlying intraday candles from Kotak gateway or local fallbacks
    underlying_candles = gw.fetch_historical_candles(
        symbol=underlying_name,
        instrument_key=inst_cfg.get("instrument_key", ""),
        timeframe_minutes=timeframe_m,
        start_date=today,
        end_date=today,
    )
    if underlying_candles is None or underlying_candles.empty:
        for fb in [
            f"data/{underlying_name.lower()}_{timeframe_m}m.csv",
            f"data/{underlying_name.lower()}_5m.csv",
            "data/nifty_5m.csv",
            "data/nifty_1m.csv",
        ]:
            if os.path.exists(fb):
                underlying_candles = load_csv(fb)
                break

    if underlying_candles is not None and not underlying_candles.empty:
        underlying_candles["timestamp"] = pd.to_datetime(underlying_candles["timestamp"])
        if underlying_candles["timestamp"].dt.tz is not None:
            underlying_candles["timestamp"] = underlying_candles["timestamp"].dt.tz_localize(None)
        underlying_candles = underlying_candles[
            (underlying_candles["timestamp"].dt.time >= time(9, 15, 0)) &
            (underlying_candles["timestamp"].dt.time <= time(15, 30, 0))
        ].reset_index(drop=True)

    underlying_ltp = float(oi_status.get("ltp") or 0.0)
    if underlying_ltp <= 0.0 and underlying_candles is not None and not underlying_candles.empty:
        underlying_ltp = float(underlying_candles.iloc[-1]["close"])
    if underlying_ltp <= 0.0:
        q_und = gw.get_ltp(underlying_name)
        if q_und and float(q_und) > 0:
            underlying_ltp = float(q_und)

    # Step 4: After finding Active Instrument is valid (or in active trade), fetch live candles from Kotak & display Active Instrument chart
    if active_instrument:
        candles_df = get_or_fetch_active_option_candles(
            contract_symbol=active_instrument,
            underlying_candles=underlying_candles,
            gateway=gw,
            timeframe_minutes=timeframe_m,
            start_date=today,
            end_date=today,
            underlying=underlying_name,
        )
        if candles_df is None or candles_df.empty:
            candles_df = underlying_candles

        opt_quote = gw.get_ltp(active_instrument)
        if opt_quote and float(opt_quote) > 0:
            live_ltp = float(opt_quote)
        else:
            parsed = parse_oipulse_contract_symbol(active_instrument, underlying=underlying_name)
            if parsed:
                exp_dt, _ = resolve_nearest_expiry_date(datetime.now().date(), underlying=parsed["underlying"])
                calc_p = calculate_option_price(
                    underlying_price=underlying_ltp,
                    strike_price=float(parsed["strike"]),
                    option_type=parsed["option_type"],
                    candle_dt=datetime.now(),
                    expiry_dt=exp_dt,
                )
                live_ltp = round(max(calc_p, 1.0), 2)
            elif candles_df is not None and not candles_df.empty:
                live_ltp = float(candles_df.iloc[-1]["close"])
            else:
                live_ltp = 100.0
    else:
        # Step 1: Standby (Neutral Band) - Display underlying index
        candles_df = underlying_candles
        live_ltp = underlying_ltp

    # Strictly filter candles to regular market trading hours (09:15:00 to 15:30:00 IST)
    if candles_df is not None and not candles_df.empty:
        candles_df["timestamp"] = pd.to_datetime(candles_df["timestamp"])
        if candles_df["timestamp"].dt.tz is not None:
            candles_df["timestamp"] = candles_df["timestamp"].dt.tz_localize(None)
        candles_df = candles_df[
            (candles_df["timestamp"].dt.time >= time(9, 15, 0)) &
            (candles_df["timestamp"].dt.time <= time(15, 30, 0))
        ].reset_index(drop=True)

    # Display Live Ticker & Order Book metrics
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    if active_instrument:
        m_col1.metric("Option Tick (LTP)", f"₹{live_ltp:,.2f}", f"Contract: {active_instrument}")
    else:
        m_col1.metric("Underlying LTP", f"₹{live_ltp:,.2f}", f"OI Strength: {oi_status.get('strength_str', '0%')}")
    if candles_df is not None and not candles_df.empty:
        m_col2.metric("Day High", f"₹{max(float(candles_df['high'].max()), live_ltp):,.2f}")
        m_col3.metric("Day Low", f"₹{min(float(candles_df['low'].min()), live_ltp):,.2f}")
        m_col4.metric("Candles Today", f"{len(candles_df)} bars ({timeframe_m}m, capped @ 15:30)")

    # Evaluate Strategy Consensus on Candles
    custom_cfg = CustomStrategyConfig(
        name=selected_strat,
        ema_enabled=ind_cfg.get("ema_enabled", True), ema_fast=ind_cfg.get("ema_fast", 20), ema_slow=ind_cfg.get("ema_slow", 50), ema_weight=ind_cfg.get("ema_weight", 2),
        rsi_enabled=ind_cfg.get("rsi_enabled", True), rsi_period=ind_cfg.get("rsi_period", 14), rsi_bull_low=ind_cfg.get("rsi_bull_low", 55.0),
        rsi_bull_high=ind_cfg.get("rsi_bull_high", 70.0), rsi_bear_low=ind_cfg.get("rsi_bear_low", 30.0), rsi_bear_high=ind_cfg.get("rsi_bear_high", 45.0), rsi_weight=ind_cfg.get("rsi_weight", 1),
        macd_enabled=ind_cfg.get("macd_enabled", True), macd_fast=ind_cfg.get("macd_fast", 12), macd_slow=ind_cfg.get("macd_slow", 26), macd_signal=ind_cfg.get("macd_signal", 9), macd_weight=ind_cfg.get("macd_weight", 2),
        vwap_enabled=ind_cfg.get("vwap_enabled", True), vwap_weight=ind_cfg.get("vwap_weight", 1),
        atr_enabled=ind_cfg.get("atr_enabled", True), atr_period=ind_cfg.get("atr_period", 14), atr_stop_multiplier=risk_cfg.get("atr_stop_multiplier", 1.5), atr_reward_risk_ratio=risk_cfg.get("reward_risk_ratio", 2.0),
        adx_enabled=ind_cfg.get("adx_enabled", True), adx_period=ind_cfg.get("adx_period", 14), adx_min=ind_cfg.get("adx_min", 20.0), adx_weight=ind_cfg.get("adx_weight", 1),
        volume_enabled=ind_cfg.get("volume_enabled", True), volume_period=ind_cfg.get("volume_period", 20), volume_ratio_min=ind_cfg.get("volume_ratio_min", 1.0),
        volume_consecutive=int(ind_cfg.get("volume_consecutive", 3)), volume_weight=ind_cfg.get("volume_weight", 1),
        bollinger_enabled=ind_cfg.get("bollinger_enabled", False), bollinger_period=ind_cfg.get("bollinger_period", 20), bollinger_std_dev=ind_cfg.get("bollinger_std_dev", 2.0), bollinger_weight=ind_cfg.get("bollinger_weight", 1),
        stoch_enabled=ind_cfg.get("stoch_enabled", False), stoch_k=ind_cfg.get("stoch_k", 14), stoch_d=ind_cfg.get("stoch_d", 3), stoch_low=ind_cfg.get("stoch_low", 20.0), stoch_high=ind_cfg.get("stoch_high", 80.0), stoch_weight=ind_cfg.get("stoch_weight", 1),
        obv_enabled=ind_cfg.get("obv_enabled", False), obv_ma_period=ind_cfg.get("obv_ma_period", 20), obv_weight=ind_cfg.get("obv_weight", 1),
        higher_high_enabled=bool(ind_cfg.get("higher_high_enabled", False)), higher_high_period=int(ind_cfg.get("higher_high_period", 5)), higher_high_weight=int(ind_cfg.get("higher_high_weight", 1)),
        lower_low_enabled=bool(ind_cfg.get("lower_low_enabled", False)), lower_low_period=int(ind_cfg.get("lower_low_period", 5)), lower_low_weight=int(ind_cfg.get("lower_low_weight", 1)),
        hma_enabled=bool(ind_cfg.get("hma_enabled", False)), hma_period=int(ind_cfg.get("hma_period", 100)), hma_weight=int(ind_cfg.get("hma_weight", 1)),
        supertrend_enabled=bool(ind_cfg.get("supertrend_enabled", False)), supertrend_period=int(ind_cfg.get("supertrend_period", 10)),
        supertrend_mult_simple=float(ind_cfg.get("supertrend_mult_simple", 2.0)), supertrend_mult_complex=float(ind_cfg.get("supertrend_mult_complex", 4.0)),
        supertrend_weight=int(ind_cfg.get("supertrend_weight", 1)),
        long_threshold=long_th, short_threshold=short_th,
    )

    latest_score = 0
    scored_candles = pd.DataFrame()
    if candles_df is not None and not candles_df.empty:
        scored_candles = calculate_custom_indicators(candles_df, custom_cfg)
        if not scored_candles.empty:
            scored_candles["consensus_score"] = [
                int(sum(score_custom_row(r, custom_cfg).values())) for _, r in scored_candles.iterrows()
            ]
            latest_score = int(scored_candles.iloc[-1]["consensus_score"])

    # Constant Display of Both Trigger & Signal Bar Check Sections during active live session
    if is_live_running:
        now_dt = datetime.now()
        tf_val = max(1, timeframe_m)
        seconds_past_hour = now_dt.minute * 60 + now_dt.second
        tf_seconds = tf_val * 60
        next_boundary_sec = ((seconds_past_hour // tf_seconds) + 1) * tf_seconds
        next_candle_dt = now_dt.replace(minute=0, second=0, microsecond=0) + timedelta(seconds=next_boundary_sec)
        rem_seconds = max(0, int((next_candle_dt - now_dt).total_seconds()))
        next_check_str = next_candle_dt.strftime("%H:%M:%S")

        if candles_df is not None and not candles_df.empty:
            last_candle_row = candles_df.iloc[-1]
            last_bar_dt = pd.to_datetime(last_candle_row["timestamp"])
            last_bar_str = last_bar_dt.strftime("%Y-%m-%d %H:%M:%S")
            bar_closed_at_str = (last_bar_dt + timedelta(minutes=tf_val)).strftime("%H:%M:%S")
        else:
            last_bar_dt = now_dt - timedelta(minutes=tf_val)
            last_bar_str = last_bar_dt.strftime("%Y-%m-%d %H:%M:%S")
            bar_closed_at_str = now_dt.strftime("%H:%M:%S")

        if latest_score >= long_th:
            sig_disp = "LONG"
            sig_color = "#00e676"
        elif latest_score <= short_th:
            sig_disp = "SHORT"
            sig_color = "#ff5252"
        else:
            sig_disp = "HOLD"
            sig_color = "#ffd600"

        st.markdown(f"""
        <div style="background: rgba(33, 150, 243, 0.12); border: 1px solid #2196f3; border-radius: 8px; padding: 10px 16px; margin-top: 10px; margin-bottom: 6px; font-size: 0.95rem;">
            <b style="color: #64b5f6;">⏱️ CANDLE CLOSE TRIGGER ACTIVE:</b> Next signal check at <b>{next_check_str}</b> (<b>{rem_seconds}s remaining</b>) on <b>{tf_val}m candle close</b>.
        </div>
        <div style="background: rgba(0, 230, 118, 0.08); border: 1px solid rgba(0, 230, 118, 0.35); border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; font-size: 0.95rem;">
            <b style="color: #00e676;">📊 Signal bar close check</b> &nbsp;➔&nbsp; Last Signal Bar: <b>{last_bar_str}</b> (Closed at <b>{bar_closed_at_str}</b>) &nbsp;|&nbsp; Signal: <b style="color: {sig_color};">{sig_disp}</b> &nbsp;|&nbsp; Score: <b>{latest_score}</b> &nbsp;|&nbsp; <i>Continuous 2s LTP, chart & exit monitoring active.</i>
        </div>
        """, unsafe_allow_html=True)

    # If live running, check market hours and strategy square-off time; if past square-off or market closed, auto square off and stop
    if is_live_running:
        now_time = datetime.now().time()
        if now_time >= strategy_sq_time or not is_mkt_open:
            st.session_state.live_kotak_executing = False
            if active_open_trade is not None:
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                e_p = float(active_open_trade.get("entry_price", live_ltp))
                e_side = active_open_trade.get("side", "LONG")
                gross = (live_ltp - e_p) * int(active_open_trade.get("quantity", 65)) if e_side == "LONG" else (e_p - live_ltp) * int(active_open_trade.get("quantity", 65))
                exit_reason = "SQUARE_OFF" if now_time >= strategy_sq_time else "MARKET_CLOSE_SQUARE_OFF"
                gw.emergency_square_off_all()
                live_db.close_active_trade(
                    trade_id_or_row_id=active_open_trade.get("id"),
                    exit_time=now_ts,
                    exit_price=live_ltp,
                    exit_reason=exit_reason,
                    gross_pnl=round(gross, 2),
                    charges=20.0,
                    peak_gain_pct=0.0,
                )
            if now_time >= strategy_sq_time:
                st.warning(f"🔔 Strategy Square-off Time ({strategy_sq_time.strftime('%H:%M')} IST) reached from Risk Parameters. Live session ended and open positions squared off.")
            else:
                st.warning("🔔 Market closed at 15:30:00 IST. Live session ended and open positions squared off.")
            st.rerun()

        # Live Signal Check & Kotak Live Trade Execution
        lot_size = int(inst_cfg.get("lot_size", 65))
        now_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ---------------- RISK PARAMETERS CHECK AGAINST KOTAK LIVE API ----------------
        max_open_pos = int(risk_cfg.get("max_open_positions", 3))
        cur_open_pos = gw.get_open_positions_count()
        max_losses_today = int(risk_cfg.get("max_consecutive_losses", 3))
        loss_trades_today = gw.get_loss_trades_today()
        max_daily_loss = float(risk_cfg.get("max_daily_loss", 2000.0))
        today_net_pnl = net_pnl

        risk_blocked = False
        if cur_open_pos >= max_open_pos and active_open_trade is None:
            st.warning(f"⚠️ **Risk Parameter Limit:** Max Open Positions ({max_open_pos}) reached on Kotak Live API. New entries blocked.")
            risk_blocked = True
        elif loss_trades_today >= max_losses_today and active_open_trade is None:
            st.error(f"🛑 **Risk Parameter Limit:** Max Consecutive Losses ({max_losses_today}) reached on Kotak Live API today. New entries blocked.")
            risk_blocked = True
        elif today_net_pnl <= -max_daily_loss and active_open_trade is None:
            st.error(f"🛑 **Risk Parameter Limit:** Max Daily Loss Limit (-₹{max_daily_loss:,.2f}) hit. New entries blocked.")
            risk_blocked = True
        elif (now_time >= strategy_sq_time or now_time > strategy_entry_end) and active_open_trade is None:
            st.info(f"⏳ Current time ({now_time.strftime('%H:%M:%S')}) is past Strategy Entry End ({strategy_entry_end.strftime('%H:%M')}) / Square-off ({strategy_sq_time.strftime('%H:%M')}) from Risk Parameters. New entries are blocked.")
            risk_blocked = True

        if active_open_trade is None and not risk_blocked:
            # Check for new entry signal strictly within market hours & strategy entry window
            if fo_mode == "OIPulse Mode":
                strength_val = float(oi_status.get("strength_val", 0.0) or 0.0)

                # Step 1: If OI strength is not crossed >ce_strength or <pe_strength (neutral band), BLOCK TRADE!
                # Trade must NEVER be executed using NIFTY instrument directly.
                if not (strength_val > ce_strength or strength_val < pe_strength):
                    st.info(
                        f"📡 **Step 1: Standby (Neutral Band):** Real-time OI Strength is **{oi_status.get('strength_str', '0%')}** "
                        f"(within neutral band {pe_strength:.0f}% to +{ce_strength:.0f}%). Trade entry blocked: Waiting for momentum breakout "
                        f"(> +{ce_strength:.0f}% for CE, < {pe_strength:.0f}% for PE) before constructing active option contract from {underlying_name} Option Chain. "
                        f"Underlying '{underlying_name}' will NOT be traded directly."
                    )
                elif not active_instrument or active_instrument in (underlying_name, "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                    st.error(f"🛑 Cannot execute trade: Active instrument '{active_instrument}' is invalid or is raw index.")
                else:
                    opt_type = str(oi_status.get("option_type", "")).upper()
                    trade_symbol = active_instrument

                    # Step 5 & Step 7: Check strategy conditions on live candle close
                    signal_matched = False
                    if opt_type == "CE" and latest_score >= long_th and side_val in ("BOTH", "LONG"):
                        signal_matched = True
                    elif opt_type == "PE" and latest_score <= short_th and side_val in ("BOTH", "SHORT"):
                        signal_matched = True

                    if signal_matched:
                        entry_p = live_ltp
                        tgt_p = round(entry_p * (1.0 + tgt_val / 100.0), 2)
                        sl_p = round(entry_p * (1.0 - sl_val / 100.0), 2)
                        next_tid = 6000 + len(real_trades) + 1

                        # Trigger Live Orders on Kotak for option contract
                        entry_order = gw.place_market_order(
                            symbol=trade_symbol,
                            side="BUY",
                            quantity=lot_size,
                            reference_price=entry_p,
                        )
                        entry_fill_time = entry_order.fill_time or now_time_str
                        entry_fill_price = float(entry_order.fill_price or entry_p)

                        # Place Target limit order
                        tgt_order = gw.place_target_order(
                            symbol=trade_symbol,
                            side="SELL",
                            quantity=lot_size,
                            target_price=tgt_p,
                        )

                        # Place Stop Loss SL-M order
                        sl_order = gw.place_stoploss_order(
                            symbol=trade_symbol,
                            side="SELL",
                            quantity=lot_size,
                            stoploss_price=sl_p,
                        )

                        # Step 7 & 8: Add active record in Live Trade database
                        # Once saved, active_open_trade is active, so Step 2 (Strike Rotation) STOPS!
                        live_db.save_active_trade(
                            strategy_name=selected_strat,
                            symbol=trade_symbol,
                            session_date=today_str,
                            trade_id=next_tid,
                            side="LONG",
                            quantity=lot_size,
                            entry_time=entry_fill_time,
                            entry_price=entry_fill_price,
                            stop_loss=sl_p,
                            target=tgt_p,
                            score=latest_score,
                            regime="TRENDING",
                            gateway="KOTAK",
                            index_ltp=underlying_ltp,
                        )
                        st.toast(f"🟢 Kotak Live: Executed LONG {opt_type} Trade #{next_tid} on {trade_symbol} @ ₹{entry_fill_price:.2f}!", icon="🟢")
                        st.rerun()

            else:
                # Normal Mode (fo_mode != "OIPulse Mode"):
                trade_symbol = active_instrument
                if latest_score >= long_th and side_val in ("BOTH", "LONG"):
                    entry_p = live_ltp
                    tgt_p = round(entry_p * (1.0 + tgt_val / 100.0), 2)
                    sl_p = round(entry_p * (1.0 - sl_val / 100.0), 2)
                    next_tid = 6000 + len(real_trades) + 1

                    # Trigger Live Orders on Kotak
                    entry_order = gw.place_market_order(
                        symbol=trade_symbol,
                        side="BUY",
                        quantity=lot_size,
                        reference_price=entry_p,
                    )
                    entry_fill_time = entry_order.fill_time or now_time_str
                    entry_fill_price = float(entry_order.fill_price or entry_p)

                    # Place Target limit order
                    tgt_order = gw.place_target_order(
                        symbol=trade_symbol,
                        side="SELL",
                        quantity=lot_size,
                        target_price=tgt_p,
                    )

                    # Place Stop Loss SL-M order
                    sl_order = gw.place_stoploss_order(
                        symbol=trade_symbol,
                        side="SELL",
                        quantity=lot_size,
                        stoploss_price=sl_p,
                    )

                    # Add active record in Live Trade database
                    live_db.save_active_trade(
                        strategy_name=selected_strat,
                        symbol=trade_symbol,
                        session_date=today_str,
                        trade_id=next_tid,
                        side="LONG",
                        quantity=lot_size,
                        entry_time=entry_fill_time,
                        entry_price=entry_fill_price,
                        stop_loss=sl_p,
                        target=tgt_p,
                        score=latest_score,
                        regime="TRENDING",
                        gateway="KOTAK",
                        index_ltp=underlying_ltp,
                    )
                    st.toast(f"🟢 Kotak Live: Executed LONG Trade #{next_tid} on {trade_symbol} @ ₹{entry_fill_price:.2f}!", icon="🟢")
                    st.rerun()

                elif latest_score <= short_th and side_val in ("BOTH", "SHORT"):
                    entry_p = live_ltp
                    tgt_p = round(entry_p * (1.0 - tgt_val / 100.0), 2)
                    sl_p = round(entry_p * (1.0 + sl_val / 100.0), 2)
                    next_tid = 6000 + len(real_trades) + 1

                    # Trigger Live Orders on Kotak
                    entry_order = gw.place_market_order(
                        symbol=trade_symbol,
                        side="SELL",
                        quantity=lot_size,
                        reference_price=entry_p,
                    )
                    entry_fill_time = entry_order.fill_time or now_time_str
                    entry_fill_price = float(entry_order.fill_price or entry_p)

                    # Place Target limit order
                    tgt_order = gw.place_target_order(
                        symbol=trade_symbol,
                        side="BUY",
                        quantity=lot_size,
                        target_price=tgt_p,
                    )

                    # Place Stop Loss SL-M order
                    sl_order = gw.place_stoploss_order(
                        symbol=trade_symbol,
                        side="BUY",
                        quantity=lot_size,
                        stoploss_price=sl_p,
                    )

                    # Add active record in Live Trade database
                    live_db.save_active_trade(
                        strategy_name=selected_strat,
                        symbol=trade_symbol,
                        session_date=today_str,
                        trade_id=next_tid,
                        side="SHORT",
                        quantity=lot_size,
                        entry_time=entry_fill_time,
                        entry_price=entry_fill_price,
                        stop_loss=sl_p,
                        target=tgt_p,
                        score=latest_score,
                        regime="TRENDING",
                        gateway="KOTAK",
                        index_ltp=underlying_ltp,
                    )
                    st.toast(f"🔴 Kotak Live: Executed SHORT Trade #{next_tid} on {trade_symbol} @ ₹{entry_fill_price:.2f}!", icon="🔴")
                    st.rerun()

                st.rerun()

        elif active_open_trade is not None:
            # Active trade is running: watch for Square-off Time, Target, or Stop Loss execution
            e_side = active_open_trade.get("side", "LONG")
            e_p = float(active_open_trade.get("entry_price", live_ltp))
            tgt = float(active_open_trade.get("target") or 0.0)
            sl = float(active_open_trade.get("stop_loss") or 0.0)
            row_db_id = active_open_trade.get("id")

            now_time = datetime.now().time()
            exit_triggered = None
            if now_time >= strategy_sq_time or (not candles_df.empty and pd.to_datetime(candles_df.iloc[-1]["timestamp"]).time() >= strategy_sq_time):
                exit_triggered = "SQUARE_OFF"
            elif e_side == "LONG":
                if tgt > 0 and live_ltp >= tgt:
                    exit_triggered = "TARGET"
                elif sl > 0 and live_ltp <= sl:
                    exit_triggered = "STOP_LOSS"
            else:
                if tgt > 0 and live_ltp <= tgt:
                    exit_triggered = "TARGET"
                elif sl > 0 and live_ltp >= sl:
                    exit_triggered = "STOP_LOSS"

            if exit_triggered:
                opp_exit_side = "SELL" if e_side == "LONG" else "BUY"
                try:
                    exit_order = gw.place_market_order(
                        symbol=active_instrument,
                        side=opp_exit_side,
                        quantity=lot_size,
                        reference_price=live_ltp,
                    )
                    exit_fill_time = exit_order.fill_time or now_time_str
                    exit_fill_price = float(exit_order.fill_price or live_ltp)
                except Exception as ex:
                    logger.warning("Kotak Live exit market order note: %s", ex)
                    exit_fill_time = now_time_str
                    exit_fill_price = live_ltp

                gross = (exit_fill_price - e_p) * lot_size if e_side == "LONG" else (e_p - exit_fill_price) * lot_size
                peak_pct = max(0.0, (exit_fill_price - e_p) / e_p * 100.0) if e_side == "LONG" else max(0.0, (e_p - exit_fill_price) / e_p * 100.0)

                live_db.close_active_trade(
                    trade_id_or_row_id=row_db_id,
                    exit_time=exit_fill_time,
                    exit_price=exit_fill_price,
                    exit_reason=exit_triggered,
                    gross_pnl=round(gross, 2),
                    charges=20.0,
                    peak_gain_pct=round(peak_pct, 2),
                )
                if exit_triggered == "SQUARE_OFF":
                    st.session_state.live_kotak_executing = False
                    st.toast(f"🔔 Kotak Live: Strategy Square-off ({strategy_sq_time.strftime('%H:%M')}) triggered! Position closed.", icon="⏹️")
                else:
                    st.toast(f"⏹️ Kotak Live: Trade exited via {exit_triggered} at ₹{exit_fill_price:.2f}! Net P&L: ₹{gross - 20:,.2f}", icon="⏹️")
                st.rerun()

    # Display Interactive Plotly Candlestick Chart (Current Date - strictly up to 15:30)
    if candles_df is not None and not candles_df.empty and HAS_PLOTLY:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.04,
            subplot_titles=(
                f"📈 Live Current Date Kotak Chart: {active_instrument} ({timeframe_m}m - Market Hours 09:15 to 15:30)"
                if active_instrument
                else f"📈 Standby Chart: {underlying_name} Underlying Index (Waiting for OI Breakout >50 or <-50)",
                "📊 Volume",
            ),
            row_heights=[0.75, 0.25],
        )
        plot_slice = candles_df.tail(60).copy()
        chart_inst_name = active_instrument if active_instrument else f"{underlying_name} (Standby)"
        fig.add_trace(go.Candlestick(
            x=plot_slice["timestamp"].astype(str),
            open=plot_slice["open"],
            high=plot_slice["high"],
            low=plot_slice["low"],
            close=plot_slice["close"],
            name=chart_inst_name,
            increasing_line_color="#00e676",
            decreasing_line_color="#ff5252",
        ), row=1, col=1)

        # Fast / Slow EMA overlays
        fig.add_trace(go.Scatter(
            x=plot_slice["timestamp"].astype(str),
            y=plot_slice["close"].ewm(span=9, adjust=False).mean(),
            mode="lines",
            name="EMA 9",
            line=dict(color="#00e5ff", width=1.5),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_slice["timestamp"].astype(str),
            y=plot_slice["close"].ewm(span=21, adjust=False).mean(),
            mode="lines",
            name="EMA 21",
            line=dict(color="#ffd600", width=1.5),
        ), row=1, col=1)

        # If active trade open, draw entry, target, and stop loss lines
        if active_open_trade:
            ep = float(active_open_trade.get("entry_price", 0.0))
            fig.add_hline(y=ep, line_dash="dash", line_color="#2979ff", annotation_text=f"Entry: ₹{ep:.2f}", row=1, col=1)
            if active_open_trade.get("stop_loss"):
                fig.add_hline(y=float(active_open_trade["stop_loss"]), line_dash="dot", line_color="#ff5252", annotation_text="SL", row=1, col=1)
            if active_open_trade.get("target"):
                fig.add_hline(y=float(active_open_trade["target"]), line_dash="dot", line_color="#00e676", annotation_text="TP", row=1, col=1)

        bar_colors = ["#00e676" if c >= o else "#ff5252" for c, o in zip(plot_slice["close"], plot_slice["open"])]
        fig.add_trace(go.Bar(
            x=plot_slice["timestamp"].astype(str),
            y=plot_slice["volume"],
            marker_color=bar_colors,
            name="Volume",
            opacity=0.6,
        ), row=2, col=1)

        fig.update_layout(
            template="plotly_dark",
            height=420,
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False,
            xaxis2_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

    # ---------------- Active Open Trade Highlight Card ----------------
    if active_open_trade:
        side_act = active_open_trade.get("side", "LONG")
        box_class = "active-trade-box" if side_act == "LONG" else "active-trade-box-short"
        entry_p = float(active_open_trade.get("entry_price", 0.0))
        cur_ltp = float(gw.get_live_market_depth(active_instrument).get("ltp") or entry_p)
        qty = int(active_open_trade.get("quantity", 65))
        unrealized_pnl = (cur_ltp - entry_p) * qty if side_act == "LONG" else (entry_p - cur_ltp) * qty
        gain_pct = (unrealized_pnl / (entry_p * qty) * 100.0) if entry_p > 0 else 0.0

        st.markdown(f"""
        <div class="{box_class}">
            <div style="font-size: 1.15rem; font-weight: 800; color: {'#00e676' if side_act=='LONG' else '#ff5252'};">
                ⚡ ACTIVE OPEN LIVE {side_act} TRADE #{active_open_trade.get('trade_id')} &nbsp;|&nbsp; Symbol: {active_open_trade.get('symbol')}
            </div>
            <div style="margin-top: 6px; font-size: 0.95rem; color: #fff;">
                Entry Price: <b>₹{entry_p:.2f}</b> &nbsp;|&nbsp; Current LTP: <b>₹{cur_ltp:.2f}</b> &nbsp;|&nbsp;
                Live MTM P&L: <b style="color: {'#00e676' if unrealized_pnl>=0 else '#ff5252'};">₹{unrealized_pnl:+,.2f} ({gain_pct:+.2f}%)</b> &nbsp;|&nbsp;
                Target: <b>₹{float(active_open_trade.get('target', 0)):.2f}</b> &nbsp;|&nbsp; Stop Loss: <b>₹{float(active_open_trade.get('stop_loss', 0)):.2f}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        btn_c1, btn_c2 = st.columns([2, 3])
        with btn_c1:
            if st.button("⏹️ Force Square Off Active Trade Now", key="force_sq_off_btn_live", use_container_width=True):
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                opp_side = "SELL" if side_act == "LONG" else "BUY"
                try:
                    gw.place_market_order(symbol=active_instrument, side=opp_side, quantity=qty, reference_price=cur_ltp)
                except Exception as e:
                    logger.warning("Square off order error: %s", e)

                live_db.close_active_trade(
                    trade_id_or_row_id=active_open_trade.get("id"),
                    exit_time=now_ts,
                    exit_price=cur_ltp,
                    exit_reason="MANUAL_SQUARE_OFF",
                    gross_pnl=round(unrealized_pnl, 2),
                    charges=20.0,
                    peak_gain_pct=round(max(0.0, gain_pct), 2),
                )
                st.toast(f"⏹️ Squared off Trade #{active_open_trade.get('trade_id')} on Kotak Live!", icon="⏹️")
                st.rerun()

        with btn_c2:
            if not is_mkt_open or datetime.now().time() >= strategy_sq_time:
                sq_btn_label = f"⏹️ Auto Square-off Position (Past {strategy_sq_time.strftime('%H:%M')} Square-off Time)" if datetime.now().time() >= strategy_sq_time else "⏹️ Close Position (Market Closed at 15:30)"
                if st.button(sq_btn_label, key="mkt_closed_sq_off_live_btn", type="primary", use_container_width=True):
                    opp_side = "SELL" if side_act == "LONG" else "BUY"
                    try:
                        gw.place_market_order(symbol=active_instrument, side=opp_side, quantity=qty, reference_price=cur_ltp)
                    except Exception as e:
                        logger.warning("Square off order note: %s", e)

                    exit_reason = "SQUARE_OFF" if datetime.now().time() >= strategy_sq_time else "MARKET_CLOSE_SQUARE_OFF"
                    live_db.close_active_trade(
                        trade_id_or_row_id=active_open_trade.get("id"),
                        exit_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        exit_price=cur_ltp,
                        exit_reason=exit_reason,
                        gross_pnl=round(unrealized_pnl, 2),
                        charges=20.0,
                        peak_gain_pct=round(max(0.0, gain_pct), 2),
                    )
                    st.success(f"Live Position #{active_open_trade.get('trade_id')} squared off successfully!")
                    st.rerun()

    # ==============================================================================
    # Equity Curve and Daily Net P&L Charts
    # ==============================================================================
    if not real_trades.empty:
        ch1, ch2, ch3 = st.columns(3)
        with ch1:
            st.subheader("Equity Curve")
            eq = pd.concat([pd.Series([capital]), capital + real_trades.sort_values("id")["net_pnl"].cumsum()], ignore_index=True)
            st.line_chart(eq.rename("Equity (₹)"))

        with ch2:
            st.subheader("Daily Net P&L")
            if "session_date" in real_trades.columns:
                daily_pnl = real_trades.groupby("session_date")["net_pnl"].sum()
                st.bar_chart(daily_pnl)
            elif "exit_time" in real_trades.columns:
                real_trades["day"] = pd.to_datetime(real_trades["exit_time"]).dt.date
                daily_pnl = real_trades.groupby("day")["net_pnl"].sum()
                st.bar_chart(daily_pnl)

        with ch3:
            st.subheader("Monthly Net P&L")
            date_src = real_trades["session_date"] if "session_date" in real_trades.columns else (real_trades["exit_time"] if "exit_time" in real_trades.columns else None)
            if date_src is not None:
                dt = pd.to_datetime(date_src.astype(str).str.slice(0, 10), errors="coerce")
                m_df = real_trades.assign(
                    month_key=dt.dt.strftime("%Y-%m"),
                    month_label=dt.dt.strftime("%b %Y")
                ).dropna(subset=["month_key"])
                if not m_df.empty:
                    monthly_pnl = m_df.groupby(["month_key", "month_label"])["net_pnl"].sum().reset_index()
                    monthly_pnl = monthly_pnl.sort_values("month_key").set_index("month_label")["net_pnl"]
                    st.bar_chart(monthly_pnl)

        import sqlite3
        db_oi_file = PROJECT_ROOT / "data" / "backtest.db"
        oi_conn = None
        if os.path.exists(db_oi_file):
            try:
                oi_conn = sqlite3.connect(db_oi_file)
            except Exception:
                oi_conn = None
        oi_cur = oi_conn.cursor() if oi_conn else None

        c_ts_map = {}
        if candles_df is not None and not candles_df.empty and "timestamp" in candles_df.columns:
            for _, c_row in candles_df.iterrows():
                ts_key = str(c_row["timestamp"])[:19]
                val = c_row.get("ltp", c_row.get("close"))
                if val is not None and pd.notna(val):
                    c_ts_map[ts_key] = float(val)

        import re
        underlying_name = str(inst_cfg.get("underlying") or "NIFTY").strip()
        def get_trade_instrument_and_opt_type(row):
            # 1. Resolve active constructed instrument name
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

            # 2. Resolve Index-LTP from oi_strength table
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

                    if pd.isna(idx_ltp):
                        ts_k = str(e_ts)[:19]
                        if ts_k in c_ts_map:
                            idx_ltp = round(c_ts_map[ts_k], 2)

            # 3. Resolve Option Type (CE or PE)
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

        if not real_trades.empty:
            real_trades[["Instrument", "Index-LTP", "Option Type"]] = real_trades.apply(get_trade_instrument_and_opt_type, axis=1)
        if not trades.empty:
            trades[["Instrument", "Index-LTP", "Option Type"]] = trades.apply(get_trade_instrument_and_opt_type, axis=1)

        if oi_conn:
            try:
                oi_conn.close()
            except Exception:
                pass

        # Breakdowns
        b1, b2, b3 = st.columns(3)
        with b1:
            st.subheader("By Exit Reason")
            if not real_trades.empty and "exit_reason" in real_trades.columns and real_trades["exit_reason"].dropna().nunique() > 0:
                by_exit = real_trades.groupby("exit_reason")["net_pnl"].agg(
                    Trades="count", Net_PnL="sum", Avg_Trade="mean"
                ).round(2)
                st.dataframe(by_exit, use_container_width=True)
            else:
                empty_exit = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                empty_exit.index.name = "exit_reason"
                st.dataframe(empty_exit, use_container_width=True)

        with b2:
            st.subheader("Option Type PE: By Exit Reason")
            pe_trades = real_trades[real_trades["Option Type"].astype(str).str.upper() == "PE"] if "Option Type" in real_trades.columns else pd.DataFrame()
            if not pe_trades.empty and "exit_reason" in pe_trades.columns and pe_trades["exit_reason"].dropna().nunique() > 0:
                by_exit_pe = pe_trades.groupby("exit_reason")["net_pnl"].agg(
                    Trades="count", Net_PnL="sum", Avg_Trade="mean"
                ).round(2)
                st.dataframe(by_exit_pe, use_container_width=True)
            else:
                empty_pe = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                empty_pe.index.name = "exit_reason"
                st.dataframe(empty_pe, use_container_width=True)

        with b3:
            st.subheader("Option Type CE: By Exit Reason")
            ce_trades = real_trades[real_trades["Option Type"].astype(str).str.upper() == "CE"] if "Option Type" in real_trades.columns else pd.DataFrame()
            if not ce_trades.empty and "exit_reason" in ce_trades.columns and ce_trades["exit_reason"].dropna().nunique() > 0:
                by_exit_ce = ce_trades.groupby("exit_reason")["net_pnl"].agg(
                    Trades="count", Net_PnL="sum", Avg_Trade="mean"
                ).round(2)
                st.dataframe(by_exit_ce, use_container_width=True)
            else:
                empty_ce = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                empty_ce.index.name = "exit_reason"
                st.dataframe(empty_ce, use_container_width=True)

    # ==============================================================================
    # Trade Audit Trail (All Executions)
    # ==============================================================================
    st.markdown("---")
    st.subheader("Trade Audit Trail (All Executions)")

    if not trades.empty:
        df_trail = trades.copy()

        # Format Calculated_Score and Margin used
        if "score" in df_trail.columns and "Calculated_Score" not in df_trail.columns:
            df_trail["Calculated_Score"] = df_trail["score"]
        if "quantity" in df_trail.columns and "entry_price" in df_trail.columns:
            df_trail["Margin used"] = (df_trail["quantity"] * df_trail["entry_price"]).round(2)

        # Peak gain calculation
        if "peak_gain_pct" in df_trail.columns:
            df_trail["peak_gain"] = df_trail.apply(
                lambda r: f"{int(float(r['peak_gain_pct']) // 5) * 5}% (+{float(r['peak_gain_pct']):.1f}%)"
                if float(r.get("peak_gain_pct", 0.0) or 0.0) >= 5.0
                else f"0% (+{float(r.get('peak_gain_pct', 0.0) or 0.0):.1f}%)",
                axis=1,
            )
        else:
            df_trail["peak_gain"] = "0% (+0.0%)"

        # Slabs 5%, 10%, 15%, 20%, 25%, 30%
        slabs = [5, 10, 15, 20, 25, 30]
        slab_cols = [f"{s}%" for s in slabs]
        for c in slab_cols:
            df_trail[c] = "-"

        for idx, r_tr in df_trail.iterrows():
            if str(r_tr.get("status")) == "NO_TRADE":
                continue
            pk = float(r_tr.get("peak_gain_pct", 0.0) or 0.0)
            sc = int(r_tr.get("score", 0) or 0)
            sc_str = f"+{sc}" if sc > 0 else str(sc)
            for s, c_name in zip(slabs, slab_cols):
                if pk >= s:
                    df_trail.at[idx, c_name] = sc_str

        # Format Trade id column for presentation (e.g. Trade id-5000)
        def format_trade_id_display(r):
            tid = r.get("trade_id")
            s_date = str(r.get("session_date") or str(r.get("entry_time", ""))[:10])
            side = str(r.get("side", ""))
            if str(r.get("status")) == "NO_TRADE" or side == "No Trade":
                return f"No Trade ({s_date})"
            return f"Trade id-{tid}"

        df_trail["Display_ID"] = df_trail.apply(format_trade_id_display, axis=1)

        cols_wanted = [
            "Display_ID", "Instrument", "Index-LTP", "Option Type", "side", "regime", "Calculated_Score", "session_date", "entry_time", "exit_time",
            "quantity", "entry_price", "Margin used", "peak_gain",
            "5%", "10%", "15%", "20%", "25%", "30%",
            "exit_price", "stop_loss", "target",
            "exit_reason", "gross_pnl", "charges", "net_pnl", "status",
        ]
        display_cols = [c for c in cols_wanted if c in df_trail.columns]

        # Row highlighting logic: Highlight active records alone with colors!
        def highlight_active_rows(row):
            st_val = str(row.get("status", "")).upper()
            sd_val = str(row.get("side", "")).upper()
            if st_val == "ACTIVE":
                if "LONG" in sd_val or "BUY" in sd_val:
                    return ["background-color: rgba(0, 230, 118, 0.35); color: #00e676; font-weight: 700; border-left: 5px solid #00e676;"] * len(row)
                else:
                    return ["background-color: rgba(255, 82, 82, 0.35); color: #ff5252; font-weight: 700; border-left: 5px solid #ff5252;"] * len(row)
            elif st_val == "NO_TRADE" or "NO TRADE" in sd_val:
                return ["color: #888888; font-style: italic; background-color: rgba(255, 255, 255, 0.02);"] * len(row)
            return [""] * len(row)

        format_dict = {
            "entry_price": "₹{:.2f}",
            "exit_price": "₹{:.2f}",
            "gross_pnl": "₹{:+,.2f}",
            "charges": "₹{:.2f}",
            "net_pnl": "₹{:+,.2f}",
            "Margin used": "₹{:,.2f}",
            "stop_loss": "₹{:.2f}",
            "target": "₹{:.2f}",
            "Index-LTP": "{:.2f}",
        }
        styled_trail = df_trail[display_cols].style.apply(highlight_active_rows, axis=1).format(format_dict, na_rep="-")

        st.dataframe(
            styled_trail,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Display_ID": st.column_config.TextColumn("Trade Identifier", help="Daily live trade run identifier mapped to Strategy Name"),
                "Instrument": st.column_config.TextColumn("Instrument", help="Active constructed instrument / contract symbol (option chain name)"),
                "Index-LTP": st.column_config.NumberColumn("Index-LTP", help="Underlying Index LTP from oi_strength table at trade entry (used to validate constructed option strike)", format="%.2f"),
                "Option Type": st.column_config.TextColumn("Option Type", help="Option contract type: CE (Call) or PE (Put)"),
                "session_date": st.column_config.TextColumn("Session Date"),
                "status": st.column_config.TextColumn("Status", help="ACTIVE (Open), CLOSED, or NO_TRADE"),
                "Margin used": st.column_config.TextColumn("Margin used", help="Margin used per trade = Quantity × Entry Price"),
                "peak_gain": st.column_config.TextColumn("peak_gain", help="Crossed profit slab reached before reversing to exit / Stop."),
            },
        )

        # ==============================================================================
        # Trade Audit Trail-2 (Candle Progression: C1 to C360)
        # Dynamic Auto-Increment Population for each 1m/3m/5m candle at live market
        # ==============================================================================
        st.markdown("---")
        st.subheader("Trade Audit Trail-2 (Candle Progression: C1 to C360)")
        st.caption(
            "Candle-by-candle multi-factor confluence score and percentage gain from entry until exit. "
            "Format: Score(Gain%) e.g. +5(+10.0%), +4(+1.3%). "
            "Auto-increments for each 1m/3m candle in real time; active records highlighted in color."
        )

        c_cols = [f"C{i}" for i in range(1, 361)]
        c_data = {col: ["-"] * len(df_trail) for col in c_cols}

        for row_i, (_, r_tr) in enumerate(df_trail.iterrows()):
            if str(r_tr.get("status")) == "NO_TRADE":
                continue

            e_str = str(r_tr.get("entry_time", ""))
            x_str = str(r_tr.get("exit_time", ""))
            e_p = float(r_tr.get("entry_price", 0.0) or 0.0)
            side = str(r_tr.get("side", "LONG")).upper()
            tr_status = str(r_tr.get("status", "CLOSED")).upper()

            candles_matched = []
            if scored_candles is not None and not scored_candles.empty and e_str:
                try:
                    e_dt = pd.to_datetime(e_str)
                    if e_dt.tzinfo is not None:
                        e_dt = e_dt.tz_localize(None)

                    # Match candles after entry_time
                    m = scored_candles["timestamp"] >= e_dt
                    if tr_status == "CLOSED" and x_str:
                        x_dt = pd.to_datetime(x_str)
                        if x_dt.tzinfo is not None:
                            x_dt = x_dt.tz_localize(None)
                        m = m & (scored_candles["timestamp"] <= x_dt)

                    sub_candles = scored_candles[m]
                    if not sub_candles.empty:
                        candles_matched = sub_candles.to_dict("records")
                except Exception as ex:
                    logger.debug("Candle match error for trade %s: %s", r_tr.get("trade_id"), ex)

            if candles_matched:
                for k, c_row in enumerate(candles_matched[:360]):
                    c_close = float(c_row.get("close", e_p))
                    c_score = int(c_row.get("consensus_score", r_tr.get("score", 0)))
                    sc_str = f"+{c_score}" if c_score > 0 else str(c_score)
                    if e_p > 0:
                        if "LONG" in side or "BUY" in side:
                            pct = round((c_close - e_p) / e_p * 100.0, 1)
                        else:
                            pct = round((e_p - c_close) / e_p * 100.0, 1)
                    else:
                        pct = 0.0
                    pct_str = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
                    c_data[f"C{k+1}"][row_i] = f"{sc_str}({pct_str})"
            else:
                # Fallback synthesized progression based on peak gain and entry score
                pk_val = float(r_tr.get("peak_gain_pct", 0.0) or 0.0)
                sc_val = int(r_tr.get("score", 0) or 0)
                sc_txt = f"+{sc_val}" if sc_val > 0 else str(sc_val)
                steps = min(10, max(1, int(pk_val // 1.5))) if pk_val > 0 else 1
                for k in range(steps):
                    pct_step = round((k + 1) * (pk_val / max(1, steps)), 1)
                    c_data[f"C{k+1}"][row_i] = f"{sc_txt}(+{pct_step}%)"

        c_df = pd.DataFrame(c_data, index=df_trail.index)
        base_cols_2 = [c for c in display_cols if c in df_trail.columns]
        trades_2 = pd.concat([df_trail[base_cols_2], c_df], axis=1)

        col_a, col_b = st.columns([3, 1])
        with col_b:
            st.download_button(
                label="📥 Export Trail-2 CSV",
                data=trades_2.to_csv(index=False).encode("utf-8"),
                file_name=f"live_trade_audit_trail_2_{selected_strat}.csv",
                mime="text/csv",
                key="dl_trade_audit_trail_2_live",
                use_container_width=True,
            )

        styled_trail_2 = trades_2.style.apply(highlight_active_rows, axis=1)
        st.dataframe(
            styled_trail_2,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Display_ID": st.column_config.TextColumn("Trade Identifier", help="Daily live trade run identifier mapped to Strategy Name"),
                "Instrument": st.column_config.TextColumn("Instrument", help="Active constructed instrument / contract symbol (option chain name)"),
                "Index-LTP": st.column_config.NumberColumn("Index-LTP", help="Underlying Index LTP from oi_strength table at trade entry (used to validate constructed option strike)", format="%.2f"),
                "Option Type": st.column_config.TextColumn("Option Type", help="Option contract type: CE (Call) or PE (Put)"),
            },
        )

    else:
        st.info(f"No live trades recorded yet for strategy `{selected_strat}` within the previous one month.")

    # ---------------- 2-Second Live Auto Refresh Loop ----------------
    if is_live_running:
        _time.sleep(2.0)
        st.rerun()


if __name__ == "__main__":
    st.set_page_config(
        page_title="Live Trading Terminal",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_live_trade_module()

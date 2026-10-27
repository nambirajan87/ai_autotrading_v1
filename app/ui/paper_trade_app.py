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
from app.database.paper_database import get_paper_db
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



def render_html_block(html_content: str):
    """
    Renders raw HTML safely in Streamlit without triggering Markdown's 4-space indented code block parser.
    Strips leading/trailing whitespace from each line and eliminates empty lines.
    """
    cleaned_lines = [line.strip() for line in html_content.strip().splitlines() if line.strip()]
    cleaned_html = "".join(cleaned_lines)
    st.markdown(cleaned_html, unsafe_allow_html=True)


def render_paper_trade_hud_and_watchtower(
    is_live_running: bool,
    active_open_trade: Optional[dict],
    live_ltp: float,
    candles_df: Optional[pd.DataFrame],
    timeframe_m: int,
    strategy_sq_time: time,
    rem_seconds: int,
    next_check_str: str,
    latest_score: int,
    sig_disp: str,
    sig_color: str,
    long_th: int,
    short_th: int,
    lot_size: int,
    selected_strat: str,
    active_instrument: str,
    snooze_min: int = 0,
    last_exit_dt: Optional[datetime] = None,
):
    """
    Renders Hybrid Live Trade Status:
    1. Persistent Top HUD Ribbon (Stopwatch, Bar Countdown, Proximity Track Bar, P&L)
    2. Collapsible 'Trade Watchtower' 4-Card Deck (In-depth lifecycle, target/SL delta, P&L, signal radar)
    """
    now_dt = datetime.now()
    tf_val = max(1, timeframe_m)

    # Square-off Countdown
    sq_dt = datetime.combine(now_dt.date(), strategy_sq_time)
    if sq_dt > now_dt:
        sq_rem_sec = int((sq_dt - now_dt).total_seconds())
        sq_h, rem_sq = divmod(sq_rem_sec, 3600)
        sq_m, sq_s = divmod(rem_sq, 60)
        sq_rem_str = f"{sq_h:02d}h {sq_m:02d}m {sq_s:02d}s"
    else:
        sq_rem_str = "00h 00m 00s (Reached)"

    # Cooldown remaining
    cooldown_str = None
    if snooze_min > 0 and last_exit_dt:
        s_end = last_exit_dt + timedelta(minutes=snooze_min)
        if now_dt < s_end:
            rem_s = int((s_end - now_dt).total_seconds())
            sm, ss = divmod(rem_s, 60)
            cooldown_str = f"{sm:02d}m {ss:02d}s"

    if active_open_trade is not None:
        tid = active_open_trade.get("trade_id") or active_open_trade.get("id") or 5001
        e_side = str(active_open_trade.get("side", "LONG")).upper()
        e_p = float(active_open_trade.get("entry_price", live_ltp))
        trade_qty = int(active_open_trade.get("quantity", lot_size))
        tgt = float(active_open_trade.get("target") or 0.0)
        sl = float(active_open_trade.get("stop_loss") or 0.0)
        trade_sym = str(active_open_trade.get("symbol") or active_instrument)

        e_time_str = active_open_trade.get("entry_time") or ""
        elapsed_str = "00m 00s"
        if e_time_str:
            try:
                e_dt = pd.to_datetime(e_time_str)
                elapsed_sec = max(0, int((now_dt - e_dt).total_seconds()))
                eh, er = divmod(elapsed_sec, 3600)
                em, es = divmod(er, 60)
                elapsed_str = f"{eh:02d}:{em:02d}:{es:02d}" if eh > 0 else f"{em:02d}m {es:02d}s"
            except Exception:
                pass

        candle_count = 1
        if candles_df is not None and not candles_df.empty and e_time_str:
            try:
                c_after = candles_df[pd.to_datetime(candles_df["timestamp"]) >= pd.to_datetime(e_time_str)]
                candle_count = max(1, len(c_after))
            except Exception:
                pass

        gross_pnl = (live_ltp - e_p) * trade_qty if e_side == "LONG" else (e_p - live_ltp) * trade_qty
        pnl_pct = ((live_ltp - e_p) / e_p * 100.0) if e_p > 0 and e_side == "LONG" else ((e_p - live_ltp) / e_p * 100.0 if e_p > 0 else 0.0)
        charges = 40.0
        net_pnl = gross_pnl - charges
        pnl_color = "#00e676" if net_pnl >= 0 else "#ff5252"
        pnl_bg = "rgba(0, 230, 118, 0.15)" if net_pnl >= 0 else "rgba(255, 82, 82, 0.15)"
        pnl_sign = "+" if net_pnl >= 0 else ""

        if e_side == "LONG":
            dist_tgt_pts = max(0.0, tgt - live_ltp) if tgt > 0 else 0.0
            dist_tgt_pct = (dist_tgt_pts / live_ltp * 100.0) if live_ltp > 0 else 0.0
            dist_sl_pts = max(0.0, live_ltp - sl) if sl > 0 else 0.0
            dist_sl_pct = (dist_sl_pts / live_ltp * 100.0) if live_ltp > 0 else 0.0
            prog_pct = max(0.0, min(100.0, ((live_ltp - sl) / (tgt - sl)) * 100.0)) if (tgt > sl and sl > 0) else 50.0
        else:
            dist_tgt_pts = max(0.0, live_ltp - tgt) if tgt > 0 else 0.0
            dist_tgt_pct = (dist_tgt_pts / live_ltp * 100.0) if live_ltp > 0 else 0.0
            dist_sl_pts = max(0.0, sl - live_ltp) if sl > 0 else 0.0
            dist_sl_pct = (dist_sl_pts / live_ltp * 100.0) if live_ltp > 0 else 0.0
            prog_pct = max(0.0, min(100.0, ((sl - live_ltp) / (sl - tgt)) * 100.0)) if (sl > tgt and tgt > 0) else 50.0

        rr_ratio = abs(tgt - e_p) / abs(e_p - sl) if abs(e_p - sl) > 0.01 else 2.0
        phase_badge = "🟢 PROFIT RUNNING" if gross_pnl > 0 else ("🟡 AT BREAKEVEN" if abs(gross_pnl) < 1.0 else "🔴 UNDER WATER")
        phase_color = "#00e676" if gross_pnl > 0 else ("#ffd600" if abs(gross_pnl) < 1.0 else "#ff5252")

        # 1. Top HUD Ribbon
        render_html_block(f"""
        <div style="background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.96)); border: 1px solid rgba(59, 130, 246, 0.45); border-radius: 10px; padding: 14px 18px; margin: 12px 0 16px 0; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="background: rgba(0, 230, 118, 0.2); color: #00e676; border: 1px solid #00e676; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 0.85rem; letter-spacing: 0.5px;">
                        🟢 TRADE #{tid} ACTIVE
                    </span>
                    <span style="color: #f8fafc; font-weight: 600; font-size: 0.95rem;">
                        {trade_sym} ({e_side} x{trade_qty})
                    </span>
                    <span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">
                        Candle #{candle_count} ({tf_val}m)
                    </span>
                </div>
                <div style="display: flex; align-items: center; gap: 14px; font-size: 0.88rem; color: #cbd5e1;">
                    <span>⏱️ <b>In Trade:</b> <span style="color: #38bdf8; font-weight: 700; font-family: monospace; font-size: 0.95rem;">{elapsed_str}</span></span>
                    <span>⏳ <b>Bar Close:</b> <span style="color: #fbbf24; font-weight: 700; font-family: monospace;">{rem_seconds}s</span></span>
                    <span>🛑 <b>Sq-Off:</b> <span style="color: #f87171; font-weight: 700; font-family: monospace;">{sq_rem_str}</span></span>
                    <span style="background: {pnl_bg}; color: {pnl_color}; border: 1px solid {pnl_color}; padding: 3px 10px; border-radius: 6px; font-weight: 700; font-size: 0.92rem;">
                        {pnl_sign}₹{net_pnl:,.2f} ({pnl_pct:+.2f}%)
                    </span>
                </div>
            </div>
            <div style="background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 10px 14px;">
                <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-bottom: 6px;">
                    <span style="color: #ef4444; font-weight: 600;">🛑 SL: ₹{sl:.2f} <span style="color: #94a3b8; font-weight: normal;">({dist_sl_pts:.2f} pts away)</span></span>
                    <span style="color: #38bdf8; font-weight: 600;">🔵 Entry: ₹{e_p:.2f} &nbsp;|&nbsp; Current LTP: <b style="color: #ffffff;">₹{live_ltp:.2f}</b></span>
                    <span style="color: #10b981; font-weight: 600;">🎯 Target: ₹{tgt:.2f} <span style="color: #94a3b8; font-weight: normal;">({dist_tgt_pts:.2f} pts away)</span></span>
                </div>
                <div style="height: 10px; width: 100%; background: #334155; border-radius: 5px; position: relative; overflow: hidden;">
                    <div style="position: absolute; left: 0; top: 0; bottom: 0; width: {prog_pct:.1f}%; background: linear-gradient(90deg, #ef4444 0%, #3b82f6 50%, #10b981 100%); border-radius: 5px; transition: width 0.3s ease;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: #94a3b8; margin-top: 5px;">
                    <span>Risk Cushion: <b style="color: #cbd5e1;">{dist_sl_pct:.1f}%</b></span>
                    <span style="color: {phase_color}; font-weight: 600;">{phase_badge}</span>
                    <span>Distance to Target: <b style="color: #cbd5e1;">{dist_tgt_pct:.1f}%</b> remaining</span>
                </div>
            </div>
        </div>
        """)

        # 2. Collapsible Watchtower Command Deck
        with st.expander("🔭 Trade Watchtower — In-Depth Lifecycle, Risk & Trajectory Deck", expanded=True):
            w_col1, w_col2, w_col3, w_col4 = st.columns(4)
            with w_col1:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">⏱️ Lifecycle Clock</div>
                    <div style="font-size: 1.35rem; font-weight: 700; color: #38bdf8; font-family: monospace; margin: 4px 0;">{elapsed_str}</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Entry Time:</b> {e_time_str}</div>
                        <div><b>Bar Progression:</b> Candle #{candle_count} ({tf_val}m)</div>
                        <div><b>Sq-Off In:</b> <span style="color: #f87171; font-weight: 600;">{sq_rem_str}</span></div>
                    </div>
                </div>
                """)
            with w_col2:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">🎯 Target / SL Proximity</div>
                    <div style="font-size: 1.15rem; font-weight: 700; color: #10b981; margin: 4px 0;">₹{tgt:.2f} <span style="font-size: 0.82rem; color: #94a3b8; font-weight: normal;">(+{dist_tgt_pts:.2f} pts)</span></div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Stop-Loss:</b> <span style="color: #ef4444; font-weight: 600;">₹{sl:.2f}</span> (-{dist_sl_pts:.2f} pts)</div>
                        <div><b>Risk Cushion:</b> {dist_sl_pct:.1f}% | <b>Target Dist:</b> {dist_tgt_pct:.1f}%</div>
                        <div><b>Risk/Reward Ratio:</b> 1 : {rr_ratio:.2f}</div>
                    </div>
                </div>
                """)
            with w_col3:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">📈 P&L & Trajectory</div>
                    <div style="font-size: 1.35rem; font-weight: 700; color: {pnl_color}; margin: 4px 0;">{pnl_sign}₹{net_pnl:,.2f}</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Gross P&L:</b> ₹{gross_pnl:,.2f} ({pnl_pct:+.2f}%)</div>
                        <div><b>Charges & Brokerage:</b> ₹{charges:.2f}</div>
                        <div><b>Phase:</b> <span style="color: {phase_color}; font-weight: 600;">{phase_badge}</span></div>
                    </div>
                </div>
                """)
            with w_col4:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">🧭 Engine & Signal Radar</div>
                    <div style="font-size: 1.15rem; font-weight: 700; color: {sig_color}; margin: 4px 0;">{sig_disp} (Score: {latest_score})</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Next Bar Close:</b> <span style="color: #fbbf24; font-weight: 600;">{next_check_str}</span> ({rem_seconds}s)</div>
                        <div><b>Thresholds:</b> Long &ge; {long_th} | Short &le; {short_th}</div>
                        <div><b>Strategy:</b> <span style="color: #38bdf8;">{selected_strat[:22]}</span></div>
                    </div>
                </div>
                """)

    else:
        # Scanning / Standby mode
        cooldown_badge = f"""<span style="background: rgba(234, 179, 8, 0.2); color: #eab308; border: 1px solid #eab308; padding: 2px 8px; border-radius: 4px; font-weight: 600;">⏸️ Cooldown: {cooldown_str}</span>""" if cooldown_str else ""
        render_html_block(f"""
        <div style="background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.96)); border: 1px solid rgba(59, 130, 246, 0.45); border-radius: 10px; padding: 14px 18px; margin: 12px 0 16px 0; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid #3b82f6; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 0.85rem; letter-spacing: 0.5px;">
                        📡 SCANNING FOR SIGNALS
                    </span>
                    <span style="color: #f8fafc; font-weight: 600; font-size: 0.95rem;">
                        {active_instrument} (Timeframe: {tf_val}m)
                    </span>
                </div>
                <div style="display: flex; align-items: center; gap: 14px; font-size: 0.88rem; color: #cbd5e1;">
                    <span>⏱️ <b>Next Signal Check:</b> <span style="color: #fbbf24; font-weight: 700; font-family: monospace;">{next_check_str}</span> (<b>{rem_seconds}s</b>)</span>
                    <span>🛑 <b>Session Sq-Off:</b> <span style="color: #f87171; font-weight: 700; font-family: monospace;">{sq_rem_str}</span></span>
                    {cooldown_badge}
                </div>
            </div>
            <div style="margin-top: 10px; font-size: 0.82rem; color: #94a3b8; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid rgba(148, 163, 184, 0.15); padding-top: 8px;">
                <span>Consensus Score: <b style="color: {sig_color};">{latest_score}</b> (Long threshold: <b>>={long_th}</b> | Short threshold: <b><={short_th}</b>)</span>
                <span>Continuous 2s Kotak Live LTP & Strategy Monitor Active</span>
            </div>
        </div>
        """)

        with st.expander("🔭 Trade Watchtower — Standby & Strategy Parameters Deck", expanded=False):
            w_col1, w_col2, w_col3, w_col4 = st.columns(4)
            with w_col1:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">⏱️ Execution Timing</div>
                    <div style="font-size: 1.15rem; font-weight: 700; color: #38bdf8; margin: 4px 0;">STANDBY / SCANNING</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Sq-off Time:</b> {strategy_sq_time.strftime('%H:%M')} IST</div>
                        <div><b>Session Closes In:</b> <span style="color: #f87171; font-weight: 600;">{sq_rem_str}</span></div>
                        <div><b>Cooldown:</b> {cooldown_str or 'None (Ready)'}</div>
                    </div>
                </div>
                """)
            with w_col2:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">🎯 Strategy & Target Rules</div>
                    <div style="font-size: 1.15rem; font-weight: 700; color: #10b981; margin: 4px 0;">{selected_strat[:20]}</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Timeframe:</b> {tf_val} Minutes</div>
                        <div><b>Lot Size:</b> {lot_size} Qty</div>
                        <div><b>Next Bar Check:</b> {next_check_str} ({rem_seconds}s)</div>
                    </div>
                </div>
                """)
            with w_col3:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">📈 Price & Market Radar</div>
                    <div style="font-size: 1.35rem; font-weight: 700; color: #ffffff; margin: 4px 0;">₹{live_ltp:.2f}</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Active Contract:</b> {active_instrument}</div>
                        <div><b>Status:</b> Ready for new signal</div>
                        <div><b>Broker API:</b> Kotak Neo Live Priority</div>
                    </div>
                </div>
                """)
            with w_col4:
                render_html_block(f"""
                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 12px; height: 100%;">
                    <div style="font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">🧭 Consensus Signal Radar</div>
                    <div style="font-size: 1.15rem; font-weight: 700; color: {sig_color}; margin: 4px 0;">{sig_disp} (Score: {latest_score})</div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; line-height: 1.5;">
                        <div><b>Long Trigger:</b> Score &ge; {long_th}</div>
                        <div><b>Short Trigger:</b> Score &le; {short_th}</div>
                        <div><b>Engine Status:</b> 🟢 Active Continuous Scan</div>
                    </div>
                </div>
                """)


def render_paper_trade_module():
    # Record visit for auditing
    record_session_visit()

    paper_db = get_paper_db()

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
            background: linear-gradient(135deg, rgba(0, 230, 118, 0.12), rgba(38, 166, 154, 0.20));
            border: 2px solid #00e676;
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 16px;
            animation: pulse-border 2s infinite;
        }
        .active-trade-box-short {
            background: linear-gradient(135deg, rgba(255, 82, 82, 0.12), rgba(239, 83, 80, 0.20));
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
    st.sidebar.title("📈 Paper Trading Studio")
    st.sidebar.caption("Candle-by-Candle Strategy Simulator & Trade Journal")

    st.sidebar.markdown("### 🎯 Strategy Filter")
    saved_strats = list_strategy_names()
    db_strats = paper_db.get_all_strategy_names()
    all_strats = sorted(list(set(saved_strats + db_strats)))
    if not all_strats:
        all_strats = ["PAPERTRADE_TEST_OI_V2", "ADX", "EMA_RSI_MACD_Balanced"]

    # Use PAPERTRADE_TEST_OI_V2 as default strategy for paper trade execution
    default_paper_strat = "PAPERTRADE_TEST_OI_V2" if "PAPERTRADE_TEST_OI_V2" in all_strats else ("ADX" if "ADX" in all_strats else all_strats[0])
    if "paper_selected_strategy" not in st.session_state or st.session_state.paper_selected_strategy not in all_strats:
        st.session_state.paper_selected_strategy = default_paper_strat

    def on_strategy_change():
        st.session_state.paper_strategy_user_manually_selected = True

    selected_strat = st.sidebar.selectbox(
        "Strategy Name",
        all_strats,
        index=all_strats.index(st.session_state.paper_selected_strategy),
        key="paper_sidebar_strat_select",
        on_change=on_strategy_change,
    )
    st.session_state.paper_selected_strategy = selected_strat

    if st.sidebar.button("👁️ View Run", type="primary", use_container_width=True, key="paper_btn_view_run"):
        st.session_state.paper_selected_strategy = selected_strat
        st.rerun()



    st.sidebar.markdown("---")
    with st.sidebar.expander("⚠️ Danger Zone", expanded=False):
        confirm_clear = st.checkbox("Confirm deletion of this history", key="confirm_clear_strat_paper")
        if st.button("🗑️ Clear History for this Strategy", disabled=not confirm_clear, use_container_width=True):
            paper_db.clear_history_for_strategy(selected_strat)
            st.success(f"Cleared history for {selected_strat}!")
            st.rerun()

    # ---------------- 2. Main Page: Cloned Backtest Results View ----------------
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

    # Fetch accumulated paper trades for this strategy within previous 1 month
    accumulated_trades_list = paper_db.get_accumulated_trades_for_strategy(selected_strat, max_days=30)
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
    st.caption(f"Strategy: `{selected_strat}` | Period: {one_month_ago_str} to {today_str}{tsl_info} | Square-off Time: **{strategy_sq_time.strftime('%H:%M')} IST**")

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
    cs2.metric("LONG Threshold", f"≥ {long_th} pts", help="Score threshold that triggered LONG entries.")
    cs3.metric("SHORT Threshold", f"≤ {short_th} pts", help="Score threshold that triggered SHORT entries.")
    cs4.metric("Avg Trade Score", f"{avg_score:+.2f} pts" if not real_trades.empty else "N/A", help="Average Calculated_Score at entry across executed trades.")

    tgt_val = ind_cfg.get("fixed_target_pct", 3)
    sl_val = ind_cfg.get("fixed_stoploss_pct", 2)
    side_val = ind_cfg.get("trade_side", "BOTH")
    snooze_val = ind_cfg.get("snooze_minutes", 0)
    tsl_flag = ind_cfg.get("trailing_sl_enabled", False)
    runner_flag = ind_cfg.get("trail_beyond_target", False)
    tsl_disp = f" | Trailing SL = **Model B Active{' (Runner)' if runner_flag else ''}**" if tsl_flag else ""
    short_lot_disp = f" | Short Lot Size = **{ind_cfg.get('short_lot_size', 1)} Lot**" if side_val != "LONG" else ""
    snooze_disp = f" | Snooze = **{snooze_val} min**" if snooze_val > 0 else ""
    st.caption(f"🎯 **Trade Levels:** Fixed Target = **{tgt_val}%** | Fixed Stoploss = **{sl_val}%**{tsl_disp} | Side = **{side_val}**{short_lot_disp}{snooze_disp}")

    # ==============================================================================
    # SECTION: Current Date Paper Trade
    # (Above "Equity Curve" and "Daily Net P&L", below "Strategy Calculated_Score Profile")
    # ==============================================================================
    st.markdown("---")
    st.subheader("🔴 Current Date Paper Trade")

    # Check date settings against current date
    date_is_current = (strat_from == today_str and strat_to == today_str)
    is_mkt_open, mkt_msg = check_market_trading_hours()
    mkt_badge = "🟢 `OPEN` (09:15 - 15:30 IST)" if is_mkt_open else f"🔴 `CLOSED` ({mkt_msg})"

    # Render OI Pulse Live Strength Feed & Status
    from scripts.schedule_oipulse_strength import is_paper_trade_auto_triggered, ActiveSchedulerConfig

    oi_status = get_live_oipulse_status(underlying=underlying_name, ce_strength=ce_strength, pe_strength=pe_strength)

    pt_col1, pt_col2, pt_col3 = st.columns([3, 2, 2])
    with pt_col1:
        if fo_mode == "OIPulse Mode":
            if oi_status.get("has_active_instrument") and oi_status.get("active_instrument"):
                inst_badge = f"`{oi_status['active_instrument']}` ({oi_status.get('option_type')} Strike {oi_status.get('strike')})"
            else:
                inst_badge = f"*Standby* (Neutral OI Strength: `{oi_status.get('strength_str', '0%')}` &bull; Waiting for >{ce_strength:.0f}% or <{pe_strength:.0f}%)"
            st.markdown(f"**Underlying:** `{underlying_name}` &nbsp;|&nbsp; **Constructed Active Instrument:** {inst_badge} &nbsp;|&nbsp; **Mode:** `OIPulse Mode`")
        else:
            st.markdown(f"**Active Instrument:** `{active_instrument}` &nbsp;|&nbsp; **Segment:** `{inst_cfg.get('market_segment', 'NSE F&O')}`")
        st.caption(f"Strategy Dates: **{strat_from}** to **{strat_to}** | Today's Date: **{today_str}**")
    with pt_col2:
        date_badge = "🟢 Strategy Dates Validated (Current Date)" if date_is_current else "🔴 Date Mismatch (Update to Current Date)"
        st.markdown(f"**Validation Status:** {date_badge}")
        mkt_badge = "🟢 `OPEN` (09:15 - 15:30 IST)" if is_mkt_open else f"🔴 `CLOSED` ({mkt_msg})"
        st.markdown(f"**Market Status:** {mkt_badge}")
        st.caption(f"🛡️ **Auto Square-off Time:** `{strategy_sq_time.strftime('%H:%M')} IST` (RiskConfig)")
    # Auto-align dates to current date for paper trading session
    if not date_is_current:
        inst_cfg["start_date"] = today_str
        inst_cfg["end_date"] = today_str
        save_strategy(selected_strat, ind_cfg, risk_cfg, cost_cfg, instrument=inst_cfg)
        strat_from = today_str
        strat_to = today_str
        date_is_current = True

    st.markdown("##### 📡 OI Pulse Live Strength Feed (1-Min Backend Triggers)")
    oi_c1, oi_c2, oi_c3, oi_c4, oi_c5, oi_c6 = st.columns(6)
    oi_c1.metric("Trigger No", f"#{oi_status.get('trigger_no', 1)}")
    t_time_val = str(oi_status.get("trigger_time", "")).split(" ")[-1] if oi_status.get("trigger_time") else "09:16:00"
    oi_c2.metric("Trigger Time", t_time_val)
    oi_c3.metric("OI Pulse Date", str(oi_status.get("date") or today_str))
    oi_c4.metric("OI Pulse Time", str(oi_status.get("time") or "09:16"))
    oi_c5.metric("OI Pulse LTP", f"₹{float(oi_status.get('ltp', 0.0)):,.2f}")
    strength_disp = str(oi_status.get("strength_str", "0%"))
    oi_c6.metric("OI Strength", strength_disp, help="Real-time OI Strength from OI Pulse scheduler")

    now_curr_time = datetime.now().time()
    auto_trigger_eligible = is_paper_trade_auto_triggered()
    user_paused = st.session_state.get("paper_user_paused", False)
    is_live_running = st.session_state.get("paper_executing_live", False)

    # Auto-trigger "Execute Paper Trade" at 09:16 AM during market window without manual button
    if auto_trigger_eligible and not user_paused and not is_live_running:
        if now_curr_time < strategy_sq_time:
            # Default to PAPERTRADE_TEST_OI_V2 for auto-trigger paper scheduler unless manually overridden
            if "PAPERTRADE_TEST_OI_V2" in all_strats and not st.session_state.get("paper_strategy_user_manually_selected", False):
                st.session_state.paper_selected_strategy = "PAPERTRADE_TEST_OI_V2"
            st.session_state.paper_executing_live = True
            st.session_state.paper_live_start_time = datetime.now().strftime("%H:%M:%S")
            st.session_state.paper_auto_triggered = True
            is_live_running = True
            st.rerun()

    exec_col1, exec_col2 = st.columns([2, 5])

    with exec_col1:
        if is_live_running:
            if st.button("⏹️ Stop Paper Trade Session", type="secondary", use_container_width=True, key="btn_stop_paper_live"):
                st.session_state.paper_executing_live = False
                st.session_state.paper_user_paused = True
                st.rerun()
        elif user_paused:
            if st.button("▶️ Resume Watching OI Strength", type="primary", use_container_width=True, key="btn_resume_paper_live"):
                if "PAPERTRADE_TEST_OI_V2" in all_strats and not st.session_state.get("paper_strategy_user_manually_selected", False):
                    st.session_state.paper_selected_strategy = "PAPERTRADE_TEST_OI_V2"
                st.session_state.paper_user_paused = False
                st.session_state.paper_executing_live = True
                st.session_state.paper_live_start_time = datetime.now().strftime("%H:%M:%S")
                st.rerun()
        else:
            st.markdown(
                """
                <div style="background: rgba(33, 150, 243, 0.08); border: 1px dashed rgba(33, 150, 243, 0.35); border-radius: 8px; padding: 12px; text-align: center;">
                    <div style="font-size: 0.85rem; font-weight: 700; color: #64b5f6;">⚡ AUTO-TRIGGER MODE</div>
                    <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 2px;">Market Window: Mon-Fri 09:15-15:15<br>Auto Starts at 09:16 AM</div>
                    <div style="font-size: 0.72rem; color: #00e676; margin-top: 4px; font-weight: 600;">Manual Button Removed</div>
                </div>
                """,
                unsafe_allow_html=True
            )

    with exec_col2:
        if is_live_running:
            if fo_mode == "OIPulse Mode":
                inst_watching = f"**{underlying_name} Option Chain** (Constructed Active Instrument: `{oi_status.get('active_instrument') or 'Standby (Neutral Band)'}`)"
            else:
                inst_watching = f"**{active_instrument}**"
            st.success(
                f"🟢 **Auto-Trigger Active (09:16 AM Market Window):** Execution engine auto-triggered and is continuously **watching for OI Strength** "
                f"for {inst_watching}.\n\n"
                f"📡 **OI Pulse Status:** Triggering every 1 min (:14s) &bull; Market Window: **Monday - Friday, 09:15 AM - 03:15 PM** &bull; "
                f"Trigger Active: **09:16:00 IST** &bull; Auto-Refresh: **2 seconds** &bull; Session Started: **{st.session_state.get('paper_live_start_time', '')}**"
            )
        elif user_paused:
            st.warning(
                "⏸️ **Paper Trade Session Paused by User:** Watching OI Strength is temporarily paused. "
                "Click **'▶️ Resume Watching OI Strength'** on the left to re-engage continuous scanning."
            )

        else:
            if datetime.now().weekday() < 5 and now_curr_time < time(9, 16):
                st.info(
                    "⏳ **OI Pulse Scheduler Armed:** Market Window: **Monday - Friday, 09:15 AM - 03:15 PM**.\n\n"
                    "Auto-trigger is scheduled for **09:16 AM** (waiting for 1st minute OI candle close). "
                    "The engine will automatically start and watch for OI Strength. Manual execute button has been removed."
                )
            else:
                st.info(
                    f"🛡️ **OI Pulse Scheduler Standby (Market Window: Mon - Fri, 09:15 AM - 03:15 PM):** {mkt_msg}\n\n"
                    "Paper trade auto-trigger is on standby. At **09:16 AM** on the next trading session, "
                    "execution will automatically engage and watch for OI Strength without requiring any manual button click."
                )

    # ---------------- Active Execution Engine & Live Kotak Chart ----------------
    gateway = get_gateway("KOTAK")
    timeframe_m = int(inst_cfg.get("timeframe", 1))

    # Fetch underlying intraday candles from Kotak gateway or local fallbacks
    underlying_candles = gateway.fetch_historical_candles(
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
        q_und = gateway.get_ltp(underlying_name)
        if q_und and float(q_und) > 0:
            underlying_ltp = float(q_und)

    # Step 4: After finding Active Instrument is valid (or in active trade), fetch live candles from Kotak & display Active Instrument chart
    if active_instrument:
        candles_df = get_or_fetch_active_option_candles(
            contract_symbol=active_instrument,
            underlying_candles=underlying_candles,
            gateway=gateway,
            timeframe_minutes=timeframe_m,
            start_date=today,
            end_date=today,
            underlying=underlying_name,
        )
        if candles_df is None or candles_df.empty:
            candles_df = underlying_candles

        opt_quote = gateway.get_ltp(active_instrument)
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

    # Constant Display of Both Trigger & Signal Bar Check Sections during active session
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

        # Cooldown / Snooze Check for Timer HUD
        hud_snooze_min = int(risk_cfg.get("snooze_minutes", 0) or ind_cfg.get("snooze_minutes", 0) or 0)
        hud_last_exit = st.session_state.get("paper_last_exit_time")
        if not hud_last_exit and not real_trades.empty and "exit_time" in real_trades.columns:
            valid_exits = real_trades["exit_time"].dropna()
            if not valid_exits.empty:
                try:
                    hud_last_exit = pd.to_datetime(valid_exits.iloc[-1])
                except Exception:
                    pass

        # Open trade current price for HUD & exit evaluation
        hud_trade_ltp = live_ltp
        if active_open_trade is not None:
            open_sym = str(active_open_trade.get("symbol") or "")
            open_ep = float(active_open_trade.get("entry_price", live_ltp))
            opt_q = gateway.get_ltp(open_sym)
            if opt_q and float(opt_q) > 0:
                hud_trade_ltp = float(opt_q)
            elif fo_mode == "OIPulse Mode":
                parsed_sym = parse_oipulse_contract_symbol(open_sym, underlying=underlying_name)
                if parsed_sym:
                    exp_dt, _ = resolve_nearest_expiry_date(datetime.now().date(), underlying=parsed_sym["underlying"])
                    calc_p = calculate_option_price(
                        underlying_price=live_ltp,
                        strike_price=float(parsed_sym["strike"]),
                        option_type=parsed_sym["option_type"],
                        candle_dt=datetime.now(),
                        expiry_dt=exp_dt,
                    )
                    hud_trade_ltp = round(max(calc_p, 1.0), 2)
                else:
                    hud_trade_ltp = open_ep
            else:
                hud_trade_ltp = open_ep

        # Render Hybrid Option 1 (HUD Ribbon) & Collapsible Option 2 (Trade Watchtower)
        render_paper_trade_hud_and_watchtower(
            is_live_running=is_live_running,
            active_open_trade=active_open_trade,
            live_ltp=hud_trade_ltp,
            candles_df=candles_df,
            timeframe_m=timeframe_m,
            strategy_sq_time=strategy_sq_time,
            rem_seconds=rem_seconds,
            next_check_str=next_check_str,
            latest_score=latest_score,
            sig_disp=sig_disp,
            sig_color=sig_color,
            long_th=long_th,
            short_th=short_th,
            lot_size=int(inst_cfg.get("lot_size", 65)),
            selected_strat=selected_strat,
            active_instrument=active_instrument,
            snooze_min=hud_snooze_min,
            last_exit_dt=hud_last_exit,
        )

    # If live running, check market hours and strategy square-off time; if past square-off or market closed, auto square off and stop
    if is_live_running:
        now_time = datetime.now().time()
        if now_time >= strategy_sq_time or not is_mkt_open:
            st.session_state.paper_executing_live = False
            if active_open_trade is not None:
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                e_p = float(active_open_trade.get("entry_price", hud_trade_ltp))
                e_side = active_open_trade.get("side", "LONG")
                gross = (hud_trade_ltp - e_p) * int(active_open_trade.get("quantity", 65)) if e_side == "LONG" else (e_p - hud_trade_ltp) * int(active_open_trade.get("quantity", 65))
                exit_reason = "SQUARE_OFF" if now_time >= strategy_sq_time else "MARKET_CLOSE_SQUARE_OFF"
                paper_db.close_active_trade(
                    trade_id_or_row_id=active_open_trade.get("id"),
                    exit_time=now_ts,
                    exit_price=hud_trade_ltp,
                    exit_reason=exit_reason,
                    gross_pnl=round(gross, 2),
                    charges=20.0,
                    peak_gain_pct=0.0,
                )
            if now_time >= strategy_sq_time:
                st.warning(f"🔔 Strategy Square-off Time ({strategy_sq_time.strftime('%H:%M')} IST) reached from Risk Parameters. Execution session ended and position squared off.")
            else:
                st.warning("🔔 Market closed at 15:30:00 IST. Execution session ended and open positions squared off.")
            st.rerun()

        lot_size = int(inst_cfg.get("lot_size", 65))
        now_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Cooldown / Snooze Period Check (Fix 1)
        snooze_min = int(risk_cfg.get("snooze_minutes", 0) or ind_cfg.get("snooze_minutes", 0) or 0)
        last_exit_dt = st.session_state.get("paper_last_exit_time")
        if not last_exit_dt and not real_trades.empty and "exit_time" in real_trades.columns:
            valid_exits = real_trades["exit_time"].dropna()
            if not valid_exits.empty:
                try:
                    last_exit_dt = pd.to_datetime(valid_exits.iloc[-1])
                except Exception:
                    pass

        snooze_active = False
        if snooze_min > 0 and last_exit_dt is not None:
            elapsed_sec = (datetime.now() - last_exit_dt).total_seconds()
            if 0 <= elapsed_sec < snooze_min * 60:
                snooze_active = True
                rem_m = int((snooze_min * 60 - elapsed_sec) // 60)
                rem_s = int((snooze_min * 60 - elapsed_sec) % 60)
                st.info(f"⏳ **Cooldown Active:** Waiting **{rem_m}m {rem_s}s** after previous trade exit ({snooze_min}m snooze). New entries are blocked.")

        if active_open_trade is None:
            now_time = datetime.now().time()
            if now_time >= strategy_sq_time or now_time > strategy_entry_end:
                st.info(f"⏳ Current time ({now_time.strftime('%H:%M:%S')}) is past Strategy Entry End ({strategy_entry_end.strftime('%H:%M')}) / Square-off ({strategy_sq_time.strftime('%H:%M')}) from Risk Parameters. New entries are blocked.")
            elif not snooze_active:
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
                        # Strategy Trade Side: PAPERTRADE_TEST_OI_V2 is strictly LONG (Locked to LONG via Indicator 11 Higher High)
                        signal_matched = False
                        if opt_type == "CE" and latest_score >= long_th and side_val in ("BOTH", "LONG"):
                            signal_matched = True
                        elif opt_type == "PE" and latest_score <= short_th and side_val in ("BOTH", "SHORT"):
                            signal_matched = True

                        if signal_matched:
                            entry_p = live_ltp
                            tgt_p = round(entry_p * (1.0 + tgt_val / 100.0), 2)
                            sl_p = round(entry_p * (1.0 - sl_val / 100.0), 2)
                            next_tid = 5000 + len(real_trades) + 1

                            # Dynamic Position Sizing (Fix 2)
                            from app.risk.position_sizing import calculate_quantity
                            risk_pct = float(risk_cfg.get("risk_per_trade_pct", 0.01))
                            max_exp_pct = float(risk_cfg.get("max_exposure_pct", 1.0))
                            max_quantity = int(risk_cfg.get("max_quantity", 5000))
                            room = capital * max_exp_pct
                            calc_qty = calculate_quantity(
                                capital=capital,
                                risk_pct=risk_pct,
                                entry=entry_p,
                                stop=sl_p,
                                lot_size=lot_size,
                                max_exposure_value=room,
                                max_quantity=max_quantity,
                            )
                            trade_qty = calc_qty if calc_qty > 0 else lot_size

                            # Step 7 & 8: Save active trade with Valid Active Instrument and Index-LTP
                            # Once saved, active_open_trade is active, so Step 2 (Strike Rotation) STOPS!
                            paper_db.save_active_trade(
                                strategy_name=selected_strat,
                                symbol=trade_symbol,
                                session_date=today_str,
                                trade_id=next_tid,
                                side="LONG",
                                quantity=trade_qty,
                                entry_time=now_time_str,
                                entry_price=entry_p,
                                stop_loss=sl_p,
                                target=tgt_p,
                                score=latest_score,
                                regime="TRENDING",
                                index_ltp=underlying_ltp,
                            )
                            st.toast(f"🟢 Executed LONG {opt_type} Trade #{next_tid} on {trade_symbol} (Qty: {trade_qty}) @ ₹{entry_p:.2f}!", icon="🟢")
                            st.rerun()

                else:
                    # Normal Mode (fo_mode != "OIPulse Mode"):
                    trade_symbol = active_instrument
                    if latest_score >= long_th and side_val in ("BOTH", "LONG"):
                        entry_p = live_ltp
                        tgt_p = round(entry_p * (1.0 + tgt_val / 100.0), 2)
                        sl_p = round(entry_p * (1.0 - sl_val / 100.0), 2)
                        next_tid = 5000 + len(real_trades) + 1

                        from app.risk.position_sizing import calculate_quantity
                        risk_pct = float(risk_cfg.get("risk_per_trade_pct", 0.01))
                        max_exp_pct = float(risk_cfg.get("max_exposure_pct", 1.0))
                        max_quantity = int(risk_cfg.get("max_quantity", 5000))
                        room = capital * max_exp_pct
                        calc_qty = calculate_quantity(
                            capital=capital,
                            risk_pct=risk_pct,
                            entry=entry_p,
                            stop=sl_p,
                            lot_size=lot_size,
                            max_exposure_value=room,
                            max_quantity=max_quantity,
                        )
                        trade_qty = calc_qty if calc_qty > 0 else lot_size

                        paper_db.save_active_trade(
                            strategy_name=selected_strat,
                            symbol=trade_symbol,
                            session_date=today_str,
                            trade_id=next_tid,
                            side="LONG",
                            quantity=trade_qty,
                            entry_time=now_time_str,
                            entry_price=entry_p,
                            stop_loss=sl_p,
                            target=tgt_p,
                            score=latest_score,
                            regime="TRENDING",
                            index_ltp=underlying_ltp,
                        )
                        st.toast(f"🟢 Executed LONG Trade #{next_tid} on {trade_symbol} (Qty: {trade_qty}) @ ₹{entry_p:.2f}!", icon="🟢")
                        st.rerun()

                    elif latest_score <= short_th and side_val in ("BOTH", "SHORT"):
                        entry_p = live_ltp
                        tgt_p = round(entry_p * (1.0 - tgt_val / 100.0), 2)
                        sl_p = round(entry_p * (1.0 + sl_val / 100.0), 2)
                        next_tid = 5000 + len(real_trades) + 1

                        from app.risk.position_sizing import calculate_quantity
                        risk_pct = float(risk_cfg.get("risk_per_trade_pct", 0.01))
                        max_exp_pct = float(risk_cfg.get("max_exposure_pct", 1.0))
                        max_quantity = int(risk_cfg.get("max_quantity", 5000))
                        room = capital * max_exp_pct
                        calc_qty = calculate_quantity(
                            capital=capital,
                            risk_pct=risk_pct,
                            entry=entry_p,
                            stop=sl_p,
                            lot_size=lot_size,
                            max_exposure_value=room,
                            max_quantity=max_quantity,
                        )
                        trade_qty = calc_qty if calc_qty > 0 else lot_size

                        paper_db.save_active_trade(
                            strategy_name=selected_strat,
                            symbol=trade_symbol,
                            session_date=today_str,
                            trade_id=next_tid,
                            side="SHORT",
                            quantity=trade_qty,
                            entry_time=now_time_str,
                            entry_price=entry_p,
                            stop_loss=sl_p,
                            target=tgt_p,
                            score=latest_score,
                            regime="TRENDING",
                            index_ltp=underlying_ltp,
                        )
                        st.toast(f"🔴 Executed SHORT Trade #{next_tid} on {trade_symbol} (Qty: {trade_qty}) @ ₹{entry_p:.2f}!", icon="🔴")
                        st.rerun()

        elif active_open_trade is not None:
            # Active trade is running: check for Square-off time, Target, or Stop Loss trigger
            e_side = active_open_trade.get("side", "LONG")
            e_p = float(active_open_trade.get("entry_price", hud_trade_ltp))
            trade_qty = int(active_open_trade.get("quantity", lot_size))
            tgt = float(active_open_trade.get("target") or 0.0)
            sl = float(active_open_trade.get("stop_loss") or 0.0)
            row_db_id = active_open_trade.get("id")

            now_time = datetime.now().time()
            exit_triggered = None
            if now_time >= strategy_sq_time or (not candles_df.empty and pd.to_datetime(candles_df.iloc[-1]["timestamp"]).time() >= strategy_sq_time):
                exit_triggered = "SQUARE_OFF"
            elif e_side == "LONG":
                if tgt > 0 and hud_trade_ltp >= tgt:
                    exit_triggered = "TARGET"
                elif sl > 0 and hud_trade_ltp <= sl:
                    exit_triggered = "STOP_LOSS"
            else:
                if tgt > 0 and hud_trade_ltp <= tgt:
                    exit_triggered = "TARGET"
                elif sl > 0 and hud_trade_ltp >= sl:
                    exit_triggered = "STOP_LOSS"

            if exit_triggered:
                # Realistic Stop Loss & Target execution bounds (Fix 3)
                tick_size = float(risk_cfg.get("tick_size", 0.05))
                if exit_triggered == "STOP_LOSS":
                    fill_price = round(max(sl - tick_size, hud_trade_ltp), 2) if e_side == "LONG" else round(min(sl + tick_size, hud_trade_ltp), 2)
                elif exit_triggered == "TARGET":
                    fill_price = round(tgt, 2)
                else:
                    fill_price = hud_trade_ltp

                # Full Round-Trip Brokerage Accounting (Fix 4: Buy leg ₹20 + Sell leg ₹20 = ₹40)
                round_trip_charges = float(cost_cfg.get("flat_cost_per_order", 20.0)) * 2

                gross = (fill_price - e_p) * trade_qty if e_side == "LONG" else (e_p - fill_price) * trade_qty
                peak_pct = max(0.0, (fill_price - e_p) / e_p * 100.0) if e_side == "LONG" else max(0.0, (e_p - fill_price) / e_p * 100.0)
                paper_db.close_active_trade(
                    trade_id_or_row_id=row_db_id,
                    exit_time=now_time_str,
                    exit_price=fill_price,
                    exit_reason=exit_triggered,
                    gross_pnl=round(gross, 2),
                    charges=round_trip_charges,
                    peak_gain_pct=round(peak_pct, 2),
                )
                st.session_state.paper_last_exit_time = datetime.now()

                if exit_triggered == "SQUARE_OFF":
                    st.session_state.paper_executing_live = False
                    st.toast(f"🔔 Strategy Square-off ({strategy_sq_time.strftime('%H:%M')}) triggered from Risk Parameters! Position closed.", icon="⏹️")
                else:
                    st.toast(f"⏹️ Trade exited via {exit_triggered} at ₹{fill_price:.2f}! Net P&L: ₹{gross - round_trip_charges:,.2f}", icon="⏹️")
                st.rerun()

    # Display Interactive Plotly Candlestick Chart (Current Date - strictly up to 15:30)
    if candles_df is not None and not candles_df.empty and HAS_PLOTLY:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.04,
            subplot_titles=(
                f"📈 Live Current Date Chart: {active_instrument} ({timeframe_m}m - Market Hours 09:15 to 15:30)"
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
        cur_ltp = float(gateway.get_live_market_depth(active_instrument).get("ltp") or entry_p)
        qty = int(active_open_trade.get("quantity", 65))
        unrealized_pnl = (cur_ltp - entry_p) * qty if side_act == "LONG" else (entry_p - cur_ltp) * qty
        gain_pct = (unrealized_pnl / (entry_p * qty) * 100.0) if entry_p > 0 else 0.0

        st.markdown(f"""
        <div class="{box_class}">
            <div style="font-size: 1.15rem; font-weight: 800; color: {'#00e676' if side_act=='LONG' else '#ff5252'};">
                ⚡ ACTIVE OPEN {side_act} TRADE #{active_open_trade.get('trade_id')} &nbsp;|&nbsp; Symbol: {active_open_trade.get('symbol')}
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
            if st.button("⏹️ Force Square Off Active Trade Now", key="force_sq_off_btn", use_container_width=True):
                now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                paper_db.close_active_trade(
                    trade_id_or_row_id=active_open_trade.get("id"),
                    exit_time=now_ts,
                    exit_price=cur_ltp,
                    exit_reason="MANUAL_SQUARE_OFF",
                    gross_pnl=round(unrealized_pnl, 2),
                    charges=20.0,
                    peak_gain_pct=round(max(0.0, gain_pct), 2),
                )
                st.toast(f"⏹️ Squared off Trade #{active_open_trade.get('trade_id')}!", icon="⏹️")
                st.rerun()

        with btn_c2:
            if not is_mkt_open or datetime.now().time() >= strategy_sq_time:
                sq_btn_label = f"⏹️ Auto Square-off Position (Past {strategy_sq_time.strftime('%H:%M')} Square-off Time)" if datetime.now().time() >= strategy_sq_time else "⏹️ Close Position (Market Closed at 15:30)"
                if st.button(sq_btn_label, key="mkt_closed_sq_off_btn", type="primary", use_container_width=True):
                    exit_reason = "SQUARE_OFF" if datetime.now().time() >= strategy_sq_time else "MARKET_CLOSE_SQUARE_OFF"
                    paper_db.close_active_trade(
                        trade_id_or_row_id=active_open_trade.get("id"),
                        exit_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        exit_price=cur_ltp,
                        exit_reason=exit_reason,
                        gross_pnl=round(unrealized_pnl, 2),
                        charges=20.0,
                        peak_gain_pct=round(max(0.0, gain_pct), 2),
                    )
                    st.success(f"Position #{active_open_trade.get('trade_id')} squared off successfully!")
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
            s_date = str(r.get("session_date", ""))
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
                "Display_ID": st.column_config.TextColumn("Trade Identifier", help="Daily paper trade run identifier mapped to Strategy Name"),
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
                file_name=f"trade_audit_trail_2_{selected_strat}.csv",
                mime="text/csv",
                key="dl_trade_audit_trail_2_paper",
                use_container_width=True,
            )

        styled_trail_2 = trades_2.style.apply(highlight_active_rows, axis=1)
        st.dataframe(
            styled_trail_2,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Display_ID": st.column_config.TextColumn("Trade Identifier", help="Daily paper trade run identifier mapped to Strategy Name"),
                "Instrument": st.column_config.TextColumn("Instrument", help="Active constructed instrument / contract symbol (option chain name)"),
                "Index-LTP": st.column_config.NumberColumn("Index-LTP", help="Underlying Index LTP from oi_strength table at trade entry (used to validate constructed option strike)", format="%.2f"),
                "Option Type": st.column_config.TextColumn("Option Type", help="Option contract type: CE (Call) or PE (Put)"),
            },
        )

    else:
        st.info(f"No paper trades recorded yet for strategy `{selected_strat}` within the previous one month.")

    # ---------------- 2-Second Live Auto Refresh Loop ----------------
    if is_live_running:
        _time.sleep(2.0)
        st.rerun()


if __name__ == "__main__":
    st.set_page_config(
        page_title="Paper Trading Studio",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_paper_trade_module()

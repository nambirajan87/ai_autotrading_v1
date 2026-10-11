import logging
import os
import sys
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
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
    HAS_PLOTLY = False

from app.data.historical_data import load_csv
from app.data.instrument_loader import (
    DEFAULT_INDEX_EXPIRIES,
    INDEX_LOT_SIZES,
    NIFTY_STANDARD_INSTRUMENTS,
    find_equity_by_isin,
    find_equity_by_symbol,
    load_nse_equities,
    parse_expiry_info,
    search_equities,
    validate_isin,
)
from app.database.live_database import get_live_db
from app.execution.live_trade_engine import LiveTradeEngine
from app.gateways.gateway_factory import get_gateway, list_supported_gateways
from app.monitoring.audit_logger import log_audit_event, record_session_visit
from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators
from app.strategy.strategy_store import get_strategy, list_strategy_names

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_kotak_expiries(underlying: str = "NIFTY") -> list:
    try:
        gw = get_gateway("KOTAK")
        if hasattr(gw, "get_expiries"):
            exps = gw.get_expiries(underlying=underlying)
            if exps:
                return exps
    except Exception as e:
        logger.debug("Kotak expiries error: %s", e)
    return DEFAULT_INDEX_EXPIRIES.get(underlying.upper(), DEFAULT_INDEX_EXPIRIES["NIFTY"])


@st.cache_data(ttl=120, show_spinner=False)
def fetch_kotak_option_chain(underlying: str = "NIFTY", expiry: Optional[str] = None) -> dict:
    try:
        gw = get_gateway("KOTAK")
        if hasattr(gw, "get_option_chain"):
            return gw.get_option_chain(underlying=underlying, expiry=expiry)
    except Exception as e:
        logger.debug("Kotak option_chain error: %s", e)
    return {}


@st.cache_data(ttl=60, show_spinner=False)
def search_kotak_instruments(query: str, segment: str = "nse_fo") -> list:
    try:
        gw = get_gateway("KOTAK")
        if hasattr(gw, "smart_search_instruments"):
            return gw.smart_search_instruments(query=query, exchange_segment=segment)
    except Exception as e:
        logger.debug("Kotak smart_search error: %s", e)
    return []

def render_live_trade_module():
    record_session_visit()

    # ---------------- Custom Styling ----------------
    st.markdown("""
    <style>
        .live-card {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
        }
        .neon-pill-green {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 14px;
            font-size: 0.85rem;
            font-weight: 700;
            background-color: rgba(0, 230, 118, 0.18);
            color: #00e676;
            border: 1px solid #00e676;
        }
        .neon-pill-red {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 14px;
            font-size: 0.85rem;
            font-weight: 700;
            background-color: rgba(255, 82, 82, 0.18);
            color: #ff5252;
            border: 1px solid #ff5252;
        }
        .neon-pill-blue {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 14px;
            font-size: 0.85rem;
            font-weight: 700;
            background-color: rgba(0, 176, 255, 0.18);
            color: #00b0ff;
            border: 1px solid #00b0ff;
        }
        .neon-pill-purple {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 14px;
            font-size: 0.85rem;
            font-weight: 700;
            background-color: rgba(124, 77, 255, 0.18);
            color: #b388ff;
            border: 1px solid #7c4dff;
        }
        .pnl-big-profit {
            font-size: 2.2rem;
            font-weight: 800;
            color: #00e676;
            letter-spacing: -0.5px;
        }
        .pnl-big-loss {
            font-size: 2.2rem;
            font-weight: 800;
            color: #ff5252;
            letter-spacing: -0.5px;
        }
        .pnl-big-neutral {
            font-size: 2.2rem;
            font-weight: 800;
            color: #bdbdbd;
            letter-spacing: -0.5px;
        }
    </style>
    """, unsafe_allow_html=True)


    # ---------------- State Initialization ----------------
    def init_live_state():
        if "live_gateway" not in st.session_state:
            st.session_state.live_gateway = "🟢 Upstox Pro"
        if "live_execution_mode" not in st.session_state:
            st.session_state.live_execution_mode = "🛡️ Live Paper Sandbox"
        if "live_strategy_name" not in st.session_state:
            strats = list_strategy_names()
            st.session_state.live_strategy_name = strats[0] if strats else "EMA_RSI_MACD_Balanced"
        if "live_symbol" not in st.session_state:
            st.session_state.live_symbol = "NIFTY26OCT22500PE"
        if "live_isin" not in st.session_state:
            st.session_state.live_isin = "INF000513319"
        if "live_instrument_key" not in st.session_state:
            st.session_state.live_instrument_key = "NSE_FO|51331"
        if "live_equity_name" not in st.session_state:
            st.session_state.live_equity_name = "NIFTY 27-OCT-2026 22500 PE"
        if "live_timeframe" not in st.session_state:
            st.session_state.live_timeframe = 1
        if "live_capital" not in st.session_state:
            st.session_state.live_capital = 100_000.0
        if "live_max_daily_loss" not in st.session_state:
            st.session_state.live_max_daily_loss = 2_000.0
        if "live_lot_size" not in st.session_state:
            st.session_state.live_lot_size = 65
        if "live_engine" not in st.session_state:
            st.session_state.live_engine = None
        if "live_streaming_active" not in st.session_state:
            st.session_state.live_streaming_active = False
        if "live_candles" not in st.session_state:
            st.session_state.live_candles = None
        if "live_candle_cursor" not in st.session_state:
            st.session_state.live_candle_cursor = 0


    init_live_state()

    try:
        equities_df = load_nse_equities()
    except Exception:
        equities_df = pd.DataFrame()

    live_db = get_live_db()

    # ---------------- Sidebar ----------------
    st.sidebar.title("⚡ Live Trading Terminal")
    st.sidebar.caption("Signal-Bar Close Execution Engine")

    # Gateway Toggle
    st.sidebar.markdown("### 🌐 Broker Gateway")
    gw_opts = ["🟢 Upstox Pro", "🔵 Kotak Neo"]
    cur_gw_idx = 0 if "Upstox" in st.session_state.live_gateway else 1
    sel_gw = st.sidebar.radio("Active Broker Gateway", gw_opts, index=cur_gw_idx, key="live_gw_radio")
    st.session_state.live_gateway = sel_gw

    gw_key = "KOTAK" if "Kotak" in sel_gw else "UPSTOX"
    gateway = get_gateway(gw_key)
    gw_info = gateway.get_connection_info()

    if gw_info["connected"]:
        st.sidebar.success(f"**{gw_info['gateway']}**: Online ({gw_info['latency_ms']}ms)")
    else:
        st.sidebar.warning(f"**{gw_info['gateway']}**: Sandbox / Paper Ready")

    st.sidebar.markdown("---")

    # Execution Mode Toggle (Paper Sandbox vs Real Money)
    st.sidebar.markdown("### 🛡️ Order Routing Mode")
    mode_opts = ["🛡️ Live Paper Sandbox", "⚠️ Live Broker API (Real Money)"]
    sel_mode = st.sidebar.radio("Routing Mode", mode_opts, index=0 if "Paper" in st.session_state.live_execution_mode else 1)
    st.session_state.live_execution_mode = sel_mode

    if "Real Money" in sel_mode:
        st.sidebar.error("⚠️ REAL MONEY MODE ACTIVE! Verified market orders will route to exchange upon candle close.")

    st.sidebar.markdown("---")


    # ---------------- Top Navigation & Status Bar ----------------
    t_col1, t_col2, t_col3, t_col4 = st.columns([3, 2, 2, 2])
    with t_col1:
        st.title("⚡ AI Live Trading Terminal")
        st.caption("High-frequency candle listener with instant Signal-Bar Close market order routing.")

    with t_col2:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.metric("System Clock (IST)", now_str)

    with t_col3:
        is_live = st.session_state.live_engine is not None and st.session_state.live_engine.is_active
        st.metric("Engine State", "🟢 ARMED & SCANNING" if is_live else "⚪ STANDBY")

    with t_col4:
        gw_pill = "neon-pill-blue" if "Upstox" in sel_gw else "neon-pill-purple"
        st.markdown(f"<div style='margin-top: 15px;'><span class='{gw_pill}'>{sel_gw}</span></div>", unsafe_allow_html=True)

    st.markdown("---")


    # ---------------- Tab Layout ----------------
    tab_desk, tab_config, tab_history = st.tabs([
        "🖥️ Live Trading Cockpit",
        "⚙️ Strategy & Gateway Setup",
        "📜 Live Executed Journal",
    ])


    # ==============================================================================
    # TAB 1: LIVE TRADING COCKPIT
    # ==============================================================================
    with tab_desk:
        engine: Optional[LiveTradeEngine] = st.session_state.live_engine

        if engine is None:
            st.info("👈 Please initialize the Live Trading Session below or in the **'Strategy & Gateway Setup'** tab.")
            if st.button("🚀 Arm & Launch Live Trading Engine Now", type="primary", use_container_width=True):
                strat_name = st.session_state.live_strategy_name
                strat_dict = get_strategy(strat_name) or {}
                ind_cfg = strat_dict.get("indicators", {})
                tp_pct = float(ind_cfg.get("fixed_target_pct", 30.0))
                sl_pct = float(ind_cfg.get("fixed_stoploss_pct", 5.0))
                tsl_on = bool(ind_cfg.get("trailing_sl_enabled", False))
                trail_beyond = bool(ind_cfg.get("trail_beyond_target", False))

                new_engine = LiveTradeEngine(
                    gateway=gateway,
                    strategy_name=strat_name,
                    symbol=st.session_state.live_symbol,
                    isin=st.session_state.live_isin,
                    timeframe=st.session_state.live_timeframe,
                    capital=st.session_state.live_capital,
                    lot_size=st.session_state.live_lot_size,
                    target_pct=tp_pct,
                    stoploss_pct=sl_pct,
                    trailing_sl_enabled=tsl_on,
                    trail_beyond_target=trail_beyond,
                    max_daily_loss=float(st.session_state.get("live_max_daily_loss", 2_000.0)),
                )
                st.session_state.live_engine = new_engine
                st.rerun()

        else:
            # Emergency Controls & KPI Bar
            kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns([2, 2, 2, 2, 3])
            kpi1.metric("Gateway", engine.gateway.gateway_name)
            kpi2.metric("Symbol", engine.symbol, f"{engine.timeframe}m Timeframe")
            kpi3.metric("Trades Executed", engine.total_trades)
            kpi4.metric("Realized P&L", f"₹{engine.daily_pnl:+,.2f}")

            with kpi5:
                if st.button("🚨 EMERGENCY KILL SWITCH", type="primary", use_container_width=True):
                    engine.emergency_square_off()
                    st.warning("⚠️ KILL SWITCH TRIGGERED: All positions closed at market, engine halted!")
                    st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

            # Active Position Cockpit
            pos = engine.open_position
            if pos:
                side = pos["side"]
                qty = pos["quantity"]
                entry_p = pos["entry_price"]
                entry_t = pos["entry_time"]
                sl_p = pos["stop_loss"]
                tp_p = pos["target"]
                current_p = entry_p  # fallback

                # Current price from last candle or LTP
                if st.session_state.live_candles is not None and not st.session_state.live_candles.empty:
                    current_p = float(st.session_state.live_candles.iloc[-1]["close"])

                unrealized = round(qty * (current_p - entry_p) if side == "LONG" else qty * (entry_p - current_p), 2)
                pnl_class = "pnl-big-profit" if unrealized > 0 else ("pnl-big-loss" if unrealized < 0 else "pnl-big-neutral")

                st.markdown(f"""
                <div class="live-card" style="border-left: 5px solid {'#00e676' if side == 'LONG' else '#ff5252'};">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span class="{ 'neon-pill-green' if side == 'LONG' else 'neon-pill-red' }">{side} {qty} QTY</span>
                            &nbsp;&nbsp;<b>{engine.symbol}</b> | Signal Time: <code>{pos['signal_time']}</code>
                            <div style="margin-top: 5px; color: #bdbdbd; font-size: 0.9rem;">
                                Verified Fill Price: <b>₹{entry_p:.2f}</b> | Fill Time: <b>{entry_t}</b>
                            </div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size: 0.85rem; color: #888;">UNREALIZED MTM P&L</div>
                            <div class="{pnl_class}">₹{unrealized:+,.2f}</div>
                        </div>
                    </div>
                    <div style="margin-top: 15px; display: flex; gap: 20px; font-size: 0.9rem;">
                        <div>🎯 Target: <b>₹{tp_p:.2f}</b> (+{engine.target_pct}%)</div>
                        <div>🛑 Stop Loss: <b>₹{sl_p:.2f}</b> (-{engine.stoploss_pct}%)</div>
                        <div>📈 Current Market: <b>₹{current_p:.2f}</b></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div class="live-card" style="text-align: center; padding: 25px;">
                    <h4 style="color: #bdbdbd; margin: 0;">⚪ No Open Position</h4>
                    <p style="color: #757575; margin: 5px 0 0 0;">Scanning completed candles. Market order will execute immediately upon Signal-Bar Close.</p>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("---")

            # Live Candle Stepper & Streaming Simulator Controls
            st.markdown("#### 📡 Real-Time Candle Listener & Execution Trigger")
            c_step1, c_step2, c_step3 = st.columns([2, 2, 3])

            with c_step1:
                if st.button("⚡ Simulate Completed Candle Close", type="secondary", use_container_width=True):
                    # Load candle feed if not loaded
                    if st.session_state.live_candles is None:
                        tf = engine.timeframe
                        inst = st.session_state.live_instrument_key
                        today = date.today()
                        # Try fetching live / today's data or fall back to October test session
                        try:
                            raw = gateway.fetch_historical_candles(engine.symbol, inst, tf, today - timedelta(days=5), today)
                        except Exception:
                            raw = None
                        if raw is None or raw.empty:
                            # Fallback to local cached data
                            fallbacks = [
                                f"data/{engine.symbol.lower()}_{tf}m.csv",
                                f"data/nifty26oct22500pe_1m.csv",
                                f"data/reliance_5m.csv",
                            ]
                            for fb in fallbacks:
                                if os.path.exists(fb):
                                    raw = load_csv(fb)
                                    break
                        st.session_state.live_candles = raw
                        st.session_state.live_candle_cursor = 100 if raw is not None and len(raw) > 100 else 0

                    candles_df = st.session_state.live_candles
                    if candles_df is not None and st.session_state.live_candle_cursor < len(candles_df):
                        next_candle = candles_df.iloc[st.session_state.live_candle_cursor]
                        st.session_state.live_candle_cursor += 1
                        result = engine.on_candle_close(next_candle)
                        st.success(f"Processed Candle: `{next_candle['timestamp']}` | Close: ₹{next_candle['close']} | Signal: `{result.get('signal')}` (Score: {result.get('score')})")
                        st.rerun()
                    else:
                        st.warning("All available replay candles completed.")

            with c_step2:
                auto_stream = st.toggle("▶️ Auto Live Poll (Every 3s)", value=st.session_state.live_streaming_active)
                st.session_state.live_streaming_active = auto_stream

            with c_step3:
                st.caption("ℹ️ **Signal-Bar Close Policy:** Strategy indicators calculate on the completed candle. Entry order executes instantly at market price; verified fill timestamp & price are recorded.")

            # Interactive Chart of Recent Candle Feed
            if st.session_state.live_candles is not None and not st.session_state.live_candles.empty and HAS_PLOTLY:
                cursor = st.session_state.live_candle_cursor
                display_df = st.session_state.live_candles.iloc[max(0, cursor - 40):cursor]

                if not display_df.empty:
                    fig = go.Figure(data=[go.Candlestick(
                        x=display_df["timestamp"].astype(str),
                        open=display_df["open"],
                        high=display_df["high"],
                        low=display_df["low"],
                        close=display_df["close"],
                        name=engine.symbol,
                    )])
                    fig.update_layout(
                        title=f"Live Feed: {engine.symbol} ({engine.timeframe}m Candle View)",
                        template="plotly_dark",
                        height=380,
                        margin=dict(l=10, r=10, t=40, b=10),
                        xaxis_rangeslider_visible=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)

            # Live Execution Log Feed
            st.markdown("#### 📝 Real-Time Session Audit Trail")
            if engine.logs:
                for log in engine.logs[:10]:
                    lvl_color = "#00e676" if log["level"] == "INFO" else ("#ff5252" if log["level"] in ("WARNING", "CRITICAL") else "#bdbdbd")
                    st.markdown(f"<code>[{log['timestamp']}]</code> <b style='color: {lvl_color};'>[{log['level']}]</b> {log['message']}", unsafe_allow_html=True)
            else:
                st.caption("No log events yet.")


    # ==============================================================================
    # TAB 2: STRATEGY & GATEWAY SETUP
    # ==============================================================================
    with tab_config:
        st.markdown("### ⚙️ Live Trading Configuration")

        cfg_c1, cfg_c2 = st.columns(2)
        with cfg_c1:
            st.markdown("#### 1. Strategy Selection")
            available_strats = list_strategy_names()
            cur_strat_idx = available_strats.index(st.session_state.live_strategy_name) if st.session_state.live_strategy_name in available_strats else 0
            sel_strat = st.selectbox("Select Strategy from Store", available_strats, index=cur_strat_idx)
            st.session_state.live_strategy_name = sel_strat

            strat_data = get_strategy(sel_strat) or {}
            strat_ind = strat_data.get("indicators", {})
            st.markdown(
                f"**Indicators:** {', '.join([k for k, v in strat_ind.items() if v is True]) or 'Default'} | "
                f"**Target:** {strat_ind.get('fixed_target_pct', 30)}% | **Stop Loss:** {strat_ind.get('fixed_stoploss_pct', 5)}%"
            )

            st.markdown("#### 2. Instrument Configuration")

            seg_choice = st.radio(
                "Market Segment",
                ["🎯 NSE F&O (Options & Futures) [Kotak Live Priority]", "🏢 NSE Cash Equity"],
                index=0,
                horizontal=True,
                help="NSE F&O dynamically queries live rolling contracts, weekly/monthly expiries, and strikes directly from Kotak Neo endpoints."
            )

            if "F&O" in seg_choice:
                col_und, col_exp = st.columns(2)
                with col_und:
                    underlying_choice = st.selectbox(
                        "Underlying Index / Stock",
                        ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
                        index=0,
                        key="live_underlying_choice"
                    )
                with col_exp:
                    live_expiries = fetch_kotak_expiries(underlying_choice)
                    sel_expiry = st.selectbox(
                        "Rolling Expiry Date (Kotak Live)",
                        live_expiries,
                        index=0,
                        help="Live weekly and monthly rolling expiries from Kotak Neo.",
                        key=f"live_sel_expiry_{underlying_choice}"
                    )

                col_typ, col_strk = st.columns(2)
                with col_typ:
                    opt_type_choice = st.radio("Option Type", ["PE (Put)", "CE (Call)"], horizontal=True, key="live_opt_type_choice")
                    clean_opt_type = "PE" if "PE" in opt_type_choice else "CE"

                with col_strk:
                    oc_data = fetch_kotak_option_chain(underlying_choice, expiry=sel_expiry) if sel_expiry else {}
                    spot_val = oc_data.get("spot", {}).get("ltp") or oc_data.get("future", {}).get("ltp") or "Live"
                    mkt_lot = int(oc_data.get("common_data", {}).get("mktLot", INDEX_LOT_SIZES.get(underlying_choice, 65)))

                    oc_chain = oc_data.get("put" if clean_opt_type == "PE" else "call", [])
                    strikes = []
                    strike_to_inst = {}
                    for item in oc_chain:
                        inst = item.get("inst", {})
                        strk = inst.get("strkPrc")
                        if strk:
                            strikes.append(strk)
                            strike_to_inst[strk] = inst

                    if not strikes:
                        if underlying_choice == "BANKNIFTY":
                            strikes = [str(x) for x in range(48000, 53500, 100)]
                            spot_val = "50500"
                        elif underlying_choice == "FINNIFTY":
                            strikes = [str(x) for x in range(22000, 25500, 100)]
                            spot_val = "23500"
                        elif underlying_choice == "MIDCPNIFTY":
                            strikes = [str(x) for x in range(11000, 14000, 100)]
                            spot_val = "12500"
                        else:  # NIFTY
                            strikes = [str(x) for x in range(21500, 24600, 50)]
                            spot_val = "22500"

                    try:
                        strikes = sorted(list(set(strikes)), key=lambda x: float(x))
                    except Exception:
                        strikes = sorted(list(set(strikes)))

                    default_strike = "50500" if underlying_choice == "BANKNIFTY" else ("23500" if underlying_choice == "FINNIFTY" else ("12500" if underlying_choice == "MIDCPNIFTY" else "22500"))
                    default_idx = strikes.index(default_strike) if default_strike in strikes else len(strikes) // 2
                    chosen_strike = st.selectbox(
                        f"Strike Price (Spot: ~{spot_val})",
                        strikes,
                        index=default_idx,
                        key=f"live_chosen_strike_{underlying_choice}"
                    )
                    selected_inst = strike_to_inst.get(chosen_strike, {})
                    display_dt, sym_code = parse_expiry_info(sel_expiry)

                    if selected_inst:
                        st.session_state.live_symbol = selected_inst.get("symbol", f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}")
                        st.session_state.live_isin = selected_inst.get("neoSymbol", "").split("|")[-1]
                        st.session_state.live_instrument_key = selected_inst.get("neoSymbol", f"NSE_FO|{chosen_strike}")
                        st.session_state.live_equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                        st.session_state.live_lot_size = mkt_lot
                    else:
                        target_symbol = f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}"
                        found_std = None
                        for s_inst in NIFTY_STANDARD_INSTRUMENTS:
                            if s_inst["tradingsymbol"] == target_symbol:
                                found_std = s_inst
                                break
                        if found_std:
                            st.session_state.live_symbol = found_std["tradingsymbol"]
                            st.session_state.live_isin = found_std["isin"]
                            st.session_state.live_instrument_key = found_std["instrument_key"]
                            st.session_state.live_equity_name = found_std["name"]
                            st.session_state.live_lot_size = int(found_std.get("lot_size", mkt_lot))
                        else:
                            st.session_state.live_symbol = target_symbol
                            st.session_state.live_isin = f"INF00{chosen_strike}1"
                            st.session_state.live_instrument_key = f"NSE_FO|{chosen_strike}"
                            st.session_state.live_equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                            st.session_state.live_lot_size = mkt_lot

                st.caption(f"🎯 **Active Selection:** `{st.session_state.live_symbol}` | Expiry: `{display_dt}` | Key: `{st.session_state.live_instrument_key}` | Lot: `{st.session_state.live_lot_size}`")

            else:
                search_sym = st.text_input("Search Cash Equity", value=st.session_state.live_symbol)
                matched = search_equities(search_sym, equities_df, limit=10) if not equities_df.empty else pd.DataFrame()
                if not matched.empty:
                    match_opts = [f"{r['tradingsymbol']} - {r['name']} ({r['isin']})" for _, r in matched.iterrows()]
                    chosen_inst = st.selectbox("Matching Instruments", match_opts, index=0)
                    if chosen_inst:
                        sym_sel = chosen_inst.split(" - ")[0].strip()
                        f_eq = find_equity_by_symbol(sym_sel, equities_df)
                        if f_eq:
                            st.session_state.live_symbol = f_eq["tradingsymbol"]
                            st.session_state.live_isin = f_eq["isin"]
                            st.session_state.live_instrument_key = f_eq.get("instrument_key", f"NSE_EQ|{f_eq['isin']}")
                            st.session_state.live_equity_name = f_eq["name"]
                            st.session_state.live_lot_size = 1

        with cfg_c2:
            st.markdown("#### 3. Execution Parameters")
            tf_options = [1, 3, 5, 15]
            cur_tf = tf_options.index(st.session_state.live_timeframe) if st.session_state.live_timeframe in tf_options else 0
            st.session_state.live_timeframe = st.selectbox("Candle Timeframe", tf_options, index=cur_tf, format_func=lambda x: f"{x} Minute" if x == 1 else f"{x} Minutes")

            c_lot, c_cap, c_loss = st.columns(3)
            with c_lot:
                st.session_state.live_lot_size = st.number_input("Order Lot Size (Qty)", min_value=1, max_value=5000, value=int(st.session_state.live_lot_size), step=65)
            with c_cap:
                st.session_state.live_capital = st.number_input("Allocated Capital (₹)", min_value=10_000.0, max_value=10_000_000.0, value=float(st.session_state.live_capital), step=10_000.0)
            with c_loss:
                st.session_state.live_max_daily_loss = st.number_input("Max Daily Loss Limit (₹)", min_value=500.0, max_value=500_000.0, value=float(st.session_state.get("live_max_daily_loss", 2_000.0)), step=500.0, help="Trading halts immediately if daily realized loss hits this limit.")

            st.markdown("#### 4. Active Broker Gateway Info")
            st.json(gateway.get_connection_info())

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Apply & Arm Live Session", type="primary", use_container_width=True):
            strat_name = st.session_state.live_strategy_name
            strat_dict = get_strategy(strat_name) or {}
            ind_cfg = strat_dict.get("indicators", {})

            st.session_state.live_engine = LiveTradeEngine(
                gateway=gateway,
                strategy_name=strat_name,
                symbol=st.session_state.live_symbol,
                isin=st.session_state.live_isin,
                timeframe=st.session_state.live_timeframe,
                capital=st.session_state.live_capital,
                lot_size=st.session_state.live_lot_size,
                target_pct=float(ind_cfg.get("fixed_target_pct", 30.0)),
                stoploss_pct=float(ind_cfg.get("fixed_stoploss_pct", 5.0)),
                trailing_sl_enabled=bool(ind_cfg.get("trailing_sl_enabled", False)),
                trail_beyond_target=bool(ind_cfg.get("trail_beyond_target", False)),
                max_daily_loss=float(st.session_state.get("live_max_daily_loss", 2_000.0)),
            )
            st.success(f"Live Trading Engine successfully armed with {gateway.gateway_name} Gateway!")
            st.rerun()


    # ==============================================================================
    # TAB 3: LIVE EXECUTED JOURNAL
    # ==============================================================================
    with tab_history:
        st.markdown("### 📜 Live Executed Trades Journal")

        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            f_strat = st.selectbox("Filter Strategy", ["ALL"] + list_strategy_names(), index=0)
        with f_col2:
            f_sym = st.selectbox("Filter Symbol", ["ALL", "NIFTY26OCT22500PE", "RELIANCE", "NIFTY", "BANKNIFTY"], index=0)
        with f_col3:
            f_gw = st.selectbox("Filter Gateway", ["ALL", "UPSTOX", "KOTAK"], index=0)

        strat_arg = None if f_strat == "ALL" else f_strat
        sym_arg = None if f_sym == "ALL" else f_sym
        gw_arg = None if f_gw == "ALL" else f_gw

        trades = live_db.get_trades(strategy_name=strat_arg, symbol=sym_arg, gateway=gw_arg)
        orders = live_db.get_orders()

        if trades:
            trades_df = pd.DataFrame(trades)
            total_pnl = trades_df["net_pnl"].sum()
            win_count = len(trades_df[trades_df["net_pnl"] > 0])
            win_rate = (win_count / len(trades_df)) * 100.0

            j_kpi1, j_kpi2, j_kpi3, j_kpi4 = st.columns(4)
            j_kpi1.metric("Total Live Trades", len(trades_df))
            j_kpi2.metric("Win Rate", f"{win_rate:.1f}%", f"{win_count}W / {len(trades_df) - win_count}L")
            j_kpi3.metric("Net Realized P&L", f"₹{total_pnl:+,.2f}")
            j_kpi4.metric("Total Charges", f"₹{trades_df['charges'].sum():,.2f}")

            st.markdown("---")
            st.markdown("#### 📋 Executed Trades Ledger")
            st.dataframe(
                trades_df[[
                    "id", "gateway", "strategy_name", "symbol", "side", "quantity",
                    "signal_time", "entry_time", "entry_price", "exit_time", "exit_price",
                    "exit_reason", "gross_pnl", "charges", "net_pnl"
                ]],
                use_container_width=True,
                hide_index=True,
            )

            # Export buttons
            exp_col1, exp_col2 = st.columns(2)
            with exp_col1:
                csv_data = trades_df.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Download Live Trades (CSV)", csv_data, "live_trades.csv", "text/csv")
            with exp_col2:
                json_data = trades_df.to_json(orient="records", indent=2)
                st.download_button("📥 Download Audit Trail (JSON)", json_data, "live_audit.json", "application/json")
        else:
            st.info("No live trades recorded yet in the database. Arm the engine and run trades in Cockpit.")

        if orders:
            st.markdown("---")
            st.markdown("#### 📑 Order Book Audit")
            orders_df = pd.DataFrame(orders)
            st.dataframe(
                orders_df[["order_id", "gateway", "timestamp", "symbol", "side", "quantity", "price", "status", "reason"]],
                use_container_width=True,
                hide_index=True,
            )

if __name__ == "__main__":
    st.set_page_config(
        page_title="AI Live Trading Terminal",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_live_trade_module()

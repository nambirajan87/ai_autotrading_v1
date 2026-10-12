import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

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
from app.data.upstox_loader import fetch_range
from app.database.paper_database import get_paper_db
from app.execution.paper_simulation_session import PaperSimulationSession
from app.gateways.gateway_factory import get_gateway
from app.monitoring.audit_logger import log_audit_event, record_session_visit
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
def fetch_kotak_option_chain(underlying: str = "NIFTY", expiry: Optional[str] = None, count: int = 250) -> dict:
    try:
        gw = get_gateway("KOTAK")
        if hasattr(gw, "get_option_chain"):
            return gw.get_option_chain(underlying=underlying, expiry=expiry, count=count)
    except Exception as e:
        logger.debug("Kotak option_chain error: %s", e)
    return {}


def render_paper_trade_module():
    # Record visit for auditing
    record_session_visit()

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
        .badge-pill {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.82rem;
            font-weight: 600;
            margin-right: 5px;
        }
        .badge-long { background-color: rgba(38, 166, 154, 0.25); color: #26a69a; border: 1px solid #26a69a; }
        .badge-short { background-color: rgba(239, 83, 80, 0.25); color: #ef5350; border: 1px solid #ef5350; }
        .badge-flat { background-color: rgba(158, 158, 158, 0.25); color: #bdbdbd; border: 1px solid #9e9e9e; }
    </style>
    """, unsafe_allow_html=True)


    # ---------------- Session State Initialization ----------------
    def init_paper_state():
        if "paper_nav_mode" not in st.session_state:
            st.session_state.paper_nav_mode = "A. Paper Trade Setup"
        if "paper_history_strategy" not in st.session_state:
            st.session_state.paper_history_strategy = "EMA_RSI_MACD_Balanced"

        if "paper_tab" not in st.session_state:
            st.session_state.paper_tab = "1. Setup & Configuration"

        saved_strats = list_strategy_names()
        if "paper_strategy_name" not in st.session_state:
            st.session_state.paper_strategy_name = saved_strats[0] if saved_strats else "EMA_RSI_MACD_Balanced"

        if "paper_symbol" not in st.session_state:
            st.session_state.paper_symbol = "RELIANCE"
        if "paper_isin" not in st.session_state:
            st.session_state.paper_isin = "INE002A01018"
        if "paper_equity_name" not in st.session_state:
            st.session_state.paper_equity_name = "RELIANCE INDUSTRIES LTD"
        if "paper_instrument_key" not in st.session_state:
            st.session_state.paper_instrument_key = "NSE_EQ|INE002A01018"
        if "paper_date" not in st.session_state or st.session_state.paper_date == date(2026, 9, 30):
            st.session_state.paper_date = date.today()
        if "paper_timeframe" not in st.session_state:
            st.session_state.paper_timeframe = 5
        if "paper_capital" not in st.session_state:
            st.session_state.paper_capital = 100_000.0
        if "paper_gateway" not in st.session_state or st.session_state.paper_gateway == "🟢 Upstox Pro":
            st.session_state.paper_gateway = "🔵 Kotak Neo"
        if "paper_market_segment" not in st.session_state:
            st.session_state.paper_market_segment = "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]"
        if "paper_lot_size" not in st.session_state:
            st.session_state.paper_lot_size = 65

        if "paper_session" not in st.session_state:
            st.session_state.paper_session = None


    init_paper_state()

    # Load instruments
    try:
        equities_df = load_nse_equities()
    except Exception:
        equities_df = pd.DataFrame()


    # ---------------- Sidebar Navigation ----------------
    st.sidebar.title("📈 Paper Trading Studio")
    st.sidebar.caption("Candle-by-Candle Strategy Simulator & Trade Journal")

    nav_modes = ["A. Paper Trade Setup", "B. Paper Trading History"]
    sel_mode = st.sidebar.radio(
        "Select Mode",
        nav_modes,
        index=nav_modes.index(st.session_state.paper_nav_mode) if st.session_state.paper_nav_mode in nav_modes else 0,
        key="sidebar_mode_radio",
    )
    if sel_mode != st.session_state.paper_nav_mode:
        st.session_state.paper_nav_mode = sel_mode
        st.rerun()

    st.sidebar.markdown("---")

    paper_db = get_paper_db()

    if st.session_state.paper_nav_mode == "A. Paper Trade Setup":
        tabs = ["1. Setup & Configuration", "2. Interactive Trading Desk", "3. Analytics & Trade Log"]
        sel_tab = st.sidebar.radio("Navigation", tabs, index=tabs.index(st.session_state.paper_tab))
        if sel_tab != st.session_state.paper_tab:
            st.session_state.paper_tab = sel_tab
            st.rerun()

        st.sidebar.markdown("---")
        if st.session_state.paper_session is not None:
            session = st.session_state.paper_session
            st.sidebar.markdown("### 📌 Active Session")
            st.sidebar.write(f"**Symbol:** `{session.symbol}`")
            st.sidebar.write(f"**Strategy:** `{session.strategy_name}`")
            st.sidebar.write(f"**Date:** `{session.date_str}`")
            st.sidebar.write(f"**Progress:** `{session.feed.current_index}/{session.feed.total_candles}` ({session.feed.progress_pct}%)")

            bal = session.broker.get_balance()
            mtm = session.get_state()["mtm_pnl"]
            tot_eq = bal["cash"] + mtm
            color = "green" if tot_eq >= session.initial_capital else "red"
            st.sidebar.markdown(f"**Virtual Equity:** :{color}[₹{tot_eq:,.2f}]")
            st.sidebar.markdown(f"**Realized P&L:** ₹{bal['net_realized_pnl']:+,.2f}")

            if st.sidebar.button("🔄 Reset Current Session", use_container_width=True):
                st.session_state.paper_session = None
                st.session_state.paper_tab = "1. Setup & Configuration"
                st.rerun()


    else:
        # Mode B: Paper Trading History
        st.sidebar.markdown("### 📜 Strategy Journal Filters")
        all_strat_names = list_strategy_names()
        db_strats = paper_db.get_all_strategy_names()
        combined_strats = sorted(list(set(all_strat_names + db_strats)))
        if not combined_strats:
            combined_strats = ["EMA_RSI_MACD_Balanced"]

        if st.session_state.paper_history_strategy not in combined_strats:
            st.session_state.paper_history_strategy = "EMA_RSI_MACD_Balanced" if "EMA_RSI_MACD_Balanced" in combined_strats else combined_strats[0]

        hist_strat = st.sidebar.selectbox(
            "Strategies-List",
            combined_strats,
            index=combined_strats.index(st.session_state.paper_history_strategy),
            key="sidebar_hist_strategy_select",
        )
        if hist_strat != st.session_state.paper_history_strategy:
            st.session_state.paper_history_strategy = hist_strat
            st.rerun()

        # Symbol filter
        all_trades = paper_db.get_trades_for_strategy(hist_strat)
        symbols_list = sorted(list(set([t["symbol"] for t in all_trades]))) if all_trades else []
        hist_sym_filter = st.sidebar.selectbox("Filter Symbol", ["ALL"] + symbols_list, index=0)

        # Outcome filter
        outcome_choice = st.sidebar.radio("Filter Outcome", ["ALL", "WINS", "LOSSES"], index=0)

        st.sidebar.markdown("---")
        if st.sidebar.button("🚀 Replay this Strategy in Desk", use_container_width=True, type="primary"):
            st.session_state.paper_strategy_name = hist_strat
            st.session_state.paper_nav_mode = "A. Paper Trade Setup"
            st.session_state.paper_tab = "1. Setup & Configuration"
            st.rerun()

        with st.sidebar.expander("⚠️ Danger Zone"):
            confirm_clear = st.checkbox("Confirm deletion of this history", key="confirm_clear_strat")
            if st.button("🗑️ Clear History", disabled=not confirm_clear, use_container_width=True):
                paper_db.clear_history_for_strategy(hist_strat)
                st.success(f"Cleared history for {hist_strat}!")
                st.rerun()



    # ---------------- Paper Trading History Render Function ----------------
    def render_paper_trading_history(strategy_name: str, symbol_filter: str, outcome_filter: str):
        st.header(f"📜 Paper Trading History: `{strategy_name}`")

        # Strategy definition summary badge bar
        strat_def = get_strategy(strategy_name)
        if strat_def:
            ind = strat_def.get("indicators", {})
            risk = strat_def.get("risk", {})
            side = ind.get("trade_side", risk.get("trade_side", "BOTH"))
            desc = strat_def.get("description", "Custom Consensus Strategy")

            b_html = f"<span class='badge-pill badge-{'long' if side=='LONG' else ('short' if side=='SHORT' else 'flat')}'>Side: {side}</span>"
            if ind.get("ema_enabled"): b_html += "<span class='badge-pill badge-flat'>EMA</span>"
            if ind.get("rsi_enabled"): b_html += "<span class='badge-pill badge-flat'>RSI</span>"
            if ind.get("macd_enabled"): b_html += "<span class='badge-pill badge-flat'>MACD</span>"
            if ind.get("vwap_enabled"): b_html += "<span class='badge-pill badge-flat'>VWAP</span>"
            if ind.get("supertrend_enabled"): b_html += "<span class='badge-pill badge-flat'>Supertrend</span>"
            if ind.get("volume_enabled"): b_html += "<span class='badge-pill badge-flat'>Volume</span>"
            st.markdown(b_html, unsafe_allow_html=True)
            st.caption(f"Strategy Notes: {desc}")
        else:
            st.caption("Strategy settings loaded from persistent paper trading records.")

        sym_arg = None if symbol_filter == "ALL" else symbol_filter
        metrics = paper_db.get_strategy_metrics(strategy_name, symbol=sym_arg, outcome_filter=outcome_filter)
        trades = paper_db.get_trades_for_strategy(strategy_name, symbol=sym_arg, outcome_filter=outcome_filter)
        runs = paper_db.get_runs_for_strategy(strategy_name, symbol=sym_arg, outcome_filter=outcome_filter)

        total_unfiltered_trades = len(paper_db.get_trades_for_strategy(strategy_name))
        if total_unfiltered_trades == 0:
            st.info(f"No trades recorded yet for strategy `{strategy_name}`.")
            btn_c1, btn_c2 = st.columns([1, 3])
            with btn_c1:
                if st.button("🚀 Run Paper Trade for this Strategy", type="primary"):
                    st.session_state.paper_strategy_name = strategy_name
                    st.session_state.paper_nav_mode = "A. Paper Trade Setup"
                    st.session_state.paper_tab = "1. Setup & Configuration"
                    st.rerun()
            return

        filter_desc = []
        if symbol_filter != "ALL":
            filter_desc.append(f"Symbol: `{symbol_filter}`")
        if outcome_filter != "ALL":
            filter_desc.append(f"Outcome: `{outcome_filter}`")
        if filter_desc:
            st.caption(f"🔍 Active Filter: {' | '.join(filter_desc)} ({metrics['total_trades']} matching trades)")

        # Row 1 KPI Metrics
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
        kpi1.metric("Total Trades", metrics["total_trades"])
        kpi2.metric("Win Rate", f"{metrics['win_rate']:.1f}%", f"{metrics['win_trades']}W / {metrics['loss_trades']}L")
        kpi3.metric("Gross P&L", f"₹{metrics['gross_pnl']:+,.2f}")
        kpi4.metric("Brokerage & Taxes", f"₹{metrics['total_charges']:,.2f}")
        kpi5.metric("Net Realized P&L", f"₹{metrics['net_pnl']:+,.2f}")

        # Row 2 Secondary Metrics
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Profit Factor", f"{metrics['profit_factor']:.2f}")
        s2.metric("Avg Trade P&L", f"₹{metrics['avg_trade_pnl']:+,.2f}")
        s3.metric("Best Trade", f"₹{metrics['best_trade']:+,.2f}")
        s4.metric("Worst Trade", f"₹{metrics['worst_trade']:+,.2f}")
        s5.metric("Sessions Run", len(runs))

        st.markdown("---")

        # Interactive Visualizations
        hist_tab1, hist_tab2 = st.tabs(["📈 Cumulative Net P&L Curve", "📊 Trade P&L Distribution"])

        # Prepare DataFrame sorted chronologically
        df_raw = pd.DataFrame(trades).sort_values("id", ascending=True).reset_index(drop=True)
        df_raw["cumulative_net_pnl"] = df_raw["net_pnl"].cumsum()
        df_raw["trade_num"] = range(1, len(df_raw) + 1)

        with hist_tab1:
            if HAS_PLOTLY and not df_raw.empty:
                cum_fig = go.Figure()
                cum_fig.add_trace(go.Scatter(
                    x=df_raw["trade_num"],
                    y=df_raw["cumulative_net_pnl"],
                    mode="lines+markers",
                    name="Cumulative Net P&L (₹)",
                    line=dict(color="#00e676" if metrics["net_pnl"] >= 0 else "#ff1744", width=2.5),
                    marker=dict(size=8, color="#29b6f6"),
                    hovertemplate="<b>Trade #%{x}</b><br>Cumulative P&L: ₹%{y:,.2f}<extra></extra>",
                ))
                cum_fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)")
                cum_fig.update_layout(
                    height=340,
                    margin=dict(l=10, r=10, t=10, b=10),
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Trade Number",
                    yaxis_title="Net Realized P&L (₹)",
                )
                st.plotly_chart(cum_fig, use_container_width=True)
            else:
                st.line_chart(df_raw.set_index("trade_num")["cumulative_net_pnl"])

        with hist_tab2:
            if HAS_PLOTLY and not df_raw.empty:
                bar_colors = ["#00e676" if p >= 0 else "#ff1744" for p in df_raw["net_pnl"]]
                bar_fig = go.Figure(data=[
                    go.Bar(
                        x=df_raw["trade_num"],
                        y=df_raw["net_pnl"],
                        marker_color=bar_colors,
                        hovertemplate="<b>Trade #%{x}</b><br>P&L: ₹%{y:+,.2f}<extra></extra>",
                    )
                ])
                bar_fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)")
                bar_fig.update_layout(
                    height=340,
                    margin=dict(l=10, r=10, t=10, b=10),
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Trade Number",
                    yaxis_title="Trade Net P&L (₹)",
                )
                st.plotly_chart(bar_fig, use_container_width=True)
            else:
                st.bar_chart(df_raw.set_index("trade_num")["net_pnl"])

        st.markdown("---")

        # Detailed Trade Ledger
        st.subheader(f"📋 Executed Trades Ledger ({len(trades)} trades)")
        display_cols = [
            "trade_id", "session_date", "symbol", "side", "quantity",
            "entry_time", "entry_price", "exit_time", "exit_price",
            "exit_reason", "gross_pnl", "charges", "net_pnl", "score"
        ]
        avail_cols = [c for c in display_cols if c in df_raw.columns]
        view_df = df_raw[avail_cols].sort_values("trade_id", ascending=False).reset_index(drop=True)
        view_df.rename(columns={
            "trade_id": "Trade #",
            "session_date": "Session Date",
            "symbol": "Symbol",
            "side": "Side",
            "quantity": "Qty",
            "entry_time": "Entry Time",
            "entry_price": "Entry (₹)",
            "exit_time": "Exit Time",
            "exit_price": "Exit (₹)",
            "exit_reason": "Exit Reason",
            "gross_pnl": "Gross P&L (₹)",
            "charges": "Charges (₹)",
            "net_pnl": "Net P&L (₹)",
            "score": "Score",
        }, inplace=True)

        st.dataframe(view_df, use_container_width=True, hide_index=True)

        # Downloads
        dl_c1, dl_c2, _ = st.columns([1, 1, 2])
        with dl_c1:
            csv_bytes = view_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download Trades (CSV)",
                data=csv_bytes,
                file_name=f"paper_trades_{strategy_name}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with dl_c2:
            import json
            json_bytes = json.dumps(trades, indent=2, default=str).encode("utf-8")
            st.download_button(
                "⬇️ Download Audit (JSON)",
                data=json_bytes,
                file_name=f"paper_trades_{strategy_name}.json",
                mime="application/json",
                use_container_width=True,
            )

        # Session Execution Breakdown Expander
        runs_filter_info = []
        if symbol_filter != "ALL":
            runs_filter_info.append(f"Symbol: {symbol_filter}")
        if outcome_filter != "ALL":
            runs_filter_info.append(f"Outcome: {outcome_filter}")
        runs_filter_str = f" [{', '.join(runs_filter_info)}]" if runs_filter_info else ""

        with st.expander(f"📑 Session Execution Runs History ({len(runs)} sessions){runs_filter_str}", expanded=(len(runs) > 0)):
            if runs:
                r_cols = ["id", "created_at", "session_date", "symbol", "timeframe", "total_trades", "win_rate", "gross_pnl", "net_pnl", "status"]
                avail_r_cols = [c for c in r_cols if c in pd.DataFrame(runs).columns]
                runs_df = pd.DataFrame(runs)[avail_r_cols]
                runs_df.rename(columns={
                    "id": "Run ID",
                    "created_at": "Logged At",
                    "session_date": "Date",
                    "symbol": "Symbol",
                    "timeframe": "TF (m)",
                    "total_trades": "Trades",
                    "win_rate": "Win %",
                    "gross_pnl": "Gross P&L (₹)",
                    "net_pnl": "Net P&L (₹)",
                    "status": "Status",
                }, inplace=True)
                st.dataframe(runs_df, use_container_width=True, hide_index=True)
            else:
                st.info(f"No session runs match the current filter criteria{runs_filter_str}.")

        # Cross-Strategy Comparison Benchmark Expander
        bench_filter_str = f" [{', '.join(runs_filter_info)}]" if runs_filter_info else " [All Instruments & Trades]"
        with st.expander(f"📊 All Strategies Performance Benchmark{bench_filter_str}", expanded=True):
            all_db_strats = paper_db.get_all_strategy_names()
            benchmark_rows = []
            for s_name in all_db_strats:
                m = paper_db.get_strategy_metrics(s_name, symbol=sym_arg, outcome_filter=outcome_filter)
                if m["total_trades"] > 0:
                    benchmark_rows.append({
                        "Strategy Name": s_name,
                        "Total Trades": m["total_trades"],
                        "Win Rate (%)": f"{m['win_rate']:.1f}%",
                        "Gross P&L (₹)": m["gross_pnl"],
                        "Taxes & Charges (₹)": m["total_charges"],
                        "Net P&L (₹)": m["net_pnl"],
                        "Profit Factor": m["profit_factor"],
                        "Avg Trade (₹)": m["avg_trade_pnl"],
                        "Best Trade (₹)": m["best_trade"],
                        "Worst Trade (₹)": m["worst_trade"],
                    })
            if benchmark_rows:
                st.dataframe(pd.DataFrame(benchmark_rows), use_container_width=True, hide_index=True)
            else:
                st.info(f"No strategy benchmark records match the current filter criteria{bench_filter_str}.")


    # If user selected B. Paper Trading History, render history and return
    if st.session_state.paper_nav_mode == "B. Paper Trading History":
        render_paper_trading_history(
            strategy_name=st.session_state.paper_history_strategy,
            symbol_filter=hist_sym_filter,
            outcome_filter=outcome_choice,
        )
        return



    # ==============================================================================
    # TAB 1: Setup & Configuration
    # ==============================================================================
    if st.session_state.paper_tab == "1. Setup & Configuration":
        st.header("Step 1: Session Setup & Strategy Selection")
        st.caption("Select a saved Strategy from your Strategies-List, choose an Instrument and Date, then launch the candle-by-candle simulation desk.")

        saved_strats = list_strategy_names()
        curr_idx = saved_strats.index(st.session_state.paper_strategy_name) if st.session_state.paper_strategy_name in saved_strats else 0

        st.markdown("#### 🎯 1. Choose Strategy (from Strategies-List)")
        col_s1, col_s2 = st.columns([3, 1])
        with col_s1:
            st.session_state.paper_strategy_name = st.selectbox(
                "Strategy Name",
                saved_strats,
                index=curr_idx,
                help="Choose which saved strategy to test in paper trading.",
            )
        with col_s2:
            st.write("")
            st.write("")
            strat_dict = get_strategy(st.session_state.paper_strategy_name)

        # Strategy info card
        if strat_dict:
            ind = strat_dict.get("indicators", {})
            risk = strat_dict.get("risk", {})
            active_inds = [k.replace('_enabled', '').upper() for k, v in ind.items() if k.endswith('_enabled') and v]
            tp = ind.get("fixed_target_pct", risk.get("fixed_target_pct", 3))
            sl = ind.get("fixed_stoploss_pct", risk.get("fixed_stoploss_pct", 2))
            tsl = "Active (Model B)" if (ind.get("trailing_sl_enabled") or risk.get("trailing_sl_enabled")) else "Off"
            runner = " + Runner Mode" if (ind.get("trail_beyond_target") or risk.get("trail_beyond_target")) else ""

            st.info(
                f"📌 **Active Strategy:** `{st.session_state.paper_strategy_name}` | "
                f"**Indicators ({len(active_inds)}):** {', '.join(active_inds) if active_inds else 'None'} | "
                f"**Target / SL:** {tp}% / {sl}% | "
                f"**Trailing SL:** {tsl}{runner} | "
                f"**Long Threshold:** +{ind.get('long_threshold', 5)} | **Short Threshold:** {ind.get('short_threshold', -5)}"
            )

        st.markdown("---")
        st.markdown("#### 🌐 2. Broker Gateway")
        gw_col1, gw_col2 = st.columns([1, 1])
        with gw_col1:
            gw_options = ["🟢 Upstox Pro", "🔵 Kotak Neo"]
            cur_gw_idx = 0 if "Upstox" in st.session_state.paper_gateway else 1
            sel_gw = st.radio(
                "Select Broker Gateway / Market Feed",
                gw_options,
                index=cur_gw_idx,
                horizontal=True,
                key="paper_gw_radio",
            )
            st.session_state.paper_gateway = sel_gw
        with gw_col2:
            from app.gateways.gateway_factory import get_gateway
            chosen_gw_key = "KOTAK" if "Kotak" in sel_gw else "UPSTOX"
            gw_instance = get_gateway(chosen_gw_key)
            gw_info = gw_instance.get_connection_info()
            st.markdown(
                f"**Active Gateway:** `{gw_info['gateway']}` &nbsp;|&nbsp; "
                f"**Status:** `{'🟢 ' + gw_info['status'] if gw_info['connected'] else '🟡 ' + gw_info['status']}`"
            )
            if "Kotak" in sel_gw:
                st.caption("ℹ️ **Kotak Neo:** Trade-Free Intraday Pricing (₹0 Brokerage on Equities). Replay feeds from Kotak API / Cache.")
            else:
                st.caption("ℹ️ **Upstox Pro:** Direct WebSocket & Historical Multi-Asset REST API.")

        st.markdown("---")
        st.markdown("#### 🏢 3. Instrument Configuration & Replay Date")

        seg_choice = st.radio(
            "Market Segment",
            ["🎯 NSE F&O (Options & Futures) [Kotak Live Priority]", "🏢 NSE Cash Equity"],
            index=0 if "F&O" in st.session_state.get("paper_market_segment", "F&O") else 1,
            horizontal=True,
            key="paper_market_segment_radio",
            help="NSE F&O dynamically queries live rolling contracts, weekly/monthly expiries, and strikes directly from Kotak Neo endpoints."
        )
        st.session_state.paper_market_segment = seg_choice
        is_fo = "F&O" in seg_choice

        is_valid_isin = True

        if is_fo:
            col_und, col_exp = st.columns(2)
            with col_und:
                underlying_choice = st.selectbox(
                    "Underlying Index / Stock",
                    ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
                    index=0,
                    key="paper_underlying_choice"
                )
            with col_exp:
                live_expiries = fetch_kotak_expiries(underlying_choice)
                sel_expiry = st.selectbox(
                    "Rolling Expiry Date (Kotak Live)",
                    live_expiries,
                    index=0,
                    help="Live weekly and monthly rolling expiries from Kotak Neo.",
                    key=f"paper_sel_expiry_{underlying_choice}"
                )

            col_typ, col_strk = st.columns(2)
            with col_typ:
                opt_type_choice = st.radio("Option Type", ["PE (Put)", "CE (Call)"], horizontal=True, key="paper_opt_type_choice")
                clean_opt_type = "PE" if "PE" in opt_type_choice else "CE"

            with col_strk:
                oc_data = fetch_kotak_option_chain(underlying_choice, expiry=sel_expiry, count=250) if sel_expiry else {}
                spot_val = oc_data.get("spot", {}).get("ltp") or oc_data.get("future", {}).get("ltp") or "Live"
                mkt_lot = int(oc_data.get("common_data", {}).get("mktLot", INDEX_LOT_SIZES.get(underlying_choice, 65)))

                # Collect all available strikes from both Call and Put chains
                all_strikes = set()
                for item in oc_data.get("call", []):
                    s = item.get("inst", {}).get("strkPrc")
                    if s:
                        all_strikes.add(s)
                for item in oc_data.get("put", []):
                    s = item.get("inst", {}).get("strkPrc")
                    if s:
                        all_strikes.add(s)

                oc_chain = oc_data.get("put" if clean_opt_type == "PE" else "call", [])
                strike_to_inst = {}
                for item in oc_chain:
                    inst = item.get("inst", {})
                    strk = inst.get("strkPrc")
                    if strk:
                        strike_to_inst[strk] = inst

                strikes = list(all_strikes) if all_strikes else list(strike_to_inst.keys())

                if not strikes:
                    if underlying_choice == "BANKNIFTY":
                        strikes = [str(x) for x in range(40000, 68100, 100)]
                        spot_val = "50500"
                    elif underlying_choice == "FINNIFTY":
                        strikes = [str(x) for x in range(17000, 32050, 50)]
                        spot_val = "23500"
                    elif underlying_choice == "MIDCPNIFTY":
                        strikes = [str(x) for x in range(8500, 18050, 50)]
                        spot_val = "12500"
                    else:  # NIFTY
                        strikes = [str(x) for x in range(17000, 30050, 50)]
                        spot_val = "22500"

                try:
                    strikes = sorted(list(set(strikes)), key=lambda x: float(x))
                except Exception:
                    strikes = sorted(list(set(strikes)))

                default_idx = len(strikes) // 2
                try:
                    s_num = float(spot_val)
                    closest_strike = min(strikes, key=lambda x: abs(float(x) - s_num))
                    default_idx = strikes.index(closest_strike)
                except Exception:
                    default_strike = "50500" if underlying_choice == "BANKNIFTY" else ("23500" if underlying_choice == "FINNIFTY" else ("12500" if underlying_choice == "MIDCPNIFTY" else "22500"))
                    if default_strike in strikes:
                        default_idx = strikes.index(default_strike)

                strike_key = f"paper_chosen_strike_{underlying_choice}"
                if strike_key in st.session_state and st.session_state[strike_key] not in strikes:
                    st.session_state[strike_key] = strikes[default_idx]

                chosen_strike = st.selectbox(
                    f"Strike Price (Spot: ~{spot_val})",
                    strikes,
                    index=default_idx,
                    key=strike_key
                )
                selected_inst = strike_to_inst.get(chosen_strike, {})
                display_dt, sym_code = parse_expiry_info(sel_expiry)

                if not selected_inst:
                    try:
                        gw = get_gateway("KOTAK")
                        if hasattr(gw, "search_scrip"):
                            scrip_res = gw.search_scrip(
                                symbol=underlying_choice,
                                expiry=sel_expiry,
                                option_type=clean_opt_type,
                                strike_price=chosen_strike,
                            )
                            if scrip_res and isinstance(scrip_res, list) and len(scrip_res) > 0:
                                scrip_item = scrip_res[0]
                                p_tok = str(scrip_item.get("pSymbol") or scrip_item.get("token") or "")
                                p_sym = str(scrip_item.get("pTrdSymbol") or scrip_item.get("tradingSymbol") or "")
                                if p_sym or p_tok:
                                    selected_inst = {
                                        "symbol": p_sym or f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}",
                                        "neoSymbol": f"nse_fo|{p_tok}" if p_tok else f"nse_fo|{chosen_strike}",
                                        "optType": clean_opt_type,
                                        "strkPrc": chosen_strike,
                                    }
                    except Exception as e:
                        logger.debug("Scrip search fallback error: %s", e)

                if selected_inst:
                    st.session_state.paper_symbol = selected_inst.get("symbol", f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}")
                    st.session_state.paper_isin = selected_inst.get("neoSymbol", "").split("|")[-1]
                    st.session_state.paper_instrument_key = selected_inst.get("neoSymbol", f"NSE_FO|{chosen_strike}")
                    st.session_state.paper_equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                    st.session_state.paper_lot_size = mkt_lot
                else:
                    target_symbol = f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}"
                    found_std = None
                    for s_inst in NIFTY_STANDARD_INSTRUMENTS:
                        if s_inst["tradingsymbol"] == target_symbol:
                            found_std = s_inst
                            break
                    if found_std:
                        st.session_state.paper_symbol = found_std["tradingsymbol"]
                        st.session_state.paper_isin = found_std["isin"]
                        st.session_state.paper_instrument_key = found_std["instrument_key"]
                        st.session_state.paper_equity_name = found_std["name"]
                        st.session_state.paper_lot_size = int(found_std.get("lot_size", mkt_lot))
                    else:
                        st.session_state.paper_symbol = target_symbol
                        st.session_state.paper_isin = f"INF00{chosen_strike}1"
                        st.session_state.paper_instrument_key = f"NSE_FO|{chosen_strike}"
                        st.session_state.paper_equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                        st.session_state.paper_lot_size = mkt_lot

            st.caption(f"🎯 **Active Selection:** `{st.session_state.paper_symbol}` | Expiry: `{display_dt}` | Key: `{st.session_state.paper_instrument_key}` | Lot: `{st.session_state.paper_lot_size}`")

        else:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("⚡ **Popular Benchmarks:**")
                q_cols = st.columns(4)
                if q_cols[0].button("📈 NIFTY 50", use_container_width=True, key="paper_quick_nifty"):
                    st.session_state.paper_symbol = "NIFTY"
                    st.session_state.paper_isin = "INF204KB14I2"
                    st.session_state.paper_equity_name = "Nifty 50 Index (NIP IND ETF NIFTY BEES)"
                    st.session_state.paper_instrument_key = "NSE_EQ|INF204KB14I2"
                    st.rerun()
                if q_cols[1].button("🐝 NIFTYBEES", use_container_width=True, key="paper_quick_bees"):
                    st.session_state.paper_symbol = "NIFTYBEES"
                    st.session_state.paper_isin = "INF204KB14I2"
                    st.session_state.paper_equity_name = "NIP IND ETF NIFTY BEES"
                    st.session_state.paper_instrument_key = "NSE_EQ|INF204KB14I2"
                    st.rerun()
                if q_cols[2].button("🏦 BANKNIFTY", use_container_width=True, key="paper_quick_bnifty"):
                    st.session_state.paper_symbol = "BANKNIFTY"
                    st.session_state.paper_isin = "INF174KA1ZB7"
                    st.session_state.paper_equity_name = "Nifty Bank Index (Kotak Bank Nifty ETF)"
                    st.session_state.paper_instrument_key = "NSE_EQ|INF174KA1ZB7"
                    st.rerun()
                if q_cols[3].button("🏢 RELIANCE", use_container_width=True, key="paper_quick_reliance"):
                    st.session_state.paper_symbol = "RELIANCE"
                    st.session_state.paper_isin = "INE002A01018"
                    st.session_state.paper_equity_name = "Reliance Industries Limited"
                    st.session_state.paper_instrument_key = "NSE_EQ|INE002A01018"
                    st.rerun()

                search_query = st.text_input("Search Equity (Symbol or Name)", value=st.session_state.paper_symbol, key="paper_search_query")
                matched = search_equities(search_query, equities_df, limit=20) if not equities_df.empty else pd.DataFrame()
                options = []
                if not matched.empty:
                    for _, r in matched.iterrows():
                        options.append(f"{r['tradingsymbol']} - {r['name']} ({r['isin']})")
                else:
                    options = [f"{st.session_state.paper_symbol} - {st.session_state.paper_equity_name} ({st.session_state.paper_isin})"]

                curr_eq_idx = 0
                for idx, opt in enumerate(options):
                    if opt.startswith(f"{st.session_state.paper_symbol} -"):
                        curr_eq_idx = idx
                        break

                selected_eq = st.selectbox("Matching Instruments", options, index=curr_eq_idx, key="paper_sel_equity")
                if selected_eq:
                    sel_sym = selected_eq.split(" - ")[0].strip()
                    found = find_equity_by_symbol(sel_sym, equities_df)
                    if found:
                        st.session_state.paper_symbol = found["tradingsymbol"]
                        st.session_state.paper_isin = found["isin"]
                        st.session_state.paper_equity_name = found["name"]
                        st.session_state.paper_instrument_key = found.get("instrument_key", f"NSE_EQ|{found['isin']}")

            with c2:
                input_isin = st.text_input("ISIN Code (12 chars)", value=st.session_state.paper_isin, key="paper_input_isin")
                if input_isin != st.session_state.paper_isin:
                    st.session_state.paper_isin = input_isin.strip().upper()
                    found = find_equity_by_isin(st.session_state.paper_isin, equities_df)
                    if found:
                        st.session_state.paper_symbol = found["tradingsymbol"]
                        st.session_state.paper_equity_name = found["name"]
                        st.session_state.paper_instrument_key = found.get("instrument_key", f"NSE_EQ|{found['isin']}")

                is_valid_isin = validate_isin(st.session_state.paper_isin)
                if is_valid_isin:
                    st.success(f"Valid ISIN: {st.session_state.paper_isin} ({st.session_state.paper_symbol})")
                else:
                    st.error("Invalid ISIN: Must start with 'IN' and pass the 12-char Luhn check-digit test.")

        d_col1, d_col2 = st.columns(2)
        with d_col1:
            st.session_state.paper_date = st.date_input("Simulation Date", value=st.session_state.paper_date, key="paper_sim_date")
        with d_col2:
            tf_options = [1, 3, 5, 15]
            curr_tf = tf_options.index(st.session_state.paper_timeframe) if st.session_state.paper_timeframe in tf_options else 2
            st.session_state.paper_timeframe = st.selectbox("Candle Timeframe", tf_options, index=curr_tf, format_func=lambda x: f"{x} Minute" if x == 1 else f"{x} Minutes", key="paper_sim_tf")

        st.markdown("---")
        st.markdown("#### 💰 4. Virtual Account Settings")
        v_col1, v_col2 = st.columns(2)
        with v_col1:
            st.session_state.paper_capital = st.number_input(
                "Initial Virtual Capital (₹)", min_value=10_000.0, max_value=10_000_000.0,
                value=float(st.session_state.paper_capital), step=10_000.0, key="paper_capital_input"
            )
        with v_col2:
            st.info("Simulation includes realistic adverse slippage and full Indian regulatory cost deductions (STT, Exchange turnover, GST, SEBI fees, Stamp Duty).")

        st.markdown("<br>", unsafe_allow_html=True)
        can_launch_paper = bool(st.session_state.paper_symbol) if is_fo else is_valid_isin
        if st.button("🚀 Initialize Paper Trading Desk ➡️", type="primary", use_container_width=True, disabled=not can_launch_paper):
            with st.spinner(f"Loading candles for {st.session_state.paper_symbol} on {st.session_state.paper_date}..."):
                # Prioritize Kotak Neo if F&O segment, Kotak instrument key, or Kotak selected in gateway
                is_kotak_pref = (
                    "Kotak" in st.session_state.get("paper_gateway", "")
                    or is_fo
                    or "nse_fo" in str(st.session_state.get("paper_instrument_key", "")).lower()
                )
                chosen_gw_key = "KOTAK" if is_kotak_pref else "UPSTOX"
                sym = st.session_state.paper_symbol.lower()
                sym_clean = sym.replace(" ", "")
                tf = st.session_state.paper_timeframe
                target_date = st.session_state.paper_date
                inst_key = st.session_state.get("paper_instrument_key") or f"NSE_EQ|{st.session_state.paper_isin}"
                start_d = target_date - timedelta(days=14)

                raw_df = None

                # 1. Primary gateway fetch
                try:
                    active_gw = get_gateway(chosen_gw_key)
                    logger.info("Fetching %s candles: %s from %s to %s", chosen_gw_key, inst_key, start_d, target_date)
                    cand_df = active_gw.fetch_historical_candles(st.session_state.paper_symbol, inst_key, tf, start_d, target_date)
                    if cand_df is not None and not cand_df.empty:
                        day_series = pd.to_datetime(cand_df["timestamp"]).dt.date
                        if not cand_df[day_series == target_date].empty:
                            raw_df = cand_df
                            logger.info("Successfully fetched %d candles from %s for %s including target date %s", len(raw_df), chosen_gw_key, inst_key, target_date)
                        else:
                            logger.info("Gateway %s returned data but target date %s not found. Will try alternate gateway.", chosen_gw_key, target_date)
                            raw_df = cand_df
                except Exception as e:
                    logger.warning("%s fetch failed: %s", chosen_gw_key, e)

                # 2. Try alternate gateway if raw_df is empty or missing target_date
                has_target = (raw_df is not None and not raw_df.empty and not raw_df[pd.to_datetime(raw_df["timestamp"]).dt.date == target_date].empty)
                if not has_target:
                    alt_gw_key = "UPSTOX" if chosen_gw_key == "KOTAK" else "KOTAK"
                    try:
                        logger.info("Trying alternate gateway %s for %s (%s)...", alt_gw_key, st.session_state.paper_symbol, inst_key)
                        alt_gw = get_gateway(alt_gw_key)
                        alt_cand = alt_gw.fetch_historical_candles(st.session_state.paper_symbol, inst_key, tf, start_d, target_date)
                        if alt_cand is not None and not alt_cand.empty:
                            alt_days = pd.to_datetime(alt_cand["timestamp"]).dt.date
                            if not alt_cand[alt_days == target_date].empty:
                                raw_df = alt_cand
                                logger.info("Successfully fetched %d candles with target date from alternate %s", len(raw_df), alt_gw_key)
                            elif raw_df is None or raw_df.empty:
                                raw_df = alt_cand
                    except Exception as e:
                        logger.warning("Alternate gateway %s fetch failed: %s", alt_gw_key, e)

                # Cache live fetched candles to disk for fast reload
                if raw_df is not None and not raw_df.empty:
                    try:
                        os.makedirs("data", exist_ok=True)
                        raw_df.to_csv(f"data/{sym}_{tf}m.csv", index=False)
                        if sym != sym_clean:
                            raw_df.to_csv(f"data/{sym_clean}_{tf}m.csv", index=False)
                    except Exception:
                        pass

                # 3. Local cached CSV fallback only if live gateways completely failed
                if raw_df is None or raw_df.empty:
                    fallbacks = [
                        f"data/{sym}_{tf}m.csv",
                        f"data/{sym_clean}_{tf}m.csv",
                        f"data/{sym}_5m.csv",
                        f"data/{sym_clean}_5m.csv",
                        f"data/{sym}_1m.csv",
                        f"data/{sym_clean}_1m.csv",
                        f"data/{sym}_3m.csv",
                        f"data/{sym_clean}_3m.csv",
                        f"data/nifty_{tf}m.csv",
                        f"data/nifty_5m.csv",
                        f"data/reliance_{tf}m.csv",
                        f"data/reliance_5m.csv",
                    ]
                    for fb in fallbacks:
                        if os.path.exists(fb):
                            logger.info("Loading fallback candles from: %s", fb)
                            raw_df = load_csv(fb)
                            break

                if raw_df is None or raw_df.empty:
                    st.error(f"No historical candles found for {st.session_state.paper_symbol} ({inst_key}). Verify Kotak/Upstox connection or ensure local cached CSV exists in data/.")
                    return

                # Filter for lookback + target date
                raw_df["timestamp"] = pd.to_datetime(raw_df["timestamp"])
                day_series = raw_df["timestamp"].dt.date
                # Take target date plus up to 100 preceding bars for warmup
                preceding = raw_df[day_series < target_date].tail(100)
                target_day_candles = raw_df[day_series == target_date]
                # Ensure target day candles are strictly within regular trading session (<= 15:30 IST)
                target_day_candles = target_day_candles[target_day_candles["timestamp"].dt.time <= time(15, 30)]

                if target_day_candles.empty:
                    # If target date has no candles, pick the closest available date in the dataset
                    available_dates = sorted(list(day_series.drop_duplicates()))
                    closest_date = min(available_dates, key=lambda d: abs((d - target_date).days))
                    st.warning(f"No candles found precisely on {target_date}. Using closest available trading date: {closest_date}")
                    target_date = closest_date
                    preceding = raw_df[day_series < target_date].tail(100)
                    target_day_candles = raw_df[day_series == target_date]
                    target_day_candles = target_day_candles[target_day_candles["timestamp"].dt.time <= time(15, 30)]

                try:
                    session = PaperSimulationSession(
                        symbol=st.session_state.paper_symbol,
                        isin=st.session_state.paper_isin,
                        date_str=str(target_date),
                        strategy_name=st.session_state.paper_strategy_name,
                        candles_df=target_day_candles,
                        capital=st.session_state.paper_capital,
                        warmup_df=preceding,
                    )

                    st.session_state.paper_session = session
                    st.session_state.paper_tab = "2. Interactive Trading Desk"
                    st.success("Session initialized! Redirecting to Interactive Desk...")
                    st.rerun()

                except Exception as e:
                    logger.exception("Failed to initialize paper session: %s", e)
                    st.error(f"Initialization Failed: {str(e)}")


    # ==============================================================================
    # TAB 2: Interactive Trading Desk
    # ==============================================================================
    elif st.session_state.paper_tab == "2. Interactive Trading Desk":
        session = st.session_state.paper_session
        if session is None:
            st.warning("No active session initialized. Please complete Step 1 first.")
            if st.button("⬅️ Go to Setup"):
                st.session_state.paper_tab = "1. Setup & Configuration"
                st.rerun()
            return

        state = session.get_state()
        curr_c = state["current_candle"]
        curr_ts = curr_c["timestamp"] if curr_c else "09:15:00"
        curr_close = state["ltp"]

        # Header & HUD Controls
        st.header(f"📡 Trading Desk: {session.symbol} ({session.date_str})")
        st.caption(f"Strategy: `{session.strategy_name}` | Timeframe: {st.session_state.paper_timeframe}m | Replay Status: {'FINISHED' if state['is_finished'] else 'ACTIVE'}")

        hud_c1, hud_c2, hud_c3, hud_c4 = st.columns([1, 1, 1, 1])
        with hud_c1:
            if st.button("⏭ Step 1 Candle", use_container_width=True, disabled=state["is_finished"], type="primary"):
                session.step()
                st.rerun()

        with hud_c2:
            if st.button("▶ Auto-Play 5 Bars", use_container_width=True, disabled=state["is_finished"]):
                for _ in range(5):
                    if not session.feed.is_finished():
                        session.step()
                st.rerun()

        with hud_c3:
            if st.button("⏩ Run All to Market Close", use_container_width=True, disabled=state["is_finished"]):
                with st.spinner("Simulating remaining session candles..."):
                    session.run_all()
                st.rerun()

        with hud_c4:
            if st.button("🔄 Reset to Open", use_container_width=True):
                session.reset()
                st.rerun()

        # Progress bar
        st.progress(min(state["progress_pct"] / 100.0, 1.0))
        st.caption(f"📊 Replay Progress: **{state['current_index']} / {state['total_candles']} Candles ({state['progress_pct']}%)** | Simulated Time: **{str(curr_ts)[-8:]} IST**")

        st.markdown("---")

        # Main Desk: Chart & Telemetry
        main_col1, main_col2 = st.columns([2, 1])

        with main_col1:
            ch_hdr_c1, ch_hdr_c2 = st.columns([3, 2])
            with ch_hdr_c1:
                st.subheader("Candlestick Chart & Indicators")
            with ch_hdr_c2:
                chart_range_mode = st.selectbox(
                    "Display Range",
                    ["Today's Session (Full Day)", "Last 30 Candles", "Last 60 Candles", "All Available (Warmup + Today)"],
                    index=0,
                    key="chart_display_range_select",
                )

            # Build live Plotly chart
            df_indicators = session.engine.latest_indicators_df
            if df_indicators is not None and not df_indicators.empty:
                view_work = df_indicators.copy()
                view_work["ts_dt"] = pd.to_datetime(view_work["timestamp"])

                target_dt = pd.to_datetime(session.date_str).date()
                today_mask = view_work["ts_dt"].dt.date == target_dt
                today_candles = view_work[today_mask]

                if chart_range_mode == "Today's Session (Full Day)":
                    if not today_candles.empty:
                        view_df = today_candles.copy()
                    else:
                        view_df = view_work.copy()
                elif chart_range_mode == "Last 30 Candles":
                    view_df = view_work.tail(30).copy()
                elif chart_range_mode == "Last 60 Candles":
                    view_df = view_work.tail(60).copy()
                else:  # "All Available (Warmup + Today)"
                    view_df = view_work.copy()

                view_ts = pd.to_datetime(view_df["timestamp"])
                is_single_day = (view_ts.dt.date.nunique() == 1)
                if is_single_day:
                    time_labels = view_ts.dt.strftime("%H:%M")
                else:
                    time_labels = view_ts.dt.strftime("%d-%b %H:%M")

                hover_text = [
                    f"<b>{t_str} IST</b><br>Open: ₹{o:.2f}<br>High: ₹{h:.2f}<br>Low: ₹{l:.2f}<br>Close: ₹{c:.2f}"
                    for t_str, o, h, l, c in zip(time_labels, view_df["open"], view_df["high"], view_df["low"], view_df["close"])
                ]

                if HAS_PLOTLY:
                    fig = go.Figure()
                    # Candlestick trace with discrete categorical labels and explicit hovertext
                    fig.add_trace(go.Candlestick(
                        x=time_labels,
                        open=view_df["open"],
                        high=view_df["high"],
                        low=view_df["low"],
                        close=view_df["close"],
                        name=session.symbol,
                        increasing_line_color="#26a69a",
                        decreasing_line_color="#ef5350",
                        text=hover_text,
                        hoverinfo="text",
                    ))

                    # Overlay EMA 20 & 50 & VWAP
                    if "ema20" in view_df.columns:
                        fig.add_trace(go.Scatter(
                            x=time_labels, y=view_df["ema20"], mode="lines", name="EMA 20",
                            line=dict(color="#29b6f6", width=1.2),
                            hovertemplate="EMA 20: ₹%{y:.2f}<extra></extra>",
                        ))
                    if "ema50" in view_df.columns:
                        fig.add_trace(go.Scatter(
                            x=time_labels, y=view_df["ema50"], mode="lines", name="EMA 50",
                            line=dict(color="#ab47bc", width=1.2),
                            hovertemplate="EMA 50: ₹%{y:.2f}<extra></extra>",
                        ))
                    if "vwap" in view_df.columns:
                        fig.add_trace(go.Scatter(
                            x=time_labels, y=view_df["vwap"], mode="lines", name="VWAP",
                            line=dict(color="#ffa726", width=1.2, dash="dash"),
                            hovertemplate="VWAP: ₹%{y:.2f}<extra></extra>",
                        ))

                    # Entry / Exit Markers mapped precisely to time category labels
                    time_label_set = set(time_labels.values)
                    for t in session.trade_history:
                        e_ts = pd.to_datetime(t["entry_time"])
                        x_ts = pd.to_datetime(t["exit_time"])
                        e_lbl = e_ts.strftime("%H:%M" if is_single_day else "%d-%b %H:%M")
                        x_lbl = x_ts.strftime("%H:%M" if is_single_day else "%d-%b %H:%M")

                        if e_lbl in time_label_set:
                            symb = "triangle-up" if t["side"] == "LONG" else "triangle-down"
                            color = "#00e676" if t["side"] == "LONG" else "#ff1744"
                            fig.add_trace(go.Scatter(
                                x=[e_lbl], y=[t["entry_price"]], mode="markers",
                                marker=dict(symbol=symb, size=14, color=color),
                                name=f"ENTRY {t['side']}",
                                hovertemplate=f"ENTRY {t['side']}: ₹{t['entry_price']:.2f}<extra></extra>",
                                showlegend=False,
                            ))
                        if x_lbl in time_label_set:
                            fig.add_trace(go.Scatter(
                                x=[x_lbl], y=[t["exit_price"]], mode="markers",
                                marker=dict(symbol="x", size=12, color="#ffd600"),
                                name=f"EXIT ({t['exit_reason']})",
                                hovertemplate=f"EXIT ({t['exit_reason']}): ₹{t['exit_price']:.2f}<extra></extra>",
                                showlegend=False,
                            ))

                    fig.update_layout(
                        height=480,
                        margin=dict(l=10, r=10, t=10, b=10),
                        xaxis=dict(
                            type="category",
                            categoryorder="category ascending",
                            rangeslider=dict(visible=False),
                            showgrid=True,
                            gridcolor="rgba(255,255,255,0.06)",
                        ),
                        yaxis=dict(
                            showgrid=True,
                            gridcolor="rgba(255,255,255,0.06)",
                        ),
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    c_cols = [c for c in ["close", "ema20", "ema50", "vwap"] if c in view_df.columns]
                    chart_df = view_df.copy()
                    chart_df["Time"] = time_labels
                    st.line_chart(chart_df.set_index("Time")[c_cols])
            else:
                st.info("Advancing candles to populate the chart...")

        with main_col2:
            st.subheader("Live Telemetry & Position")

            # 1. Position Card
            open_t = state["open_trade"]
            mtm = state["mtm_pnl"]
            bal = session.broker.get_balance()
            tot_eq = bal["cash"] + mtm

            with st.container():
                st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                if open_t is not None:
                    badge = f'<span class="badge-pill badge-{open_t["side"].lower()}">{open_t["side"]} {open_t["quantity"]} QTY</span>'
                    st.markdown(f"**Open Position:** {badge}", unsafe_allow_html=True)
                    st.write(f"**Entry Price:** ₹{open_t['entry_price']:.2f} | **LTP:** ₹{curr_close:.2f}")

                    mtm_color = "green" if mtm >= 0 else "red"
                    st.markdown(f"**Unrealized MTM:** :{mtm_color}[₹{mtm:+,.2f}]")
                    st.caption(f"🎯 Target: ₹{open_t['target']:.2f} | 🛡️ SL: ₹{open_t['stop_loss']:.2f}")
                else:
                    st.markdown('**Open Position:** <span class="badge-pill badge-flat">FLAT</span>', unsafe_allow_html=True)
                    st.caption("No open exposure. Waiting for signal...")

                st.markdown("---")
                st.write(f"**Virtual Cash:** ₹{bal['cash']:,.2f}")
                eq_color = "green" if tot_eq >= session.initial_capital else "red"
                st.markdown(f"**Total Equity:** :{eq_color}[₹{tot_eq:,.2f}]")
                st.write(f"**Realized Net P&L:** ₹{bal['net_realized_pnl']:+,.2f}")
                st.write(f"**Taxes & Brokerage:** ₹{bal['charges']:,.2f}")
                st.markdown('</div>', unsafe_allow_html=True)

            # 2. Strategy Consensus Score Card
            sig = state["latest_signal"]
            if sig is not None:
                with st.container():
                    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                    sc = sig["score"]
                    dec = sig["decision"]
                    dec_color = "green" if dec == "LONG" else ("red" if dec == "SHORT" else "gray")
                    st.markdown(f"**Strategy Signal:** :{dec_color}[**{dec}**] (Score: **{sc:+d}**)")

                    # Consensus progress bar
                    max_pts = 12
                    norm_score = (sc + max_pts) / (2 * max_pts)
                    st.progress(min(max(norm_score, 0.0), 1.0))
                    st.caption(f"Thresholds: Long **+{session.ind_cfg.get('long_threshold', 5)}** | Short **{session.ind_cfg.get('short_threshold', -5)}**")

                    inds = sig.get("indicators", {})
                    rsi_val = inds.get("rsi14", "N/A")
                    macd_val = inds.get("macd", "N/A")
                    adx_val = inds.get("adx", "N/A")
                    vol_rat = inds.get("volume_ratio", "N/A")

                    r_c1, r_c2 = st.columns(2)
                    r_c1.caption(f"RSI: **{rsi_val:.1f}**" if isinstance(rsi_val, float) else f"RSI: {rsi_val}")
                    r_c2.caption(f"ADX: **{adx_val:.1f}**" if isinstance(adx_val, float) else f"ADX: {adx_val}")
                    r_c1.caption(f"MACD: **{macd_val:.2f}**" if isinstance(macd_val, float) else f"MACD: {macd_val}")
                    r_c2.caption(f"Vol Ratio: **{vol_rat:.2f}x**" if isinstance(vol_rat, float) else f"Vol Ratio: {vol_rat}")
                    st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("---")

        # Lower Section: Orders, Trades, Audit
        t_tab1, t_tab2, t_tab3 = st.tabs(["📋 Executed Trades", "📑 Order Ledger", "📜 Real-time System Audit"])

        with t_tab1:
            if session.trade_history:
                df_trades = pd.DataFrame(session.trade_history)
                st.dataframe(df_trades, use_container_width=True, hide_index=True)
            else:
                st.caption("No closed trades recorded yet.")

        with t_tab2:
            if session.order_history:
                df_orders = pd.DataFrame(session.order_history)
                st.dataframe(df_orders, use_container_width=True, hide_index=True)
            else:
                st.caption("No orders placed yet.")

        with t_tab3:
            if session.events_log:
                df_events = pd.DataFrame(session.events_log[-15:])
                st.dataframe(df_events, use_container_width=True, hide_index=True)
            else:
                st.caption("No events logged.")


    # ==============================================================================
    # TAB 3: Analytics & Trade Log
    # ==============================================================================
    elif st.session_state.paper_tab == "3. Analytics & Trade Log":
        session = st.session_state.paper_session
        if session is None or not session.trade_history:
            st.info("Complete at least one trade in the Interactive Desk to view analytics.")
            if st.button("⬅️ Go to Desk"):
                st.session_state.paper_tab = "2. Interactive Trading Desk"
                st.rerun()
            return

        st.header(f"📊 Session Analytics: {session.symbol} ({session.date_str})")
        st.caption(f"Performance summary for Strategy `{session.strategy_name}`")

        trades = session.trade_history
        n_trades = len(trades)
        pnls = [t["gross_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = (len(wins) / n_trades * 100.0) if n_trades > 0 else 0.0

        gross_pnl = sum(pnls)
        bal = session.broker.get_balance()
        net_pnl = bal["net_realized_pnl"]
        charges = bal["charges"]

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Trades", n_trades)
        m2.metric("Win Rate", f"{win_rate:.1f}%")
        m3.metric("Gross P&L", f"₹{gross_pnl:+,.2f}")
        m4.metric("Brokerage & Taxes", f"₹{charges:,.2f}")
        m5.metric("Net Realized P&L", f"₹{net_pnl:+,.2f}")

        st.markdown("---")
        st.subheader("Equity Curve & MTM Progression")

        if session.equity_curve:
            eq_df = pd.DataFrame(session.equity_curve)
            if HAS_PLOTLY:
                eq_fig = go.Figure()
                eq_fig.add_trace(go.Scatter(x=eq_df["timestamp"], y=eq_df["equity"], mode="lines", name="Total Equity", line=dict(color="#00e676", width=2)))
                eq_fig.add_trace(go.Scatter(x=eq_df["timestamp"], y=eq_df["cash"], mode="lines", name="Cash Balance", line=dict(color="#29b6f6", width=1.5, dash="dash")))

                eq_fig.update_layout(
                    height=380,
                    margin=dict(l=10, r=10, t=10, b=10),
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(eq_fig, use_container_width=True)
            else:
                st.line_chart(eq_df.set_index("timestamp")[["equity", "cash"]])

        st.subheader("Detailed Trade Log")
        df_trades = pd.DataFrame(trades)
        st.dataframe(df_trades, use_container_width=True, hide_index=True)

        csv_data = df_trades.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Paper Trades (CSV)",
            data=csv_data,
            file_name=f"paper_trades_{session.symbol}_{session.date_str}.csv",
            mime="text/csv",
        )

if __name__ == "__main__":
    st.set_page_config(
        page_title="AI Paper Trading Studio",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_paper_trade_module()

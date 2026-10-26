import json
import re
import logging
import os
import sys
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

import importlib
import app.config.risk_config
import app.strategy.custom_strategy
import app.backtest.engine
importlib.reload(app.config.risk_config)
importlib.reload(app.strategy.custom_strategy)
importlib.reload(app.backtest.engine)

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.metrics import compute_metrics
from app.backtest.runner import split_by_days, trading_days
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
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
from app.database.backtest_database import BacktestDatabase
from app.strategy.custom_strategy import (
    CustomStrategyConfig,
    calculate_custom_indicators,
    score_custom_row,
)
from app.strategy.strategy_store import (
    auto_suggest_strategy_name,
    delete_strategy,
    get_strategy,
    list_strategy_names,
    load_all_strategies,
    save_strategy,
)

from app.gateways.gateway_factory import get_gateway

load_dotenv()
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


def render_backtest_module():
    # Audit log session tracking for localhost and public URL traffic
    from app.monitoring.audit_logger import (
        get_recent_audit_logs,
        log_audit_event,
        record_session_visit,
    )
    client_info = record_session_visit()


    # Custom styling to make KPI metric values compact and elegant (~double the label size)
    st.markdown("""
    <style>
    div[data-testid="stMetricValue"], 
    div[data-testid="stMetricValue"] * {
        font-size: 1.35rem !important;
        font-weight: 600 !important;
        line-height: 1.3 !important;
    }
    div[data-testid="stMetricLabel"],
    div[data-testid="stMetricLabel"] * {
        font-size: 0.82rem !important;
    }
    div[data-testid="stMetricDelta"],
    div[data-testid="stMetricDelta"] * {
        font-size: 0.80rem !important;
    }
    div[data-testid="stMetric"] {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.08);
        padding: 8px 12px;
        border-radius: 8px;
    }
    </style>
    """, unsafe_allow_html=True)

    db = BacktestDatabase()
    DOCS_PATH = PROJECT_ROOT / "docs" / "strategy_scoring_guide.html"


    def load_guide_html() -> str:
        if DOCS_PATH.exists():
            try:
                with open(DOCS_PATH, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"<h3>Error reading documentation: {e}</h3>"
        return "<h3>Documentation file not found at docs/strategy_scoring_guide.html</h3>"


    @st.dialog("📖 Strategy Scoring & Signal Thresholds Guide", width="large")
    def show_guide_dialog():
        html_content = load_guide_html()
        col_d1, col_d2 = st.columns([3, 1])
        col_d1.caption("Interactive guide explaining indicator weights, consensus scoring, and threshold gates.")
        col_d2.download_button(
            "⬇️ Save HTML File",
            data=html_content,
            file_name="strategy_scoring_guide.html",
            mime="text/html",
            use_container_width=True,
        )
        st.components.v1.html(html_content, height=650, scrolling=True)


    IND_TOGGLE_KEYS = [
        ("ema_enabled", "c_ema"),
        ("rsi_enabled", "c_rsi"),
        ("macd_enabled", "c_macd"),
        ("vwap_enabled", "c_vwap"),
        ("atr_enabled", "c_atr"),
        ("adx_enabled", "c_adx"),
        ("volume_enabled", "c_vol"),
        ("bollinger_enabled", "c_bb"),
        ("stoch_enabled", "c_stoch"),
        ("obv_enabled", "c_obv"),
        ("higher_high_enabled", "c_hh"),
        ("lower_low_enabled", "c_ll"),
        ("hma_enabled", "c_hma"),
        ("supertrend_enabled", "c_supertrend"),
    ]


    def load_strategy_into_session(strat_name: str):
        """Loads a named strategy configuration from strategy_store into session state."""
        strat = get_strategy(strat_name)
        if not strat:
            return
        st.session_state.selected_strategy_name = strat_name
        st.session_state.strategy_name_input = strat_name
        try:
            st.session_state["step1_strat_name_input_field"] = strat_name
        except Exception:
            pass
        try:
            st.session_state["step1_load_strat_select"] = strat_name
        except Exception:
            pass
        try:
            st.session_state["step2_strat_selector"] = strat_name
        except Exception:
            pass
        if "indicators" in strat:
            st.session_state.indicators = dict(strat["indicators"])
            for ik, wk in IND_TOGGLE_KEYS:
                try:
                    st.session_state[wk] = st.session_state.indicators.get(ik, True)
                except Exception:
                    pass
            try:
                st.session_state["select_all_ind"] = all(
                    st.session_state.get(w, False) for _, w in IND_TOGGLE_KEYS
                )
            except Exception:
                pass
        if "risk" in strat:
            r = dict(strat["risk"])
            for k in ["entry_start", "entry_end", "square_off_time"]:
                if k in r and isinstance(r[k], str):
                    parts = [int(p) for p in r[k].split(":")]
                    r[k] = time(*parts)
            st.session_state.risk = r
        if "cost" in strat:
            st.session_state.cost = dict(strat["cost"])
        if "instrument" in strat and isinstance(strat["instrument"], dict):
            inst_cfg = strat["instrument"]
            mkt_seg = inst_cfg.get("market_segment")
            if mkt_seg:
                st.session_state.market_segment = mkt_seg
                try:
                    st.session_state["backtest_market_segment_radio"] = mkt_seg
                except Exception:
                    pass

            if inst_cfg.get("symbol"):
                st.session_state.symbol = inst_cfg["symbol"]
            if inst_cfg.get("isin"):
                st.session_state.isin = inst_cfg["isin"]
                try:
                    st.session_state["backtest_input_isin"] = inst_cfg["isin"]
                except Exception:
                    pass
            if inst_cfg.get("equity_name"):
                st.session_state.equity_name = inst_cfg["equity_name"]
            if inst_cfg.get("instrument_key"):
                st.session_state.instrument_key = inst_cfg["instrument_key"]

            und = inst_cfg.get("underlying")
            if und:
                try:
                    st.session_state["backtest_underlying_choice"] = und
                except Exception:
                    pass
                if inst_cfg.get("expiry"):
                    try:
                        st.session_state[f"backtest_sel_expiry_{und}"] = inst_cfg["expiry"]
                    except Exception:
                        pass
                if inst_cfg.get("strike"):
                    try:
                        st.session_state[f"backtest_chosen_strike_{und}"] = str(inst_cfg["strike"])
                    except Exception:
                        pass
                if inst_cfg.get("fo_mode"):
                    try:
                        st.session_state["backtest_fo_mode"] = inst_cfg["fo_mode"]
                    except Exception:
                        pass

            if inst_cfg.get("option_type"):
                opt_str = "PE (Put)" if "PE" in str(inst_cfg["option_type"]).upper() else "CE (Call)"
                try:
                    st.session_state["backtest_opt_type_choice"] = opt_str
                except Exception:
                    pass

            if inst_cfg.get("start_date"):
                try:
                    sd = inst_cfg["start_date"]
                    d_obj = date.fromisoformat(sd) if isinstance(sd, str) else sd
                    st.session_state.start_date = d_obj
                    st.session_state["backtest_start_date"] = d_obj
                except Exception:
                    pass

            if inst_cfg.get("end_date"):
                try:
                    ed = inst_cfg["end_date"]
                    d_obj = date.fromisoformat(ed) if isinstance(ed, str) else ed
                    st.session_state.end_date = d_obj
                    st.session_state["backtest_end_date"] = d_obj
                except Exception:
                    pass

            if inst_cfg.get("timeframe"):
                try:
                    tf = int(inst_cfg["timeframe"])
                    st.session_state.timeframe = tf
                    st.session_state["backtest_timeframe_select"] = tf
                except Exception:
                    pass

            if "allow_short" in inst_cfg:
                try:
                    as_val = bool(inst_cfg["allow_short"])
                    st.session_state.allow_short = as_val
                    st.session_state["backtest_allow_short"] = as_val
                except Exception:
                    pass

            if "lot_size" in inst_cfg and inst_cfg["lot_size"] is not None:
                try:
                    st.session_state.risk["lot_size"] = int(inst_cfg["lot_size"])
                except Exception:
                    pass

            if "tick_size" in inst_cfg and inst_cfg["tick_size"] is not None:
                try:
                    st.session_state.risk["tick_size"] = float(inst_cfg["tick_size"])
                except Exception:
                    pass

    def get_current_instrument_config() -> dict:
        """Collects current instrument configuration and environment parameters from session state."""
        und = st.session_state.get("backtest_underlying_choice", "NIFTY")
        fo_mode = st.session_state.get("backtest_fo_mode", "Normal Mode")
        return {
            "market_segment": st.session_state.get("market_segment", "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]"),
            "fo_mode": fo_mode,
            "symbol": st.session_state.get("symbol", "NIFTY"),
            "isin": st.session_state.get("isin", ""),
            "equity_name": st.session_state.get("equity_name", ""),
            "instrument_key": st.session_state.get("instrument_key", ""),
            "underlying": und,
            "expiry": st.session_state.get(f"backtest_sel_expiry_{und}", "") if fo_mode != "OIPulse Mode" else "",
            "option_type": st.session_state.get("backtest_opt_type_choice", "PE (Put)") if fo_mode != "OIPulse Mode" else "",
            "strike": str(st.session_state.get(f"backtest_chosen_strike_{und}", "")) if fo_mode != "OIPulse Mode" else "",
            "start_date": str(st.session_state.get("start_date", "2026-09-01")),
            "end_date": str(st.session_state.get("end_date", "2026-09-30")),
            "timeframe": int(st.session_state.get("timeframe", 5)),
            "allow_short": bool(st.session_state.get("allow_short", True)),
            "lot_size": int(st.session_state.risk.get("lot_size", 1)),
            "tick_size": float(st.session_state.risk.get("tick_size", 0.05)),
        }

    def reset_to_default_strategy():
        """Resets to standard clean configuration with auto-suggested name."""
        clean_strat = get_strategy("EMA_RSI_MACD_Balanced")
        if clean_strat:
            load_strategy_into_session("EMA_RSI_MACD_Balanced")
        auto_name = auto_suggest_strategy_name(st.session_state.indicators)
        st.session_state.strategy_name_input = auto_name
        st.session_state.selected_strategy_name = auto_name
        try:
            st.session_state["step1_strat_name_input_field"] = auto_name
        except Exception:
            pass
        try:
            st.session_state["step2_strat_selector"] = auto_name
        except Exception:
            pass


    # ---------------- Session State Helpers ----------------
    def init_session_state():
        if "step" not in st.session_state or st.session_state.step not in (1, 2):
            st.session_state.step = 1

        saved_strats = list_strategy_names()
        if "selected_strategy_name" not in st.session_state:
            st.session_state.selected_strategy_name = saved_strats[0] if saved_strats else "EMA_RSI_MACD_Balanced"
        if "strategy_name_input" not in st.session_state:
            st.session_state.strategy_name_input = st.session_state.selected_strategy_name
        if "step1_strat_name_input_field" not in st.session_state:
            st.session_state["step1_strat_name_input_field"] = st.session_state.strategy_name_input
        if "step2_strat_selector" not in st.session_state:
            st.session_state["step2_strat_selector"] = st.session_state.selected_strategy_name

        if "symbol" not in st.session_state:
            st.session_state.symbol = "RELIANCE"
        if "isin" not in st.session_state:
            st.session_state.isin = "INE002A01018"
        if "equity_name" not in st.session_state:
            st.session_state.equity_name = "RELIANCE INDUSTRIES LTD"
        if "instrument_key" not in st.session_state:
            st.session_state.instrument_key = "NSE_EQ|INE002A01018"
        if "market_segment" not in st.session_state:
            st.session_state.market_segment = "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]"


        if "start_date" not in st.session_state:
            st.session_state.start_date = date(2026, 9, 1)
        if "end_date" not in st.session_state:
            st.session_state.end_date = date(2026, 9, 30)
        if "timeframe" not in st.session_state:
            st.session_state.timeframe = 5
        if "allow_short" not in st.session_state:
            st.session_state.allow_short = True
        if "holdout_pct" not in st.session_state:
            st.session_state.holdout_pct = 0

        # Indicator defaults
        if "indicators" not in st.session_state:
            strat = get_strategy(st.session_state.selected_strategy_name)
            if strat and "indicators" in strat:
                st.session_state.indicators = dict(strat["indicators"])
            else:
                st.session_state.indicators = {
                    "ema_enabled": True, "ema_fast": 20, "ema_slow": 50, "ema_weight": 2,
                    "rsi_enabled": True, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
                    "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 1,
                    "macd_enabled": True, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
                    "vwap_enabled": True, "vwap_weight": 1,
                    "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
                    "adx_enabled": True, "adx_period": 14, "adx_min": 20.0, "adx_weight": 1,
                    "volume_enabled": True, "volume_period": 20, "volume_ratio_min": 1.2, "volume_consecutive": 3, "volume_weight": 1,
                    "bollinger_enabled": True, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 1,
                    "stoch_enabled": True, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 1,
                    "obv_enabled": True, "obv_ma_period": 20, "obv_weight": 1,
                    "higher_high_enabled": False, "higher_high_period": 5, "higher_high_weight": 1,
                    "lower_low_enabled": False, "lower_low_period": 5, "lower_low_weight": 1,
                    "hma_enabled": False, "hma_period": 100, "hma_weight": 1,
                    "supertrend_enabled": False, "supertrend_period": 10, "supertrend_mult_simple": 2.0, "supertrend_mult_complex": 4.0, "supertrend_weight": 1,
                    "long_threshold": 5, "short_threshold": -5,
                    "fixed_target_pct": 3, "fixed_stoploss_pct": 2,
                    "trailing_sl_enabled": False, "trail_beyond_target": False,
                    "trade_side": "BOTH", "short_lot_size": 1,
                    "snooze_period_str": "5 min", "snooze_minutes": 5,
                }
        else:
            if "volume_consecutive" not in st.session_state.indicators:
                st.session_state.indicators["volume_consecutive"] = 3
            if "fixed_target_pct" not in st.session_state.indicators:
                st.session_state.indicators["fixed_target_pct"] = 3
            if "fixed_stoploss_pct" not in st.session_state.indicators:
                st.session_state.indicators["fixed_stoploss_pct"] = 2
            if "trailing_sl_enabled" not in st.session_state.indicators:
                st.session_state.indicators["trailing_sl_enabled"] = False
            if "trail_beyond_target" not in st.session_state.indicators:
                st.session_state.indicators["trail_beyond_target"] = False
            if "higher_high_enabled" not in st.session_state.indicators:
                st.session_state.indicators["higher_high_enabled"] = False
            if "higher_high_period" not in st.session_state.indicators:
                st.session_state.indicators["higher_high_period"] = 5
            if "higher_high_weight" not in st.session_state.indicators:
                st.session_state.indicators["higher_high_weight"] = 1
            if "lower_low_enabled" not in st.session_state.indicators:
                st.session_state.indicators["lower_low_enabled"] = False
            if "lower_low_period" not in st.session_state.indicators:
                st.session_state.indicators["lower_low_period"] = 5
            if "lower_low_weight" not in st.session_state.indicators:
                st.session_state.indicators["lower_low_weight"] = 1
            if "hma_enabled" not in st.session_state.indicators:
                st.session_state.indicators["hma_enabled"] = False
            if "hma_period" not in st.session_state.indicators:
                st.session_state.indicators["hma_period"] = 100
            if "hma_weight" not in st.session_state.indicators:
                st.session_state.indicators["hma_weight"] = 1
            if "supertrend_enabled" not in st.session_state.indicators:
                st.session_state.indicators["supertrend_enabled"] = False
            if "supertrend_period" not in st.session_state.indicators:
                st.session_state.indicators["supertrend_period"] = 10
            if "supertrend_mult_simple" not in st.session_state.indicators:
                st.session_state.indicators["supertrend_mult_simple"] = 2.0
            if "supertrend_mult_complex" not in st.session_state.indicators:
                st.session_state.indicators["supertrend_mult_complex"] = 4.0
            if "supertrend_weight" not in st.session_state.indicators:
                st.session_state.indicators["supertrend_weight"] = 1
            if "trade_side" not in st.session_state.indicators:
                st.session_state.indicators["trade_side"] = "BOTH"
            if "short_lot_size" not in st.session_state.indicators:
                st.session_state.indicators["short_lot_size"] = 1
            if "snooze_period_str" not in st.session_state.indicators:
                st.session_state.indicators["snooze_period_str"] = "5 min"
            if "snooze_minutes" not in st.session_state.indicators:
                st.session_state.indicators["snooze_minutes"] = 5

        # Risk defaults (from RiskConfig)
        if "risk" not in st.session_state:
            strat = get_strategy(st.session_state.selected_strategy_name)
            if strat and "risk" in strat:
                r = dict(strat["risk"])
                for k in ["entry_start", "entry_end", "square_off_time"]:
                    if k in r and isinstance(r[k], str):
                        parts = [int(p) for p in r[k].split(":")]
                        r[k] = time(*parts)
                st.session_state.risk = r
            else:
                st.session_state.risk = {
                    "capital": 100_000.0,
                    "risk_per_trade_pct": 0.01,
                    "max_daily_loss": 2_000.0,
                    "max_open_positions": 3,
                    "max_exposure_pct": 1.0,
                    "max_quantity": 5_000,
                    "max_consecutive_losses": 3,
                    "lot_size": 1,
                    "tick_size": 0.05,
                    "trade_side": "BOTH",
                    "short_lot_size": 1,
                    "snooze_period_str": "5 min",
                    "snooze_minutes": 5,
                    "atr_stop_multiplier": 1.5,
                    "reward_risk_ratio": 2.0,
                    "fixed_target_pct": 3,
                    "fixed_stoploss_pct": 2,
                    "trailing_sl_enabled": False,
                    "trail_beyond_target": False,
                    "entry_start": time(9, 20),
                    "entry_end": time(15, 0),
                    "square_off_time": time(15, 15),
                }
        else:
            if "fixed_target_pct" not in st.session_state.risk:
                st.session_state.risk["fixed_target_pct"] = 3
            if "fixed_stoploss_pct" not in st.session_state.risk:
                st.session_state.risk["fixed_stoploss_pct"] = 2
            if "trailing_sl_enabled" not in st.session_state.risk:
                st.session_state.risk["trailing_sl_enabled"] = False
            if "trail_beyond_target" not in st.session_state.risk:
                st.session_state.risk["trail_beyond_target"] = False
            if "trade_side" not in st.session_state.risk:
                st.session_state.risk["trade_side"] = "BOTH"
            if "short_lot_size" not in st.session_state.risk:
                st.session_state.risk["short_lot_size"] = 1
            if "snooze_period_str" not in st.session_state.risk:
                st.session_state.risk["snooze_period_str"] = "5 min"
            if "snooze_minutes" not in st.session_state.risk:
                st.session_state.risk["snooze_minutes"] = 5

        # Cost defaults
        if "cost" not in st.session_state:
            strat = get_strategy(st.session_state.selected_strategy_name)
            if strat and "cost" in strat:
                st.session_state.cost = dict(strat["cost"])
            else:
                st.session_state.cost = {
                    "cost_mode": "FLAT_PER_ORDER",
                    "flat_cost_per_order": 20.0,
                    "slippage_mode": "FLAT_PER_ORDER",
                    "flat_slippage_per_order": 20.0,
                    "slippage_pct": 0.0005,
                }

        if "active_run" not in st.session_state:
            st.session_state.active_run = None


    init_session_state()

    # ---------------- Load Instruments Cache ----------------
    try:
        equities_df = load_nse_equities()
    except Exception as e:
        logger.error("Failed loading instruments: %s", e)
        equities_df = pd.DataFrame()


    # ---------------- Sidebar: Execution History ----------------
    st.sidebar.title("Execution History")

    all_db_strats = db.get_distinct_strategies()
    all_saved_strats = list_strategy_names()
    merged_strats = sorted(list(set(all_saved_strats + all_db_strats)))

    strat_filter_options = ["[All Strategies]"] + merged_strats
    current_selected_strat = st.session_state.get("selected_strategy_name", "")
    default_filter_idx = 0
    if current_selected_strat in strat_filter_options:
        default_filter_idx = strat_filter_options.index(current_selected_strat)

    selected_strat_filter = st.sidebar.selectbox(
        "🎯 Strategy Filter",
        strat_filter_options,
        index=default_filter_idx,
        key="sb_strat_filter",
        help="Filter previous executions by Strategy Name",
    )

    filter_arg = selected_strat_filter if selected_strat_filter != "[All Strategies]" else None
    recent_runs = db.get_recent_runs(limit=20, strategy_name=filter_arg)

    if recent_runs:
        run_options = {}
        for r in recent_runs:
            net_pnl = r.get("metrics", {}).get("net_pnl")
            if net_pnl is not None:
                pnl_val = float(net_pnl)
                pnl_tag = f"+₹{pnl_val:,.0f}" if pnl_val >= 0 else f"-₹{abs(pnl_val):,.0f}"
            else:
                pnl_tag = "N/A"
            label = f"#{r['id']} | {r['symbol']} ({pnl_tag}) | {r['date_from'][5:]} to {r['date_to'][5:]} | {r['created_at'][5:16]}"
            run_options[label] = r["id"]

        selected_label = st.sidebar.selectbox(f"Previous executions ({len(recent_runs)}/20)", list(run_options.keys()), key="sb_run_picker")
        selected_run_id = run_options[selected_label]

        col_btn1, col_btn2 = st.sidebar.columns(2)
        if col_btn1.button("View Run", use_container_width=True):
            run_data = db.get_run(selected_run_id)
            trades_df = db.get_trades(selected_run_id)
            st.session_state.active_run = {
                "run_id": selected_run_id,
                "run_data": run_data,
                "trades_df": trades_df,
                "metrics": run_data.get("metrics", {}),
                "in_sample": None,
                "out_sample": None,
            }
            st.session_state.step = 2
            st.rerun()

        if col_btn2.button("Re-run Config", use_container_width=True):
            run_data = db.get_run(selected_run_id)
            cfg_dict = run_data.get("config", {})
            if "symbol" in cfg_dict:
                st.session_state.symbol = cfg_dict["symbol"]
            if "isin" in cfg_dict:
                st.session_state.isin = cfg_dict["isin"]
            if "indicators" in cfg_dict:
                st.session_state.indicators = cfg_dict["indicators"]
            strat_to_reload = cfg_dict.get("strategy_name") or run_data.get("strategy_version")
            if strat_to_reload:
                st.session_state.selected_strategy_name = strat_to_reload
                st.session_state.strategy_name_input = strat_to_reload
                try:
                    st.session_state["step1_strat_name_input_field"] = strat_to_reload
                except Exception:
                    pass
                try:
                    st.session_state["step1_load_strat_select"] = strat_to_reload
                except Exception:
                    pass
            if "risk" in cfg_dict:
                r = cfg_dict["risk"]
                for k in ["entry_start", "entry_end", "square_off_time"]:
                    if k in r and isinstance(r[k], str):
                        parts = [int(p) for p in r[k].split(":")]
                        r[k] = time(*parts)
                st.session_state.risk = r
            if "cost" in cfg_dict:
                st.session_state.cost = cfg_dict["cost"]
            st.session_state.step = 1
            st.sidebar.success("Config reloaded! Redirecting to Step 1...")
            st.rerun()
    else:
        st.sidebar.info(f"No executions found for '{selected_strat_filter}'. Complete a backtest to see history.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("📖 Documentation & Help")
    if st.sidebar.button("❓ Strategy & Scoring Guide", use_container_width=True, key="btn_side_guide"):
        show_guide_dialog()

    guide_raw = load_guide_html()
    st.sidebar.download_button(
        "⬇️ Download Guide (.html)",
        data=guide_raw,
        file_name="strategy_scoring_guide.html",
        mime="text/html",
        use_container_width=True,
        help="Download the offline HTML guide to open in any browser",
        key="btn_side_dl_guide"
    )

    # ---------------- Sidebar: Live Access & Audit ----------------
    st.sidebar.markdown("---")
    source_badge = "🟢 Localhost"
    if "Ngrok" in client_info.get("access_source", ""):
        source_badge = "🌐 Public (Ngrok)"
    elif "Cloudflare" in client_info.get("access_source", ""):
        source_badge = "☁️ Public (Cloudflare)"
    elif "LAN" in client_info.get("access_source", ""):
        source_badge = "📶 Local Network"
    elif "Public" in client_info.get("access_source", ""):
        source_badge = "🌍 Public Internet"

    with st.sidebar.expander(f"🛡️ Access & Audit ({source_badge})", expanded=False):
        st.markdown(f"**Access Source:** `{client_info.get('access_source', 'Unknown')}`")
        st.markdown(f"**Client IP:** `{client_info.get('client_ip', '127.0.0.1')}`")
        st.markdown(f"**Host:** `{client_info.get('host', 'localhost')}`")
        st.markdown(f"**Device:** `{client_info.get('browser', 'Unknown')} on {client_info.get('os', 'Unknown')}`")
        st.markdown("---")
        st.caption("Live Access Audit Trail (newest first):")
        recent_logs = get_recent_audit_logs(limit=5)
        if recent_logs:
            log_df = pd.DataFrame([{
                "Time": str(r.get("timestamp_local", ""))[-8:],
                "Src": r.get("access_source", "").replace("Public Tunnel (Ngrok)", "Ngrok").replace("Public Tunnel (Cloudflare)", "Cloudflare"),
                "IP": r.get("client_ip", ""),
                "Event": r.get("event_type", ""),
            } for r in recent_logs])
            st.dataframe(log_df, width="stretch", hide_index=True)
        else:
            st.caption("No audit entries recorded yet.")



    # ---------------- Wizard Steps ----------------
    steps = ["1. Strategy Config & Rules", "2. Results"]
    st.progress(min(st.session_state.step / 2.0, 1.0))
    col_tabs = st.columns(2)
    for i, name in enumerate(steps, 1):
        disabled = (i == 2 and st.session_state.active_run is None)
        if col_tabs[i - 1].button(name, key=f"nav_tab_{i}", disabled=disabled):
            st.session_state.step = i
            st.rerun()

    st.markdown("---")


    def execute_backtest_simulation():
        progress_bar = st.progress(10)
        status_text = st.empty()
        strat_name = st.session_state.get("selected_strategy_name", "custom")

        token = os.getenv("UPSTOX_ACCESS_TOKEN")
        inst_key = st.session_state.get("instrument_key") or f"NSE_EQ|{st.session_state.isin}"
        start_d = st.session_state.start_date
        end_d = st.session_state.end_date

        log_audit_event(
            "BACKTEST_INITIATED",
            details={
                "symbol": st.session_state.symbol,
                "isin": st.session_state.isin,
                "from_date": str(start_d),
                "to_date": str(end_d),
                "timeframe": f"{st.session_state.timeframe}m",
                "capital": st.session_state.risk.get("capital"),
            },
        )

        try:
            status_text.text(f"Fetching historical candles for {st.session_state.symbol} ({inst_key})...")
            progress_bar.progress(25)

            raw_df = None
            is_kotak_inst = bool(inst_key and ("nse_fo" in inst_key.lower() or "nse_cm" in inst_key.lower() or inst_key.isdigit()))
            is_fo = "F&O" in st.session_state.get("market_segment", "")
            kotak_err = None
            upstox_err = None

            # 0. Primary for OIPulse Mode: directly query oi_strength table from data/backtest.db
            fo_mode_active = st.session_state.get("backtest_fo_mode", "Normal Mode") == "OIPulse Mode"
            if fo_mode_active:
                import sqlite3
                from app.strategy.oipulse_resolver import parse_strength_value
                db_file = "data/backtest.db"
                if os.path.exists(db_file):
                    try:
                        conn = sqlite3.connect(db_file)
                        df_oi = pd.read_sql("SELECT Date, Time, LTP, Strength FROM oi_strength WHERE Time != 'EOD' ORDER BY id ASC", conn)
                        conn.close()
                        records = []
                        for _, row_oi in df_oi.iterrows():
                            try:
                                d_str = row_oi["Date"].strip()
                                t_str = row_oi["Time"].strip()
                                dt_oi = datetime.strptime(f"{d_str} {t_str}", "%d-%m-%Y %H:%M:%S")
                                if start_d <= dt_oi.date() <= end_d:
                                    ltp_val = float(row_oi["LTP"])
                                    str_val = parse_strength_value(row_oi["Strength"])
                                    records.append({
                                        "timestamp": dt_oi,
                                        "ltp": ltp_val,
                                        "strength": str_val,
                                    })
                            except Exception:
                                pass
                        if records:
                            raw_df = pd.DataFrame(records).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
                            raw_df["open"] = raw_df["ltp"].shift(1).fillna(raw_df["ltp"])
                            raw_df["high"] = raw_df[["open", "ltp"]].max(axis=1) + 0.5
                            raw_df["low"] = raw_df[["open", "ltp"]].min(axis=1) - 0.5
                            raw_df["close"] = raw_df["ltp"]
                            raw_df["volume"] = 50000
                            st.session_state["data_source_warning"] = None
                    except Exception as _oi_err:
                        logger.warning("Error loading oi_strength for OIPulse mode: %s", _oi_err)
            elif st.session_state.symbol.upper() == "NIFTY":
                nifty_master_file = f"data/nifty_{st.session_state.timeframe}m.csv"
                if os.path.exists(nifty_master_file):
                    try:
                        logger.info("Loading pre-calculated master dataset from %s", nifty_master_file)
                        loaded_m = load_csv(nifty_master_file)
                        if loaded_m is not None and not loaded_m.empty:
                            ts_dt = pd.to_datetime(loaded_m["timestamp"]).dt.date
                            in_range = loaded_m[(ts_dt >= start_d) & (ts_dt <= end_d)]
                            if not in_range.empty:
                                raw_df = in_range.copy()
                                st.session_state["data_source_warning"] = None
                    except Exception as _m_err:
                        logger.warning("Error reading master dataset %s: %s", nifty_master_file, _m_err)

            # 1. Fetch from Kotak Neo Gateway (Primary for F&O and Kotak instruments)
            if (raw_df is None or raw_df.empty) and (is_kotak_inst or is_fo):
                try:
                    kotak_gw = get_gateway("KOTAK")
                    logger.info("Fetching historical candles from Kotak Neo for %s (%s)", st.session_state.symbol, inst_key)
                    raw_df = kotak_gw.fetch_historical_candles(
                        symbol=st.session_state.symbol,
                        instrument_key=inst_key,
                        timeframe_minutes=st.session_state.timeframe,
                        start_date=start_d,
                        end_date=end_d,
                    )
                    if raw_df is None or raw_df.empty:
                        kotak_err = "Kotak API returned 0 candles for requested symbol and date window."
                except Exception as e:
                    kotak_err = str(e)
                    logger.warning("Kotak Neo candle fetch exception: %s", e)

            # Auto-cache freshly fetched Kotak candles so local CSV cache is enriched
            # Strictly protected: NEVER overwrite master datasets (nifty_1m, nifty_3m, etc.)
            protected_master = {
                f"nifty_{st.session_state.timeframe}m.csv".lower(),
                "nifty_1m.csv",
                "nifty_3m.csv",
                "nifty_5m.csv",
            }
            sym_clean = st.session_state.symbol.lower().replace(" ", "")
            target_fn = f"{sym_clean}_{st.session_state.timeframe}m.csv".lower()
            if raw_df is not None and len(raw_df) > 100 and (is_kotak_inst or is_fo) and not fo_mode_active and target_fn not in protected_master:
                try:
                    raw_df.to_csv(f"data/{sym_clean}_{st.session_state.timeframe}m.csv", index=False)
                    raw_df.to_csv(f"data/{st.session_state.symbol.lower()}_{st.session_state.timeframe}m.csv", index=False)
                except Exception as _e:
                    logger.debug("Failed saving cache CSV: %s", _e)
                st.session_state["data_source_warning"] = None

            # 2. If not fetched and Upstox token is available for standard Upstox keys
            if (raw_df is None or raw_df.empty) and token and not is_kotak_inst:
                try:
                    raw_df = fetch_range(inst_key, "minutes", st.session_state.timeframe, start_d, end_d, chunk_days=28)
                    if raw_df is None or len(raw_df) < 10:
                        upstox_err = "Upstox API returned 0 or truncated candles."
                        raw_df = None
                except Exception as e:
                    upstox_err = str(e)
                    logger.warning("Upstox fetch_range failed: %s", e)

            # 3. Local cached CSV fallback if live fetch returned empty
            if raw_df is None or raw_df.empty:
                sym_clean = st.session_state.symbol.lower().replace(" ", "")
                local_fallback = f"data/{st.session_state.symbol.lower()}_{st.session_state.timeframe}m.csv"
                fallback_clean = f"data/{sym_clean}_{st.session_state.timeframe}m.csv"
                fallback_5m = f"data/{st.session_state.symbol.lower()}_5m.csv"
                fallback_clean_5m = f"data/{sym_clean}_5m.csv"
                fallback_nifty = f"data/nifty_{st.session_state.timeframe}m.csv"
                fallback_nifty5m = "data/nifty_5m.csv"
                fallback_2 = f"data/reliance_{st.session_state.timeframe}m.csv"
                fallback_3 = "data/reliance_5m.csv"
                fallback_4 = "data/upstox_5m.csv"
                fb_found = None
                for fb in [local_fallback, fallback_clean, fallback_5m, fallback_clean_5m, fallback_nifty, fallback_nifty5m, fallback_2, fallback_3, fallback_4]:
                    if os.path.exists(fb):
                        logger.warning("Using local fallback file: %s", fb)
                        raw_df = load_csv(fb)
                        fb_found = fb
                        break

                if raw_df is not None and not raw_df.empty:
                    ts_dt = pd.to_datetime(raw_df["timestamp"]).dt.date
                    avail_min = ts_dt.min()
                    avail_max = ts_dt.max()

                    # Strict date coverage check:
                    # If requested range extends beyond local cache and broker fetch failed, throw explicit error!
                    if start_d < avail_min or end_d > avail_max:
                        broker_fail_reason = ""
                        if is_kotak_inst or is_fo:
                            broker_fail_reason = f"Kotak Neo live market fetch failed ({kotak_err or 'No candle records returned from Kotak API'}). "
                        elif not is_kotak_inst:
                            broker_fail_reason = f"Upstox live market fetch failed ({upstox_err or 'Token missing or no records returned'}). "

                        raw_df = None
                        raise ValueError(
                            f"{broker_fail_reason}Local fallback cache '{os.path.basename(fb_found)}' only covers {avail_min} to {avail_max}, "
                            f"which does not satisfy your requested date range ({start_d} to {end_d}). "
                            "Please verify your broker connection / credentials or select an available date range."
                        )

                    in_range = raw_df[(ts_dt >= start_d) & (ts_dt <= end_d)]
                    if not in_range.empty:
                        raw_df = in_range.copy()
                        st.session_state["data_source_warning"] = (
                            f"⚠️ **Live Broker Fetch Notice**: Live data fetch failed ({kotak_err or upstox_err or 'No live response'}). "
                            f"Backtest executed using local cached data `{os.path.basename(fb_found)}` ({avail_min} to {avail_max})."
                        )
                    else:
                        avail_min = ts_dt.min()
                        avail_max = ts_dt.max()
                        raw_df = None
                        raise ValueError(
                            f"Candle data for {st.session_state.symbol} in fallback cache only covers {avail_min} to {avail_max}, "
                            f"which does not match your requested backtest date range ({start_d} to {end_d}). "
                            "Please ensure Kotak/Upstox live market connection is active or select an available date range."
                        )

            if raw_df is None or raw_df.empty:
                broker_fail_reason = ""
                if is_kotak_inst or is_fo:
                    broker_fail_reason = f"Kotak Neo live fetch failed ({kotak_err or 'Broker returned 0 candles for instrument ' + str(inst_key)}). "
                elif not is_kotak_inst:
                    broker_fail_reason = f"Upstox live fetch failed ({upstox_err or 'Upstox API returned 0 candles'}). "
                raise ValueError(f"{broker_fail_reason}No candle data available for {inst_key} between {start_d} and {end_d}. Verify Kotak/Upstox connection or ensure local cached CSV exists in data/.")


            progress_bar.progress(50)
            status_text.text("Calculating 14 custom indicators...")

            ind = st.session_state.indicators
            risk_inp = st.session_state.risk
            cost_inp = st.session_state.cost

            custom_cfg = CustomStrategyConfig(
                name=strat_name,
                ema_enabled=ind["ema_enabled"], ema_fast=ind["ema_fast"], ema_slow=ind["ema_slow"], ema_weight=ind["ema_weight"],
                rsi_enabled=ind["rsi_enabled"], rsi_period=ind["rsi_period"], rsi_bull_low=ind["rsi_bull_low"],
                rsi_bull_high=ind["rsi_bull_high"], rsi_bear_low=ind["rsi_bear_low"], rsi_bear_high=ind["rsi_bear_high"], rsi_weight=ind["rsi_weight"],
                macd_enabled=ind["macd_enabled"], macd_fast=ind["macd_fast"], macd_slow=ind["macd_slow"], macd_signal=ind["macd_signal"], macd_weight=ind["macd_weight"],
                vwap_enabled=ind["vwap_enabled"], vwap_weight=ind["vwap_weight"],
                atr_enabled=ind["atr_enabled"], atr_period=ind["atr_period"], atr_stop_multiplier=risk_inp["atr_stop_multiplier"], atr_reward_risk_ratio=risk_inp["reward_risk_ratio"],
                adx_enabled=ind["adx_enabled"], adx_period=ind["adx_period"], adx_min=ind["adx_min"], adx_weight=ind["adx_weight"],
                volume_enabled=ind["volume_enabled"], volume_period=ind["volume_period"], volume_ratio_min=ind["volume_ratio_min"],
                volume_consecutive=int(ind.get("volume_consecutive", 3)), volume_weight=ind["volume_weight"],
                bollinger_enabled=ind["bollinger_enabled"], bollinger_period=ind["bollinger_period"], bollinger_std_dev=ind["bollinger_std_dev"], bollinger_weight=ind["bollinger_weight"],
                stoch_enabled=ind["stoch_enabled"], stoch_k=ind["stoch_k"], stoch_d=ind["stoch_d"], stoch_low=ind["stoch_low"], stoch_high=ind["stoch_high"], stoch_weight=ind["stoch_weight"],
                obv_enabled=ind["obv_enabled"], obv_ma_period=ind["obv_ma_period"], obv_weight=ind["obv_weight"],
                higher_high_enabled=bool(ind.get("higher_high_enabled", False)),
                higher_high_period=int(ind.get("higher_high_period", 5)),
                higher_high_weight=int(ind.get("higher_high_weight", 1)),
                lower_low_enabled=bool(ind.get("lower_low_enabled", False)),
                lower_low_period=int(ind.get("lower_low_period", 5)),
                lower_low_weight=int(ind.get("lower_low_weight", 1)),
                hma_enabled=bool(ind.get("hma_enabled", False)),
                hma_period=int(ind.get("hma_period", 100)),
                hma_weight=int(ind.get("hma_weight", 1)),
                supertrend_enabled=bool(ind.get("supertrend_enabled", False)),
                supertrend_period=int(ind.get("supertrend_period", 10)),
                supertrend_mult_simple=float(ind.get("supertrend_mult_simple", 2.0)),
                supertrend_mult_complex=float(ind.get("supertrend_mult_complex", 4.0)),
                supertrend_weight=int(ind.get("supertrend_weight", 1)),
                long_threshold=ind["long_threshold"], short_threshold=ind["short_threshold"],
                fixed_target_pct=float(ind.get("fixed_target_pct", 3)), fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 2)),
                trailing_sl_enabled=bool(ind.get("trailing_sl_enabled", False)),
                trail_beyond_target=bool(ind.get("trail_beyond_target", False)),
            )

            indicators_df = calculate_custom_indicators(raw_df, custom_cfg)

            progress_bar.progress(70)
            status_text.text("Simulating execution via Backtester...")

            trade_side = ind.get("trade_side", "BOTH")
            short_lot_size = int(ind.get("short_lot_size", 1)) if trade_side != "LONG" else None
            snooze_minutes = int(risk_inp.get("snooze_minutes") or ind.get("snooze_minutes") or 5)

            risk_cfg = RiskConfig(
                capital=float(risk_inp["capital"]),
                risk_per_trade_pct=float(risk_inp["risk_per_trade_pct"]),
                max_daily_loss=float(risk_inp["max_daily_loss"]),
                max_open_positions=int(risk_inp["max_open_positions"]),
                max_exposure_pct=float(risk_inp["max_exposure_pct"]),
                max_quantity=int(risk_inp["max_quantity"]),
                max_consecutive_losses=int(risk_inp["max_consecutive_losses"]),
                lot_size=int(risk_inp["lot_size"]),
                tick_size=float(risk_inp["tick_size"]),
                trade_side=trade_side,
                short_lot_size=short_lot_size,
                snooze_minutes=snooze_minutes,
                atr_stop_multiplier=float(risk_inp["atr_stop_multiplier"]),
                reward_risk_ratio=float(risk_inp["reward_risk_ratio"]),
                fixed_target_pct=float(ind.get("fixed_target_pct", 3)),
                fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 2)),
                trailing_sl_enabled=bool(ind.get("trailing_sl_enabled", False)),
                trail_beyond_target=bool(ind.get("trail_beyond_target", False)),
                entry_start=risk_inp["entry_start"],
                entry_end=risk_inp["entry_end"],
                square_off_time=risk_inp["square_off_time"],
            )

            cost_cfg = CostConfig(
                cost_mode=cost_inp["cost_mode"],
                flat_cost_per_order=float(cost_inp["flat_cost_per_order"]),
                slippage_mode=cost_inp["slippage_mode"],
                flat_slippage_per_order=float(cost_inp["flat_slippage_per_order"]),
                slippage_pct=float(cost_inp.get("slippage_pct", 0.0005)),
            )

            bt_cfg = BacktestConfig(
                allow_short=st.session_state.allow_short or (trade_side in ("SHORT", "BOTH")),
                trade_side=trade_side,
                short_lot_size=short_lot_size,
                snooze_minutes=snooze_minutes,
                trailing_sl_enabled=bool(ind.get("trailing_sl_enabled", False)),
                trail_beyond_target=bool(ind.get("trail_beyond_target", False)),
                fo_mode=st.session_state.get("backtest_fo_mode", "Normal Mode"),
            )

            # Full run
            trades_df, bt = run_backtest(indicators_df, st.session_state.symbol, custom_cfg, risk_cfg, cost_cfg, bt_cfg)
            full_metrics = compute_metrics(trades_df, risk_cfg.capital, trading_days(indicators_df))

            # Train/Test Split if requested
            in_sample_res, out_sample_res = None, None
            if st.session_state.holdout_pct > 0 and len(trading_days(indicators_df)) >= 4:
                total_days = len(trading_days(indicators_df))
                out_days = int(np.ceil(total_days * (st.session_state.holdout_pct / 100.0)))
                in_days = total_days - out_days
                in_df, out_df = split_by_days(indicators_df, in_days)

                in_trades, _ = run_backtest(in_df, st.session_state.symbol, custom_cfg, risk_cfg, cost_cfg, bt_cfg)
                out_trades, _ = run_backtest(out_df, st.session_state.symbol, custom_cfg, risk_cfg, cost_cfg, bt_cfg)

                in_sample_res = {
                    "trades": in_trades,
                    "metrics": compute_metrics(in_trades, risk_cfg.capital, trading_days(in_df)),
                    "days": len(trading_days(in_df)),
                }
                out_sample_res = {
                    "trades": out_trades,
                    "metrics": compute_metrics(out_trades, risk_cfg.capital, trading_days(out_df)),
                    "days": len(trading_days(out_df)),
                }

            progress_bar.progress(90)
            status_text.text("Saving execution to backtest audit database...")

            full_config_save = {
                "strategy_name": strat_name,
                "symbol": st.session_state.symbol,
                "isin": st.session_state.isin,
                "equity_name": st.session_state.equity_name,
                "date_from": str(start_d),
                "date_to": str(end_d),
                "timeframe": st.session_state.timeframe,
                "allow_short": st.session_state.allow_short,
                "holdout_pct": st.session_state.holdout_pct,
                "indicators": ind,
                "risk": {k: str(v) if isinstance(v, (time, date)) else v for k, v in risk_inp.items()},
                "cost": cost_inp,
            }

            run_id = db.save_run(
                symbol=st.session_state.symbol,
                isin=st.session_state.isin,
                date_from=str(start_d),
                date_to=str(end_d),
                timeframe=f"{st.session_state.timeframe}m",
                strategy_version=strat_name,
                config=full_config_save,
                metrics=full_metrics,
                trades_df=trades_df,
                status="COMPLETED",
            )

            log_audit_event(
                "BACKTEST_COMPLETED",
                details={
                    "symbol": st.session_state.symbol,
                    "run_id": run_id,
                    "trades": full_metrics.get("num_trades", len(trades_df)),
                    "net_pnl": round(float(full_metrics.get("net_pnl", 0.0)), 2),
                    "win_rate_pct": round(float(full_metrics.get("win_rate_pct", 0.0)), 1),
                },
            )

            progress_bar.progress(100)
            status_text.text("Complete!")

            # Update active results
            run_data = db.get_run(run_id)
            st.session_state.active_run = {
                "run_id": run_id,
                "run_data": run_data,
                "trades_df": trades_df,
                "metrics": full_metrics,
                "in_sample": in_sample_res,
                "out_sample": out_sample_res,
                "indicators_df": indicators_df,
                "raw_df": raw_df,
                "custom_cfg": custom_cfg,
            }
            st.session_state.step = 2
            st.rerun()

        except Exception as e:
            progress_bar.progress(100)
            logger.exception("Backtest execution error: %s", e)
            st.error(f"❌ Execution Failed: {str(e)}")
            st.info("Check your Upstox access token in .env, verify date ranges, and ensure network access.")
            if st.button("⬅️ Return to Step 1"):
                st.session_state.step = 1
                st.rerun()


    def render_step_1_strategy_config(equities_df):
        st.header("Step 1: Strategy Configuration & Rules")
        st.caption("Configure technical indicators, scoring weights, risk guardrails, and instrument selection. Save your complete strategy in your Strategies-List.")

        # Strategy Selection & Management Toolbar
        saved_strategies = list_strategy_names()
        target_s1 = st.session_state.get("selected_strategy_name") or (saved_strategies[0] if saved_strategies else "EMA_RSI_MACD_Balanced")
        if target_s1 not in saved_strategies:
            saved_strategies.insert(0, target_s1)
        st.session_state["step1_load_strat_select"] = target_s1
        curr_strat_idx = saved_strategies.index(target_s1) if target_s1 in saved_strategies else 0
        current_strat = target_s1

        st.markdown("#### 📂 Strategies-List & Management")
        col_load, col_new, col_del = st.columns([3, 1, 1])

        def on_load_strategy_change():
            chosen = st.session_state.get("step1_load_strat_select")
            if chosen:
                load_strategy_into_session(chosen)
                st.session_state.strategy_name_input = chosen
                try:
                    st.session_state["step1_strat_name_input_field"] = chosen
                except Exception:
                    pass

        with col_load:
            chosen_strat = st.selectbox(
                "Load Strategy (Strategies-List)",
                saved_strategies,
                index=curr_strat_idx,
                key="step1_load_strat_select",
                on_change=on_load_strategy_change,
                help="Select any saved strategy template to view or alter its rules.",
            )
            if chosen_strat != current_strat:
                load_strategy_into_session(chosen_strat)
                st.session_state.strategy_name_input = chosen_strat
                try:
                    st.session_state["step1_strat_name_input_field"] = chosen_strat
                except Exception:
                    pass
                st.rerun()

        with col_new:
            st.write("")
            st.write("")
            if st.button("➕ New Strategy", use_container_width=True, help="Reset to standard defaults and create a new strategy"):
                reset_to_default_strategy()
                suggested = auto_suggest_strategy_name(st.session_state.indicators)
                st.session_state.strategy_name_input = suggested
                try:
                    st.session_state["step1_strat_name_input_field"] = suggested
                except Exception:
                    pass
                st.rerun()

        with col_del:
            st.write("")
            st.write("")
            if st.button("🗑️ Delete", use_container_width=True, help="Delete the current custom strategy", disabled=(len(saved_strategies) <= 1)):
                deleted = delete_strategy(current_strat)
                if deleted:
                    st.success(f"Deleted '{current_strat}'")
                    saved_remaining = list_strategy_names()
                    new_active = saved_remaining[0] if saved_remaining else "EMA_RSI_MACD_Balanced"
                    load_strategy_into_session(new_active)
                    st.session_state.strategy_name_input = new_active
                    try:
                        st.session_state["step1_strat_name_input_field"] = new_active
                    except Exception:
                        pass
                    st.rerun()
                else:
                    st.warning("Cannot delete default starter template.")

        # Auto-suggested Strategy Name row
        suggested_name = auto_suggest_strategy_name(st.session_state.indicators)
        col_name, col_suggest = st.columns([3, 1])
        with col_name:
            if "step1_strat_name_input_field" not in st.session_state:
                st.session_state["step1_strat_name_input_field"] = st.session_state.get("strategy_name_input", suggested_name)

            strat_name_input = st.text_input(
                "🏷️ Strategy Name",
                key="step1_strat_name_input_field",
                help="Meaningful name for this strategy based on chosen indicators. You can customize this.",
            )
            st.session_state.strategy_name_input = strat_name_input
            if strat_name_input and strat_name_input.strip():
                st.session_state.selected_strategy_name = strat_name_input.strip()
                try:
                    st.session_state["step2_strat_selector"] = strat_name_input.strip()
                except Exception:
                    pass
        def on_auto_suggest_click():
            s_name = auto_suggest_strategy_name(st.session_state.indicators)
            st.session_state.strategy_name_input = s_name
            st.session_state.selected_strategy_name = s_name
            try:
                st.session_state["step1_strat_name_input_field"] = s_name
            except Exception:
                pass
            try:
                st.session_state["step2_strat_selector"] = s_name
            except Exception:
                pass

        with col_suggest:
            st.write("")
            st.write("")
            st.button(
                "✨ Auto-Suggest Name",
                key="btn_auto_suggest_strat_name",
                use_container_width=True,
                help="Generate a meaningful name based on active indicators",
                on_click=on_auto_suggest_click,
            )

        st.caption(f"💡 *Suggested based on active indicators:* `{suggested_name}`")
        st.markdown("---")

        ind = st.session_state.indicators
        risk = st.session_state.risk
        cost = st.session_state.cost

        tab_ind, tab_risk, tab_costs = st.tabs(["📊 Technical Indicators", "🛡️ Risk Management Parameters", "💰 Cost & Slippage Model"])

        with tab_ind:
            t_col1, t_col2 = st.columns([3, 1])
            with t_col1:
                st.subheader("Indicator Rules & Scoring Weights")
                st.caption("Each enabled indicator awards or subtracts points based on candle conditions. Points accumulate into a net signal score.")
            with t_col2:
                if st.button("📖 Strategy Guide", key="btn_tab_guide", use_container_width=True):
                    show_guide_dialog()

            ind_toggle_keys = [
                ("ema_enabled", "c_ema"),
                ("rsi_enabled", "c_rsi"),
                ("macd_enabled", "c_macd"),
                ("vwap_enabled", "c_vwap"),
                ("atr_enabled", "c_atr"),
                ("adx_enabled", "c_adx"),
                ("volume_enabled", "c_vol"),
                ("bollinger_enabled", "c_bb"),
                ("stoch_enabled", "c_stoch"),
                ("obv_enabled", "c_obv"),
                ("higher_high_enabled", "c_hh"),
                ("lower_low_enabled", "c_ll"),
                ("hma_enabled", "c_hma"),
                ("supertrend_enabled", "c_supertrend"),
            ]

            for ik, wk in ind_toggle_keys:
                if wk not in st.session_state:
                    st.session_state[wk] = ind.get(ik, False if "enabled" in ik and ("higher_high" in ik or "lower_low" in ik or "hma" in ik or "supertrend" in ik) else True)

            def sync_side_for_indicators():
                hh = st.session_state.indicators.get("higher_high_enabled", False)
                ll = st.session_state.indicators.get("lower_low_enabled", False)
                if hh and ll:
                    st.session_state.indicators["trade_side"] = "BOTH"
                    st.session_state.risk["trade_side"] = "BOTH"
                elif hh:
                    st.session_state.indicators["trade_side"] = "LONG"
                    st.session_state.risk["trade_side"] = "LONG"
                elif ll:
                    st.session_state.indicators["trade_side"] = "SHORT"
                    st.session_state.risk["trade_side"] = "SHORT"

            def on_select_all_toggle():
                target_val = st.session_state["select_all_ind"]
                for ik, wk in ind_toggle_keys:
                    st.session_state[wk] = target_val
                    st.session_state.indicators[ik] = target_val
                sync_side_for_indicators()

            def on_ind_checkbox_change(ik, wk):
                st.session_state.indicators[ik] = st.session_state[wk]
                st.session_state["select_all_ind"] = all(
                    st.session_state.get(w, False) for _, w in ind_toggle_keys
                )
                sync_side_for_indicators()

            if "select_all_ind" not in st.session_state:
                st.session_state["select_all_ind"] = all(
                    st.session_state.get(w, False) for _, w in ind_toggle_keys
                )

            st.checkbox(
                "☑️ Select All / Deselect All Indicators",
                key="select_all_ind",
                on_change=on_select_all_toggle,
                help="Check to enable all 14 indicators, or uncheck to disable all indicators at once."
            )

            i_col1, i_col2 = st.columns(2)

            with i_col1:
                # 1. EMA
                with st.expander("1. EMA (Exponential Moving Average)", expanded=True):
                    ind["ema_enabled"] = st.checkbox("Enable EMA", key="c_ema", on_change=on_ind_checkbox_change, args=("ema_enabled", "c_ema"))
                    e1, e2, e3 = st.columns(3)
                    ind["ema_fast"] = e1.number_input("Fast Period", min_value=1, max_value=100, value=ind["ema_fast"])
                    ind["ema_slow"] = e2.number_input("Slow Period", min_value=1, max_value=200, value=ind["ema_slow"])
                    ind["ema_weight"] = e3.number_input("Weight (Points)", min_value=1, max_value=5, value=ind["ema_weight"])

                # 2. RSI
                with st.expander("2. RSI (Relative Strength Index)", expanded=True):
                    ind["rsi_enabled"] = st.checkbox("Enable RSI", key="c_rsi", on_change=on_ind_checkbox_change, args=("rsi_enabled", "c_rsi"))
                    r1, r2, r3, r4 = st.columns(4)
                    ind["rsi_period"] = r1.number_input("RSI Period", min_value=2, max_value=50, value=ind["rsi_period"])
                    ind["rsi_bull_low"] = r2.number_input("Bull Zone Low", min_value=40.0, max_value=60.0, value=ind["rsi_bull_low"])
                    ind["rsi_bull_high"] = r3.number_input("Bull Zone High", min_value=60.0, max_value=85.0, value=ind["rsi_bull_high"])
                    ind["rsi_weight"] = r4.number_input("Weight", min_value=1, max_value=5, value=ind["rsi_weight"], key="w_rsi")

                # 3. MACD
                with st.expander("3. MACD (Moving Average Convergence Divergence)", expanded=True):
                    ind["macd_enabled"] = st.checkbox("Enable MACD", key="c_macd", on_change=on_ind_checkbox_change, args=("macd_enabled", "c_macd"))
                    m1, m2, m3, m4 = st.columns(4)
                    ind["macd_fast"] = m1.number_input("Fast EMA", min_value=2, max_value=50, value=ind["macd_fast"])
                    ind["macd_slow"] = m2.number_input("Slow EMA", min_value=10, max_value=100, value=ind["macd_slow"])
                    ind["macd_signal"] = m3.number_input("Signal Period", min_value=2, max_value=30, value=ind["macd_signal"])
                    ind["macd_weight"] = m4.number_input("Weight", min_value=1, max_value=5, value=ind["macd_weight"], key="w_macd")

                # 4. VWAP
                with st.expander("4. VWAP (Volume-Weighted Average Price)", expanded=True):
                    ind["vwap_enabled"] = st.checkbox("Enable VWAP", key="c_vwap", on_change=on_ind_checkbox_change, args=("vwap_enabled", "c_vwap"))
                    st.caption("VWAP is computed per intraday session. Bullish if Close > VWAP, Bearish if Close < VWAP.")
                    ind["vwap_weight"] = st.number_input("VWAP Weight", min_value=1, max_value=5, value=ind["vwap_weight"])

                # 5. ATR (Levels Only)
                with st.expander("5. ATR (Average True Range) - Stops & Targets", expanded=True):
                    ind["atr_enabled"] = st.checkbox("Enable ATR Stops", key="c_atr", on_change=on_ind_checkbox_change, args=("atr_enabled", "c_atr"))
                    ind["atr_period"] = st.number_input("ATR Period", min_value=2, max_value=50, value=ind["atr_period"])
                    st.caption(f"Stop multiplier ({risk['atr_stop_multiplier']}x) & Reward:Risk ({risk['reward_risk_ratio']}:1) configured under Risk Management tab.")

                # 11. Higher High (NEW)
                with st.expander("11. Higher High (NEW)", expanded=True):
                    ind["higher_high_enabled"] = st.checkbox(
                        "Enable Higher High",
                        key="c_hh",
                        on_change=on_ind_checkbox_change,
                        args=("higher_high_enabled", "c_hh"),
                        help="Condition: Current candle high > previous swing high. Automatically locks trade direction to LONG only."
                    )
                    st.caption("Condition: **Current candle high > previous swing high**. Automatically locks Long trade direction only.")
                    hh1, hh2 = st.columns(2)
                    ind["higher_high_period"] = hh1.number_input(
                        "Swing Lookback Period",
                        min_value=1,
                        max_value=50,
                        value=ind.get("higher_high_period", 5),
                        help="Lookback period of previous candles to identify the swing high."
                    )
                    ind["higher_high_weight"] = hh2.number_input(
                        "Higher High Weight",
                        min_value=1,
                        max_value=5,
                        value=ind.get("higher_high_weight", 1),
                        key="w_hh"
                    )

                # 13. HMA (NEW)
                with st.expander("13. HMA (Hull Moving Average 100) (NEW)", expanded=True):
                    ind["hma_enabled"] = st.checkbox(
                        "Enable HMA(100)",
                        key="c_hma",
                        on_change=on_ind_checkbox_change,
                        args=("hma_enabled", "c_hma"),
                        help="Condition: Signal bar close > HMA(100) adds score for bullish consensus (or < HMA for bearish)."
                    )
                    st.caption("Condition: **Signal bar close > current HMA(100)** adds score for this logic.")
                    h1, h2 = st.columns(2)
                    ind["hma_period"] = h1.number_input("HMA Period", min_value=5, max_value=300, value=ind.get("hma_period", 100))
                    ind["hma_weight"] = h2.number_input("HMA Weight", min_value=1, max_value=5, value=ind.get("hma_weight", 1), key="w_hma")

            with i_col2:
                # 6. ADX
                with st.expander("6. ADX (Average Directional Index)", expanded=True):
                    ind["adx_enabled"] = st.checkbox("Enable ADX", key="c_adx", on_change=on_ind_checkbox_change, args=("adx_enabled", "c_adx"))
                    a1, a2, a3 = st.columns(3)
                    ind["adx_period"] = a1.number_input("ADX Period", min_value=2, max_value=50, value=ind["adx_period"])
                    ind["adx_min"] = a2.number_input("Min Trend ADX", min_value=10.0, max_value=50.0, value=ind["adx_min"])
                    ind["adx_weight"] = a3.number_input("ADX Weight", min_value=1, max_value=5, value=ind["adx_weight"], key="w_adx")

                # 7. Volume Confirmation
                with st.expander("7. Volume Ratio (Confirmation)", expanded=True):
                    ind["volume_enabled"] = st.checkbox("Enable Volume", key="c_vol", on_change=on_ind_checkbox_change, args=("volume_enabled", "c_vol"))
                    st.caption("Requires N consecutive candles where EACH candle close is in trend and volume ratio >= min within intraday.")
                    v1, v2, v3, v4 = st.columns(4)
                    ind["volume_period"] = v1.number_input("Volume MA Period", min_value=5, max_value=100, value=ind["volume_period"])
                    ind["volume_ratio_min"] = v2.number_input("Min Volume Ratio", min_value=1.0, max_value=3.0, value=ind["volume_ratio_min"], step=0.1)
                    cur_consec = int(ind.get("volume_consecutive", 3))
                    consec_opts = [1, 2, 3, 4, 5]
                    consec_idx = consec_opts.index(cur_consec) if cur_consec in consec_opts else 2
                    ind["volume_consecutive"] = v3.selectbox(
                        "consecutive period:",
                        options=consec_opts,
                        index=consec_idx,
                        help="Checks last N consecutive candles: EACH must have close > prev close AND volume ratio >= min (LONG), or close < prev close AND volume ratio >= min (SHORT). 9:15 open candle is not compared."
                    )
                    ind["volume_weight"] = v4.number_input("Volume Weight", min_value=1, max_value=5, value=ind["volume_weight"], key="w_vol")

                # 8. Bollinger Bands (NEW)
                with st.expander("8. Bollinger Bands (NEW)", expanded=True):
                    ind["bollinger_enabled"] = st.checkbox("Enable Bollinger", key="c_bb", on_change=on_ind_checkbox_change, args=("bollinger_enabled", "c_bb"))
                    b1, b2, b3 = st.columns(3)
                    ind["bollinger_period"] = b1.number_input("BB Period", min_value=5, max_value=100, value=ind["bollinger_period"])
                    ind["bollinger_std_dev"] = b2.number_input("Std Deviations", min_value=0.5, max_value=4.0, value=ind["bollinger_std_dev"], step=0.1)
                    ind["bollinger_weight"] = b3.number_input("BB Weight", min_value=1, max_value=5, value=ind["bollinger_weight"], key="w_bb")

                # 9. Stochastic (NEW)
                with st.expander("9. Stochastic Oscillator (NEW)", expanded=True):
                    ind["stoch_enabled"] = st.checkbox("Enable Stochastic", key="c_stoch", on_change=on_ind_checkbox_change, args=("stoch_enabled", "c_stoch"))
                    s1, s2, s3 = st.columns(3)
                    ind["stoch_k"] = s1.number_input("%K Period", min_value=2, max_value=50, value=ind["stoch_k"])
                    ind["stoch_d"] = s2.number_input("%D Period", min_value=1, max_value=20, value=ind["stoch_d"])
                    ind["stoch_weight"] = s3.number_input("Stoch Weight", min_value=1, max_value=5, value=ind["stoch_weight"], key="w_stoch")

                # 10. OBV (NEW)
                with st.expander("10. On-Balance Volume (NEW)", expanded=True):
                    ind["obv_enabled"] = st.checkbox("Enable OBV", key="c_obv", on_change=on_ind_checkbox_change, args=("obv_enabled", "c_obv"))
                    o1, o2 = st.columns(2)
                    ind["obv_ma_period"] = o1.number_input("OBV MA Period", min_value=5, max_value=100, value=ind["obv_ma_period"])
                    ind["obv_weight"] = o2.number_input("OBV Weight", min_value=1, max_value=5, value=ind["obv_weight"], key="w_obv")

                # 12. Lower Low (NEW)
                with st.expander("12. Lower Low (NEW)", expanded=True):
                    ind["lower_low_enabled"] = st.checkbox(
                        "Enable Lower Low",
                        key="c_ll",
                        on_change=on_ind_checkbox_change,
                        args=("lower_low_enabled", "c_ll"),
                        help="Condition: Current candle low < previous swing low. Automatically locks trade direction to SHORT only."
                    )
                    st.caption("Condition: **Current candle low < previous swing low**. Automatically locks Short trade direction only.")
                    ll1, ll2 = st.columns(2)
                    ind["lower_low_period"] = ll1.number_input(
                        "Swing Lookback Period",
                        min_value=1,
                        max_value=50,
                        value=ind.get("lower_low_period", 5),
                        help="Lookback period of previous candles to identify the swing low."
                    )
                    ind["lower_low_weight"] = ll2.number_input(
                        "Lower Low Weight",
                        min_value=1,
                        max_value=5,
                        value=ind.get("lower_low_weight", 1),
                        key="w_ll"
                    )

                # 14. Supertrend (NEW)
                with st.expander("14. Supertrend ((10,2) simple and (10,4) complex trend applied by default) (NEW)", expanded=True):
                    ind["supertrend_enabled"] = st.checkbox(
                        "Enable Supertrend",
                        key="c_supertrend",
                        on_change=on_ind_checkbox_change,
                        args=("supertrend_enabled", "c_supertrend"),
                        help="(10,2) simple and (10,4) complex trend applied by default. Condition: Signal bar close > Supertrend (10,2) AND Signal bar close > Supertrend (10,4)."
                    )
                    st.caption("(10,2) simple and (10,4) complex trend applied by default.<br>Condition: AND mode { <b>Signal bar close > current Supertrend (10,2)</b> AND <b>Signal bar close > current Supertrend (10,4)</b> } adds score for this logic.", unsafe_allow_html=True)
                    st1, st2, st3, st4 = st.columns(4)
                    ind["supertrend_period"] = st1.number_input("Lookback Period", min_value=2, max_value=50, value=ind.get("supertrend_period", 10))
                    ind["supertrend_mult_simple"] = st2.number_input("Simple Multiplier", min_value=0.5, max_value=10.0, value=float(ind.get("supertrend_mult_simple", 2.0)), step=0.5)
                    ind["supertrend_mult_complex"] = st3.number_input("Complex Multiplier", min_value=0.5, max_value=10.0, value=float(ind.get("supertrend_mult_complex", 4.0)), step=0.5)
                    ind["supertrend_weight"] = st4.number_input("Supertrend Weight", min_value=1, max_value=5, value=ind.get("supertrend_weight", 1), key="w_supertrend")

            # Dynamic Max Score Calculation
            max_possible = 0
            if ind["ema_enabled"]: max_possible += ind["ema_weight"]
            if ind["rsi_enabled"]: max_possible += ind["rsi_weight"]
            if ind["macd_enabled"]: max_possible += ind["macd_weight"]
            if ind["vwap_enabled"]: max_possible += ind["vwap_weight"]
            if ind["adx_enabled"]: max_possible += ind["adx_weight"]
            if ind["volume_enabled"]: max_possible += ind["volume_weight"]
            if ind["bollinger_enabled"]: max_possible += ind["bollinger_weight"]
            if ind["stoch_enabled"]: max_possible += ind["stoch_weight"]
            if ind["obv_enabled"]: max_possible += ind["obv_weight"]
            if ind.get("higher_high_enabled"): max_possible += ind.get("higher_high_weight", 1)
            if ind.get("lower_low_enabled"): max_possible += ind.get("lower_low_weight", 1)
            if ind.get("hma_enabled"): max_possible += ind.get("hma_weight", 1)
            if ind.get("supertrend_enabled"): max_possible += ind.get("supertrend_weight", 1)
            max_possible = max(max_possible, 1)

            th_head_col, th_help_col = st.columns([3, 1])
            with th_head_col:
                st.markdown(f"#### Signal Thresholds (Max Possible Score: **{max_possible}**)")
            with th_help_col:
                if st.button("❓ How Thresholds Work", key="btn_help_thresholds", use_container_width=True):
                    show_guide_dialog()

            if ind.get("volume_enabled"):
                st.info(f"📊 **Volume Trend Gate Active:** Trade entries require {ind.get('volume_consecutive', 3)} consecutive candles meeting price close direction AND volume ≥ {ind.get('volume_ratio_min', 1.2)}× MA.")
            if ind.get("higher_high_enabled"):
                st.info(f"📈 **Higher High Active (Indicator 11):** Only LONG trades taken. Requires Current Candle High > Previous Swing High (Lookback: {ind.get('higher_high_period', 5)} candles).")
            if ind.get("lower_low_enabled"):
                st.info(f"📉 **Lower Low Active (Indicator 12):** Only SHORT trades taken. Requires Current Candle Low < Previous Swing Low (Lookback: {ind.get('lower_low_period', 5)} candles).")
            if ind.get("hma_enabled"):
                st.info(f"🌊 **HMA(100) Active (Indicator 13):** Adds {ind.get('hma_weight', 1)} pt(s) when Signal bar close > HMA({ind.get('hma_period', 100)}) (or short when < HMA).")
            if ind.get("supertrend_enabled"):
                st.info(f"⚡ **Supertrend Active (Indicator 14):** (10,2) simple and (10,4) complex trend applied by default in AND mode. Adds {ind.get('supertrend_weight', 1)} pt(s) when Signal bar close > Supertrend(10,2) AND > Supertrend(10,4).")

            th1, th2 = st.columns(2)
            ind["long_threshold"] = th1.number_input(
                f"LONG Threshold (>= points, max {max_possible})",
                min_value=1, max_value=max_possible, value=min(ind["long_threshold"], max_possible)
            )
            ind["short_threshold"] = th2.number_input(
                f"SHORT Threshold (<= points, min -{max_possible})",
                min_value=-max_possible, max_value=-1, value=max(ind["short_threshold"], -max_possible)
            )

            st.markdown("#### Fixed Target & Stoploss")
            st.caption("Calculate target and stoploss as a fixed percentage of entry price for each trade.")
            pct_col1, pct_col2 = st.columns(2)
            target_options = list(range(1, 51))
            stoploss_options = list(range(1, 11))

            current_tgt = int(ind.get("fixed_target_pct", 3))
            current_tgt_idx = target_options.index(current_tgt) if current_tgt in target_options else 2

            ind["fixed_target_pct"] = pct_col1.selectbox(
                "Fixed Target Percentage",
                options=target_options,
                index=current_tgt_idx,
                format_func=lambda x: f"{x}%",
                key="sb_fixed_target_pct",
                help="Fixed profit target percentage for each trade."
            )
            risk["fixed_target_pct"] = ind["fixed_target_pct"]

            current_sl = int(ind.get("fixed_stoploss_pct", 2))
            current_sl_idx = stoploss_options.index(current_sl) if current_sl in stoploss_options else 1

            ind["fixed_stoploss_pct"] = pct_col2.selectbox(
                "Fixed Stoploss Percentage",
                options=stoploss_options,
                index=current_sl_idx,
                format_func=lambda x: f"{x}%",
                key="sb_fixed_stoploss_pct",
                help="Fixed stoploss percentage for each trade."
            )
            risk["fixed_stoploss_pct"] = ind["fixed_stoploss_pct"]

            # Trailing Stop-Loss Section (Model B)
            st.markdown("##### 📈 Trailing Stop-Loss (Model B: Breakeven & Target-Lock)")
            tsl_col1, tsl_col2 = st.columns(2)
            ind["trailing_sl_enabled"] = tsl_col1.checkbox(
                "Enable Trailing Stop-Loss",
                value=bool(ind.get("trailing_sl_enabled", False)),
                key="cb_trailing_sl_enabled",
                help="Milestone 1: Moves Stop Loss to Breakeven (Entry Price) once profit reaches 50% of Fixed Target.\nMilestone 2: Above 50% of Fixed Target, continuously trails the peak price by Fixed Stoploss %."
            )
            if ind["trailing_sl_enabled"]:
                ind["trail_beyond_target"] = tsl_col2.checkbox(
                    "Trail beyond Fixed Target (let runner trades run)",
                    value=bool(ind.get("trail_beyond_target", False)),
                    key="cb_trail_beyond_target",
                    help="If checked, trades won't cap at Fixed Target, but will keep trailing to capture runaway upside."
                )
                mode_desc = "Runs past Fixed Target" if ind.get("trail_beyond_target") else "Secures Fixed Target when reached"
                st.info(
                    f"🛡️ **Trailing Stop-Loss Active (Model B):**\n\n"
                    f"• **Milestone 1 (Breakeven):** When profit reaches **{ind['fixed_target_pct']/2:.1f}%** (50% of target), Stop Loss moves to Entry Price (Zero Risk).\n\n"
                    f"• **Milestone 2 (Profit Trailing):** Continuously trails the highest peak by **{ind['fixed_stoploss_pct']}%**, locking in gains.\n\n"
                    f"• **Target Handling:** {mode_desc}."
                )
            else:
                ind["trail_beyond_target"] = False
            risk["trailing_sl_enabled"] = ind["trailing_sl_enabled"]
            risk["trail_beyond_target"] = ind["trail_beyond_target"]

            st.markdown("#### Trade Direction & Short Sizing")
            st.caption("Select allowed trading side (LONG, SHORT, or BOTH) and configure fixed 1-lot sizing for short trades.")
            side_col1, side_col2 = st.columns(2)

            hh_active = bool(ind.get("higher_high_enabled", False))
            ll_active = bool(ind.get("lower_low_enabled", False))

            side_options = ["LONG", "SHORT", "BOTH"]
            if hh_active and ll_active:
                current_side = "BOTH"
                side_disabled = True
                side_caption = "🔒 Locked to **BOTH**: Higher High (Long) and Lower Low (Short) are both enabled."
                side_help = "Automatically locked to BOTH because both Higher High and Lower Low indicators are enabled."
            elif hh_active:
                current_side = "LONG"
                side_disabled = True
                side_caption = "🔒 Locked to **LONG**: Indicator 11 (Higher High) takes only Long trades."
                side_help = "Automatically locked to LONG because Higher High indicator is enabled."
            elif ll_active:
                current_side = "SHORT"
                side_disabled = True
                side_caption = "🔒 Locked to **SHORT**: Indicator 12 (Lower Low) takes only Short trades."
                side_help = "Automatically locked to SHORT because Lower Low indicator is enabled."
            else:
                current_side = ind.get("trade_side", "BOTH")
                side_disabled = False
                side_caption = None
                side_help = "Filter trades: LONG only, SHORT only, or BOTH."

            current_side_idx = side_options.index(current_side) if current_side in side_options else 2

            chosen_side = side_col1.selectbox(
                "Long/Short Side",
                options=side_options,
                index=current_side_idx,
                disabled=side_disabled,
                help=side_help
            )
            if side_caption:
                side_col1.caption(side_caption)

            ind["trade_side"] = chosen_side
            risk["trade_side"] = chosen_side

            # If user chooses SHORT or BOTH, enable Short Lot Size textbox with value 1 (non-editable)
            # If user chooses LONG, disable Short Lot Size
            if chosen_side == "LONG":
                side_col2.text_input(
                    "Short Lot Size:",
                    value="Disabled",
                    disabled=True,
                    help="Disabled because only LONG trades are selected."
                )
                ind["short_lot_size"] = 1
                risk["short_lot_size"] = 1
            else:
                side_col2.text_input(
                    "Short Lot Size:",
                    value="1",
                    disabled=True,
                    help="Fixed at 1 lot for SHORT trades."
                )
                ind["short_lot_size"] = 1
                risk["short_lot_size"] = 1

            st.markdown("#### Trade Snooze Period")
            st.caption("Prevent new trade entries for 5 to 10 minutes (or custom duration) after previous trade exits.")
            snooze_col1, snooze_col2 = st.columns(2)

            current_snooze_str = ind.get("snooze_period_str", "5 min")
            snooze_input = snooze_col1.text_input(
                "Trade Snooze Period",
                value=current_snooze_str,
                key="ti_snooze_period",
                help="Specify cooldown duration after trade exit (e.g. '5 min', '10 min', or '0' to disable)."
            )

            import re
            snooze_digits = re.findall(r'\d+', snooze_input or "")
            parsed_snooze_mins = int(snooze_digits[0]) if snooze_digits else 0

            ind["snooze_period_str"] = snooze_input
            ind["snooze_minutes"] = parsed_snooze_mins
            risk["snooze_period_str"] = snooze_input
            risk["snooze_minutes"] = parsed_snooze_mins

            if parsed_snooze_mins > 0:
                snooze_col2.info(f"⏳ **Cooldown Active:** New trade entries will be prevented for **{parsed_snooze_mins} minutes** after any trade exit.")
            else:
                snooze_col2.info("ℹ️ **Cooldown Inactive:** No delay between trade exit and subsequent entry.")

        with tab_risk:
            st.subheader("🛡️ Risk Parameters (from RiskConfig)")
            st.caption("Customize account capital, daily limits, position sizing, and intraday market hours.")

            rk1, rk2, rk3 = st.columns(3)
            risk["capital"] = rk1.number_input("Starting Capital (₹)", min_value=1000.0, max_value=10_000_000.0, value=float(risk["capital"]), step=10000.0)
            risk["risk_per_trade_pct"] = rk2.number_input("Risk Per Trade (Fraction, 0.01 = 1%)", min_value=0.001, max_value=0.05, value=float(risk["risk_per_trade_pct"]), step=0.0025, format="%.4f")
            risk["max_daily_loss"] = rk3.number_input("Max Daily Loss (₹)", min_value=100.0, max_value=500_000.0, value=float(risk["max_daily_loss"]), step=500.0)

            rk4, rk5, rk6 = st.columns(3)
            risk["max_open_positions"] = rk4.number_input("Max Open Positions", min_value=1, max_value=10, value=int(risk["max_open_positions"]))
            risk["max_exposure_pct"] = rk5.number_input("Max Exposure Ratio (1.0 = 100%)", min_value=0.1, max_value=5.0, value=float(risk["max_exposure_pct"]), step=0.1)
            risk["max_quantity"] = rk6.number_input("Max Order Quantity (Shares)", min_value=1, max_value=50_000, value=int(risk["max_quantity"]), step=500)

            rk7, rk8, rk9 = st.columns(3)
            risk["max_consecutive_losses"] = rk7.number_input("Max Consecutive Losses", min_value=1, max_value=10, value=int(risk["max_consecutive_losses"]))
            risk["lot_size"] = rk8.number_input("Lot Size", min_value=1, max_value=1000, value=int(risk["lot_size"]))
            risk["tick_size"] = rk9.number_input("Tick Size (₹)", min_value=0.01, max_value=1.0, value=float(risk["tick_size"]), step=0.05)

            st.markdown("##### ATR Stop-Loss & Target Sizing (from RiskConfig)")
            st.info(f"🎯 **Fixed Levels Active:** Fixed Target = **{ind.get('fixed_target_pct', 3)}%** | Fixed Stoploss = **{ind.get('fixed_stoploss_pct', 2)}%** (configured under Technical Indicators tab)")
            atr_r1, atr_r2 = st.columns(2)
            risk["atr_stop_multiplier"] = atr_r1.number_input(
                "ATR Stop Multiplier (Stop distance = multiplier × ATR)",
                min_value=0.5, max_value=5.0, value=float(risk["atr_stop_multiplier"]), step=0.1, key="rk_atr_stop_multiplier"
            )
            risk["reward_risk_ratio"] = atr_r2.number_input(
                "Reward-to-Risk Ratio (Target distance = ratio × stop distance)",
                min_value=0.5, max_value=5.0, value=float(risk["reward_risk_ratio"]), step=0.1, key="rk_reward_risk_ratio"
            )

            st.markdown("##### Trading Hours (IST)")
            h1, h2, h3 = st.columns(3)
            risk["entry_start"] = h1.time_input("Entry Start Time", value=risk["entry_start"])
            risk["entry_end"] = h2.time_input("Entry End Time", value=risk["entry_end"])
            risk["square_off_time"] = h3.time_input("Square-off Time", value=risk["square_off_time"])

        with tab_costs:
            st.subheader("💰 Cost & Slippage Configuration")
            c_col1, c_col2 = st.columns(2)

            with c_col1:
                cost["cost_mode"] = st.selectbox(
                    "Cost Model", ["FLAT_PER_ORDER", "PERCENT_MODEL"],
                    index=0 if cost["cost_mode"] == "FLAT_PER_ORDER" else 1,
                    help="FLAT_PER_ORDER charges a fixed fee per order. PERCENT_MODEL applies brokerage, STT, turnover, SEBI, GST, stamp duty."
                )
                if cost["cost_mode"] == "FLAT_PER_ORDER":
                    cost["flat_cost_per_order"] = st.number_input("Flat Brokerage Per Order (₹)", min_value=0.0, max_value=500.0, value=float(cost["flat_cost_per_order"]), step=5.0)
                    st.warning("⚠️ **Notice:** Flat cost ignores STT, GST, exchange and SEBI charges; real costs are higher. Verify against Upstox's current brokerage schedule.")
                else:
                    st.info("Full percentage model active: charges STT, Exchange turnover, SEBI, GST, and Stamp Duty on order legs.")

            with c_col2:
                cost["slippage_mode"] = st.selectbox(
                    "Slippage Model", ["FLAT_PER_ORDER", "PERCENT"],
                    index=0 if cost["slippage_mode"] == "FLAT_PER_ORDER" else 1,
                    help="FLAT_PER_ORDER applies a flat adverse rupees impact per order spread across quantity. PERCENT applies a percentage factor."
                )
                if cost["slippage_mode"] == "FLAT_PER_ORDER":
                    cost["flat_slippage_per_order"] = st.number_input("Flat Adverse Slippage Per Order (₹)", min_value=0.0, max_value=500.0, value=float(cost["flat_slippage_per_order"]), step=5.0)
                    st.caption("Adverse impact = flat_slippage_per_order / quantity per share.")
                else:
                    cost["slippage_pct"] = st.number_input("Slippage Percent (0.0005 = 0.05%)", min_value=0.0, max_value=0.05, value=float(cost["slippage_pct"]), format="%.5f")

        # ==============================================================================
        # LAST SECTION OF STEP 1: Instrument Configuration & Environment
        # ==============================================================================
        st.markdown("---")
        st.markdown("#### 🏢 Instrument Configuration")

        seg_choice = st.radio(
            "Market Segment",
            ["🎯 NSE F&O (Options & Futures) [Kotak Live Priority]", "🏢 NSE Cash Equity"],
            index=0 if "F&O" in st.session_state.get("market_segment", "F&O") else 1,
            horizontal=True,
            key="backtest_market_segment_radio",
            help="NSE F&O dynamically queries live rolling contracts, weekly/monthly expiries, and strikes directly from Kotak Neo endpoints."
        )
        st.session_state.market_segment = seg_choice
        is_fo = "F&O" in seg_choice
        is_valid_isin = True

        if is_fo:
            fo_mode = st.radio(
                "F&O Configuration Mode",
                ["Normal Mode", "OIPulse Mode"],
                index=0 if st.session_state.get("backtest_fo_mode", "Normal Mode") == "Normal Mode" else 1,
                horizontal=True,
                key="backtest_fo_mode",
                help="Normal Mode uses static rolling contract selection. OIPulse Mode dynamically derives Expiry, Option Type (CE/PE), and Strike Price based on oi_strength readings."
            )

            if fo_mode == "OIPulse Mode":
                underlying_options = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
                curr_und = st.session_state.get("backtest_underlying_choice", "NIFTY")
                idx = underlying_options.index(curr_und) if curr_und in underlying_options else 0
                underlying_choice = st.selectbox(
                    "Underlying Index / Stock",
                    underlying_options,
                    index=idx,
                    key="backtest_underlying_choice",
                    help="In OIPulse Mode, select the Underlying Index. Expiry, Option Type, and Strike Price are determined dynamically based on oi_strength."
                )
                mkt_lot = int(INDEX_LOT_SIZES.get(underlying_choice, 65))
                st.session_state.symbol = underlying_choice
                st.session_state.isin = "INF204KB14I2" if underlying_choice == "NIFTY" else f"NSE_INDEX|{underlying_choice}"
                st.session_state.instrument_key = f"NSE_INDEX|{underlying_choice}"
                st.session_state.equity_name = f"{underlying_choice} (OIPulse Mode)"
                st.session_state.risk["lot_size"] = mkt_lot

                st.caption(f"🎯 **Active Selection:** `{underlying_choice}` (Dynamic Expiry, Strike & Option Type via `oi_strength`)")

            else:
                col_und, col_exp = st.columns(2)
                with col_und:
                    underlying_choice = st.selectbox(
                        "Underlying Index / Stock",
                        ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
                        index=0,
                        key="backtest_underlying_choice"
                    )
                with col_exp:
                    live_expiries = fetch_kotak_expiries(underlying_choice)
                    sel_expiry = st.selectbox(
                        "Rolling Expiry Date (Kotak Live)",
                        live_expiries,
                        index=0,
                        help="Live weekly and monthly rolling expiries from Kotak Neo.",
                        key=f"backtest_sel_expiry_{underlying_choice}"
                    )

                col_typ, col_strk = st.columns(2)
                with col_typ:
                    opt_type_choice = st.radio("Option Type", ["PE (Put)", "CE (Call)"], horizontal=True, key="backtest_opt_type_choice")
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

                    strike_key = f"backtest_chosen_strike_{underlying_choice}"
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
                            target_sym = f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}"
                            if hasattr(gw, "smart_search_instruments"):
                                scrips = gw.smart_search_instruments(target_sym)
                                for sc in scrips:
                                    p_tok = str(sc.get("pSymbol") or "")
                                    if p_tok:
                                        selected_inst = {
                                            "symbol": sc.get("tradingsymbol", target_sym),
                                            "neoSymbol": f"nse_fo|{p_tok}",
                                            "optType": clean_opt_type,
                                            "strkPrc": chosen_strike,
                                        }
                                        break
                            if not selected_inst and hasattr(gw, "search_scrip"):
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
                        st.session_state.symbol = selected_inst.get("symbol", f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}")
                        st.session_state.isin = selected_inst.get("neoSymbol", "").split("|")[-1]
                        st.session_state.instrument_key = selected_inst.get("neoSymbol", f"NSE_FO|{chosen_strike}")
                        st.session_state.equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                        st.session_state.risk["lot_size"] = mkt_lot
                    else:
                        target_symbol = f"{underlying_choice}{sym_code}{chosen_strike}{clean_opt_type}"
                        found_std = None
                        for s_inst in NIFTY_STANDARD_INSTRUMENTS:
                            if s_inst["tradingsymbol"] == target_symbol:
                                found_std = s_inst
                                break
                        if found_std:
                            st.session_state.symbol = found_std["tradingsymbol"]
                            st.session_state.isin = found_std["isin"]
                            st.session_state.instrument_key = found_std["instrument_key"]
                            st.session_state.equity_name = found_std["name"]
                            st.session_state.risk["lot_size"] = int(found_std.get("lot_size", mkt_lot))
                        else:
                            st.session_state.symbol = target_symbol
                            st.session_state.isin = f"INF00{chosen_strike}1"
                            st.session_state.instrument_key = f"NSE_FO|{chosen_strike}"
                            st.session_state.equity_name = f"{underlying_choice} {display_dt} {chosen_strike} {clean_opt_type}"
                            st.session_state.risk["lot_size"] = mkt_lot

                st.caption(f"🎯 **Active Selection:** `{st.session_state.symbol}` | Expiry: `{display_dt}` | Key: `{st.session_state.instrument_key}` | Lot: `{st.session_state.risk.get('lot_size', mkt_lot)}`")

        else:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("⚡ **Popular Benchmarks:**")
                q_cols = st.columns(4)
                if q_cols[0].button("📈 NIFTY 50", use_container_width=True, key="backtest_quick_nifty"):
                    st.session_state.symbol = "NIFTY"
                    st.session_state.isin = "INF204KB14I2"
                    st.session_state.equity_name = "Nifty 50 Index (NIP IND ETF NIFTY BEES)"
                    st.rerun()
                if q_cols[1].button("🐝 NIFTYBEES", use_container_width=True, key="backtest_quick_bees"):
                    st.session_state.symbol = "NIFTYBEES"
                    st.session_state.isin = "INF204KB14I2"
                    st.session_state.equity_name = "NIP IND ETF NIFTY BEES"
                    st.rerun()
                if q_cols[2].button("🏦 BANKNIFTY", use_container_width=True, key="backtest_quick_bnifty"):
                    st.session_state.symbol = "BANKNIFTY"
                    st.session_state.isin = "INF174KA1ZB7"
                    st.session_state.equity_name = "Nifty Bank Index (Kotak Bank Nifty ETF)"
                    st.rerun()
                if q_cols[3].button("🏢 RELIANCE", use_container_width=True, key="backtest_quick_rel"):
                    st.session_state.symbol = "RELIANCE"
                    st.session_state.isin = "INE002A01018"
                    st.session_state.equity_name = "Reliance Industries Limited"
                    st.rerun()

                search_query = st.text_input("Search Equity (Symbol or Name)", value=st.session_state.symbol, key="backtest_search_query")
                matched_equities = search_equities(search_query, equities_df, limit=30) if not equities_df.empty else pd.DataFrame()

                options = []
                if not matched_equities.empty:
                    for _, r in matched_equities.iterrows():
                        options.append(f"{r['tradingsymbol']} - {r['name']} ({r['isin']})")
                else:
                    options = [f"{st.session_state.symbol} - {st.session_state.equity_name} ({st.session_state.isin})"]

                curr_opt_idx = 0
                for idx, opt in enumerate(options):
                    if opt.startswith(f"{st.session_state.symbol} -"):
                        curr_opt_idx = idx
                        break

                selected_eq = st.selectbox("Matching Equities", options, index=curr_opt_idx, key="backtest_sel_equity")
                if selected_eq:
                    sel_sym = selected_eq.split(" - ")[0].strip()
                    found = find_equity_by_symbol(sel_sym, equities_df)
                    if found:
                        st.session_state.symbol = found["tradingsymbol"]
                        st.session_state.isin = found["isin"]
                        st.session_state.equity_name = found["name"]
                        st.session_state.instrument_key = found.get("instrument_key", f"NSE_EQ|{found['isin']}")
                        if "lot_size" in found and pd.notna(found["lot_size"]):
                            st.session_state.risk["lot_size"] = int(found["lot_size"])
                        if "tick_size" in found and pd.notna(found["tick_size"]):
                            st.session_state.risk["tick_size"] = float(found["tick_size"])

            with c2:
                input_isin = st.text_input("ISIN (12 alphanumeric chars)", value=st.session_state.isin, key="backtest_input_isin")
                if input_isin != st.session_state.isin:
                    st.session_state.isin = input_isin.strip().upper()
                    found = find_equity_by_isin(st.session_state.isin, equities_df)
                    if found:
                        st.session_state.symbol = found["tradingsymbol"]
                        st.session_state.equity_name = found["name"]
                        st.session_state.instrument_key = found.get("instrument_key", f"NSE_EQ|{found['isin']}")
                        if "lot_size" in found and pd.notna(found["lot_size"]):
                            st.session_state.risk["lot_size"] = int(found["lot_size"])
                        if "tick_size" in found and pd.notna(found["tick_size"]):
                            st.session_state.risk["tick_size"] = float(found["tick_size"])

                is_valid_isin = validate_isin(st.session_state.isin)
                if is_valid_isin:
                    st.success(f"Valid ISIN: {st.session_state.isin} ({st.session_state.symbol} - {st.session_state.equity_name})")
                else:
                    st.error("Invalid ISIN: Must start with 'IN' and pass the 12-char Luhn check-digit test.")

        st.markdown("### Date Range & Environment")
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.session_state.start_date = st.date_input("From Date", value=st.session_state.start_date, key="backtest_start_date")
        with d2:
            st.session_state.end_date = st.date_input("To Date", value=st.session_state.end_date, key="backtest_end_date")
        with d3:
            tf_options = [1, 3, 5, 15, 30, 60]
            curr_tf_idx = tf_options.index(st.session_state.timeframe) if st.session_state.timeframe in tf_options else 2
            st.session_state.timeframe = st.selectbox(
                "Candle Timeframe",
                tf_options,
                index=curr_tf_idx,
                format_func=lambda x: f"{x} Minute" if x == 1 else f"{x} Minutes",
                key="backtest_timeframe_select",
            )
        with d4:
            st.session_state.allow_short = st.toggle("Allow Intraday Shorting", value=st.session_state.allow_short, key="backtest_allow_short")

        # Proactive cache inspection to prevent date mismatch confusion
        sym_clean = str(st.session_state.symbol).lower().replace(" ", "").replace("_", "")
        possible_csvs = [
            f"data/{st.session_state.symbol.lower()}_{st.session_state.timeframe}m.csv",
            f"data/{sym_clean}_{st.session_state.timeframe}m.csv",
            f"data/{st.session_state.symbol.lower()}_1m.csv",
            f"data/{sym_clean}_1m.csv",
            f"data/{st.session_state.symbol.lower()}_5m.csv",
            f"data/{sym_clean}_5m.csv",
        ]
        found_cache = next((p for p in possible_csvs if os.path.exists(p)), None)
        if found_cache:
            try:
                cdf_tail = pd.read_csv(found_cache)
                if "timestamp" in cdf_tail.columns:
                    c_dates_all = pd.to_datetime(cdf_tail["timestamp"]).dt.date
                    min_d = c_dates_all.min()
                    max_d = c_dates_all.max()
                    req_start = st.session_state.start_date
                    req_end = st.session_state.end_date
                    if req_end < min_d or req_start > max_d:
                        st.warning(
                            f"⚠️ **Cache Range Alert**: Local cached file `{os.path.basename(found_cache)}` covers **{min_d}** to **{max_d}**. "
                            f"Your requested date span (**{req_start}** to **{req_end}**) is outside local cache. "
                            f"Live broker data will be queried, or adjust your dates to {min_d} to {max_d} for offline replay.",
                            icon="⚠️",
                        )
                    else:
                        st.caption(f"📁 Local cache available for {st.session_state.symbol}: **{min_d}** to **{max_d}** ({len(cdf_tail):,} candles).")
            except Exception as _e:
                pass

        st.markdown("<br>", unsafe_allow_html=True)
        b_col1, b_col2 = st.columns([1, 2])
        strat_save_name = (st.session_state.strategy_name_input or "").strip() or suggested_name
        can_run_backtest = bool(st.session_state.symbol) if is_fo else is_valid_isin

        if b_col1.button("💾 Save Strategy", use_container_width=True, key="backtest_btn_save_strat"):
            inst_cfg = get_current_instrument_config()
            save_strategy(strat_save_name, st.session_state.indicators, st.session_state.risk, st.session_state.cost, instrument=inst_cfg)
            st.session_state.selected_strategy_name = strat_save_name
            st.session_state.strategy_name_input = strat_save_name
            try:
                st.session_state["step1_load_strat_select"] = strat_save_name
            except Exception:
                pass
            try:
                st.session_state["step1_strat_name_input_field"] = strat_save_name
            except Exception:
                pass
            st.success(f"✅ Strategy '{strat_save_name}' saved with Instrument Configuration to Strategies-List!")

        if b_col2.button("🚀 Save & Proceed to Run Backtest Now", type="primary", use_container_width=True, disabled=not can_run_backtest, key="backtest_btn_save_run"):
            inst_cfg = get_current_instrument_config()
            save_strategy(strat_save_name, st.session_state.indicators, st.session_state.risk, st.session_state.cost, instrument=inst_cfg)
            st.session_state.selected_strategy_name = strat_save_name
            st.session_state.strategy_name_input = strat_save_name
            try:
                st.session_state["step1_load_strat_select"] = strat_save_name
            except Exception:
                pass
            try:
                st.session_state["step1_strat_name_input_field"] = strat_save_name
            except Exception:
                pass
            execute_backtest_simulation()



    # ==============================================================================
    # STEP ROUTING & DISPATCH
    # ==============================================================================
    if st.session_state.step == 1:
        render_step_1_strategy_config(equities_df)
    # ==============================================================================
    # STEP 2: Results Screen (formerly Step 3)
    # ==============================================================================
    elif st.session_state.step == 2:
        res = st.session_state.active_run
        if not res:
            st.warning("No run results loaded.")
            if st.button("Start New Backtest"):
                st.session_state.step = 1
                st.rerun()
            return

        run_data = res.get("run_data", {})
        trades = res.get("trades_df", pd.DataFrame())
        metrics = res.get("metrics", {})
        cfg_json = run_data.get("config", {})
        capital = cfg_json.get("risk", {}).get("capital", 100_000.0)

        tsl_on = cfg_json.get("indicators", {}).get("trailing_sl_enabled") or cfg_json.get("risk", {}).get("trailing_sl_enabled")
        tsl_runner = cfg_json.get("indicators", {}).get("trail_beyond_target") or cfg_json.get("risk", {}).get("trail_beyond_target")
        tsl_info = " | Trailing SL: Model B Active" + (" (Runner)" if tsl_runner else "") if tsl_on else ""
        st.header(f"Results: Run #{run_data.get('id', 'N/A')} ({run_data.get('symbol', 'EQUITY')})")
        st.caption(f"Strategy: `{run_data.get('strategy_version', 'custom')}` | Period: {run_data.get('date_from')} to {run_data.get('date_to')}{tsl_info}")

        if st.session_state.get("data_source_warning"):
            st.warning(st.session_state["data_source_warning"])

        # Disclaimer Note
        st.warning("⚠️ **Notice:** Past results do not predict future results. Changing parameters after seeing results turns this data into in-sample data.")

        # KPI Metrics
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        net_pnl = metrics.get("net_pnl") if metrics.get("net_pnl") is not None else 0.0
        num_trades = metrics.get("num_trades") if metrics.get("num_trades") is not None else len(trades)
        return_pct = metrics.get("return_pct") if metrics.get("return_pct") is not None else 0.0
        gross_pnl = metrics.get("gross_pnl") if metrics.get("gross_pnl") is not None else 0.0
        total_costs = metrics.get("total_costs") if metrics.get("total_costs") is not None else 0.0
        win_rate = metrics.get("win_rate_pct") if metrics.get("win_rate_pct") is not None else 0.0
        pf = metrics.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None else ("∞ (No Losses)" if gross_pnl > 0 else "0.00")

        k1.metric("Trades", num_trades)
        k2.metric("Net P&L (₹)", f"{net_pnl:,.2f}", f"{return_pct:.2f}%")
        k3.metric("Gross P&L (₹)", f"{gross_pnl:,.2f}")
        k4.metric("Total Costs (₹)", f"{total_costs:,.2f}")
        k5.metric("Win Rate", f"{win_rate:.1f}%")
        k6.metric("Profit Factor", pf_str)

        k7, k8, k9, k10, k11, k12 = st.columns(6)
        max_dd = metrics.get("max_drawdown") if metrics.get("max_drawdown") is not None else 0.0
        max_dd_pct = metrics.get("max_drawdown_pct") if metrics.get("max_drawdown_pct") is not None else 0.0
        tot_slip = metrics.get("total_slippage") if metrics.get("total_slippage") is not None else 0.0
        avg_tr = metrics.get("avg_trade") if metrics.get("avg_trade") is not None else 0.0
        rr = metrics.get("risk_reward")
        rr_str = f"{rr:.2f}" if rr is not None else "N/A"
        loss_streak = metrics.get("max_consecutive_losses") if metrics.get("max_consecutive_losses") is not None else 0
        sh = metrics.get("sharpe")
        sh_str = f"{sh:.2f}" if sh is not None else "N/A"

        k7.metric("Max Drawdown", f"₹{max_dd:,.0f}", f"-{max_dd_pct:.1f}%")
        k8.metric("Total Slippage (₹)", f"{tot_slip:,.2f}")
        k9.metric("Avg Trade (₹)", f"{avg_tr:,.2f}")
        k10.metric("Reward/Risk", rr_str)
        k11.metric("Max Loss Streak", loss_streak)
        k12.metric("Sharpe Ratio", sh_str)

        # Calculated_Score Profile Metrics
        ind_cfg = cfg_json.get("indicators", {})
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
        if ind_cfg.get("hma_enabled", False): max_score_cfg += ind_cfg.get("hma_weight", 1)
        if ind_cfg.get("supertrend_enabled", False): max_score_cfg += ind_cfg.get("supertrend_weight", 1)
        max_score_cfg = max(max_score_cfg, 1)

        long_th = ind_cfg.get("long_threshold", 5)
        short_th = ind_cfg.get("short_threshold", -5)
        avg_score = trades["score"].mean() if not trades.empty and "score" in trades.columns else 0.0

        st.markdown("##### ⚡ Strategy Calculated_Score Profile")
        cs1, cs2, cs3, cs4 = st.columns(4)
        cs1.metric("Calculated_Score (Max)", f"{max_score_cfg} pts", help="Maximum possible consensus score from active indicators.")
        cs2.metric("LONG Threshold", f"≥ {long_th} pts", help="Score threshold that triggered LONG entries.")
        cs3.metric("SHORT Threshold", f"≤ {short_th} pts", help="Score threshold that triggered SHORT entries.")
        cs4.metric("Avg Trade Score", f"{avg_score:+.2f} pts" if not trades.empty else "N/A", help="Average Calculated_Score at entry across executed trades.")

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

        active_inds = []
        if ind_cfg.get("ema_enabled"): active_inds.append(f"EMA ({ind_cfg.get('ema_fast')}/{ind_cfg.get('ema_slow')})")
        if ind_cfg.get("rsi_enabled"): active_inds.append(f"RSI ({ind_cfg.get('rsi_period')})")
        if ind_cfg.get("macd_enabled"): active_inds.append("MACD")
        if ind_cfg.get("vwap_enabled"): active_inds.append("VWAP")
        if ind_cfg.get("adx_enabled"): active_inds.append(f"ADX ({ind_cfg.get('adx_period')})")
        if ind_cfg.get("volume_enabled"): active_inds.append("Volume")
        if ind_cfg.get("bollinger_enabled"): active_inds.append("Bollinger Bands")
        if ind_cfg.get("stoch_enabled"): active_inds.append("Stochastic")
        if ind_cfg.get("obv_enabled"): active_inds.append("OBV")
        if ind_cfg.get("higher_high_enabled"): active_inds.append("Higher High")
        if ind_cfg.get("lower_low_enabled"): active_inds.append("Lower Low")
        if ind_cfg.get("hma_enabled"): active_inds.append(f"HMA ({ind_cfg.get('hma_period', 100)})")
        if ind_cfg.get("supertrend_enabled"): active_inds.append(f"Supertrend ({ind_cfg.get('supertrend_period', 10)}, {ind_cfg.get('supertrend_mult_simple', 2.0)}/{ind_cfg.get('supertrend_mult_complex', 4.0)})")
        st.caption(f"🧩 **Active Indicators ({len(active_inds)}):** {', '.join(active_inds) if active_inds else 'None'}")

        # Benchmark Buy-and-Hold
        if "indicators_df" in res and res["indicators_df"] is not None and not res["indicators_df"].empty:
            c_series = res["indicators_df"]["close"]
            b_and_h = (c_series.iloc[-1] / c_series.iloc[0] - 1) * 100
            st.caption(f"Benchmark Buy-and-Hold for same period: **{b_and_h:.2f}%**")

        # In-Sample vs Out-of-Sample Split Comparison
        in_s = res.get("in_sample")
        out_s = res.get("out_sample")
        if in_s and out_s:
            st.markdown("### 🔬 In-Sample vs Out-of-Sample Validation")
            s_col1, s_col2 = st.columns(2)
            with s_col1:
                st.markdown(f"**In-Sample (Training - {in_s['days']} days)**")
                m_in = in_s["metrics"]
                pf_in = m_in.get("profit_factor")
                pf_in_str = f"{pf_in:.2f}" if pf_in is not None else ("∞" if (m_in.get("gross_pnl") or 0.0) > 0 else "0.00")
                st.write(f"Trades: **{m_in.get('num_trades', 0)}** | Net P&L: **₹{(m_in.get('net_pnl') or 0.0):,.2f}** ({(m_in.get('return_pct') or 0.0):.2f}%)")
                st.write(f"Win Rate: **{(m_in.get('win_rate_pct') or 0.0):.1f}%** | Profit Factor: **{pf_in_str}** | Max DD: **₹{(m_in.get('max_drawdown') or 0.0):,.0f}**")
            with s_col2:
                st.markdown(f"**Out-of-Sample (Testing - {out_s['days']} days)**")
                m_out = out_s["metrics"]
                pf_out = m_out.get("profit_factor")
                pf_out_str = f"{pf_out:.2f}" if pf_out is not None else ("∞" if (m_out.get("gross_pnl") or 0.0) > 0 else "0.00")
                st.write(f"Trades: **{m_out.get('num_trades', 0)}** | Net P&L: **₹{(m_out.get('net_pnl') or 0.0):,.2f}** ({(m_out.get('return_pct') or 0.0):.2f}%)")
                st.write(f"Win Rate: **{(m_out.get('win_rate_pct') or 0.0):.1f}%** | Profit Factor: **{pf_out_str}** | Max DD: **₹{(m_out.get('max_drawdown') or 0.0):,.0f}**")

        # Charts
        if not trades.empty:
            ch1, ch2, ch3 = st.columns(3)
            with ch1:
                st.subheader("Equity Curve")
                eq = pd.concat([pd.Series([capital]), capital + trades["net_pnl"].cumsum()], ignore_index=True)
                st.line_chart(eq.rename("Equity (₹)"))

            with ch2:
                st.subheader("Daily Net P&L")
                if "exit_time" in trades.columns:
                    trades["day"] = pd.to_datetime(trades["exit_time"]).dt.date
                    daily_pnl = trades.groupby("day")["net_pnl"].sum()
                    st.bar_chart(daily_pnl)

            with ch3:
                st.subheader("Monthly Net P&L")
                time_col = "exit_time" if "exit_time" in trades.columns else ("entry_time" if "entry_time" in trades.columns else None)
                if time_col is not None:
                    dt = pd.to_datetime(trades[time_col].astype(str).str.slice(0, 10), errors="coerce")
                    m_df = trades.assign(
                        month_key=dt.dt.strftime("%Y-%m"),
                        month_label=dt.dt.strftime("%b %Y")
                    ).dropna(subset=["month_key"])
                    if not m_df.empty:
                        monthly_pnl = m_df.groupby(["month_key", "month_label"])["net_pnl"].sum().reset_index()
                        monthly_pnl = monthly_pnl.sort_values("month_key").set_index("month_label")["net_pnl"]
                        st.bar_chart(monthly_pnl)

            # Populate Instrument, Index-LTP, and Option Type columns prior to breakdowns
            if "score" in trades.columns and "Calculated_Score" not in trades.columns:
                trades["Calculated_Score"] = trades["score"]
            if "quantity" in trades.columns and "entry_price" in trades.columns:
                trades["Margin used"] = (trades["quantity"] * trades["entry_price"]).round(2)
                trades["margin_used"] = trades["Margin used"]

                import sqlite3
                db_oi_file = "data/backtest.db"
                oi_conn = None
                if os.path.exists(db_oi_file):
                    try:
                        oi_conn = sqlite3.connect(db_oi_file)
                    except Exception:
                        oi_conn = None
                oi_cur = oi_conn.cursor() if oi_conn else None

                # Pre-map candles for fallback index lookup if needed
                c_ts_map = {}
                raw_candles_ref = res.get("raw_df")
                if raw_candles_ref is None or raw_candles_ref.empty:
                    raw_candles_ref = res.get("indicators_df")
                if raw_candles_ref is not None and not raw_candles_ref.empty and "timestamp" in raw_candles_ref.columns:
                    for _, c_row in raw_candles_ref.iterrows():
                        ts_key = str(c_row["timestamp"])[:19]
                        val = c_row.get("ltp", c_row.get("close"))
                        if val is not None and pd.notna(val):
                            c_ts_map[ts_key] = float(val)

                def get_trade_instrument_and_opt_type(row):
                    import re
                    # 1. Resolve active constructed instrument name
                    sym = str(row.get("symbol") or "").strip()
                    if not sym or sym.lower() in ("none", "nan", ""):
                        sym = str(run_data.get("symbol") or st.session_state.get("symbol") or "").strip()
                    
                    cfg_sym = str(run_data.get("config", {}).get("symbol") or "").strip()
                    cfg_eq = str(run_data.get("config", {}).get("equity_name") or st.session_state.get("equity_name") or "").strip()
                    
                    # If sym is just index name, upgrade to configured symbol / equity name if available
                    if sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                        if cfg_sym and cfg_sym not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") and re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", cfg_sym.upper()):
                            sym = cfg_sym
                        elif cfg_eq and re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", cfg_eq.upper()) and "OIPulse" not in cfg_eq:
                            sym = cfg_eq

                    # 2. Resolve Index-LTP from oi_strength table
                    idx_ltp = np.nan
                    if "index_ltp" in row and pd.notna(row["index_ltp"]):
                        try:
                            idx_ltp = round(float(row["index_ltp"]), 2)
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
                    if "opt_type" in row and pd.notna(row["opt_type"]) and str(row["opt_type"]).strip() and str(row["opt_type"]).strip().lower() != "none":
                        opt_t = str(row["opt_type"]).strip().upper()
                    elif "option_type" in row and pd.notna(row["option_type"]) and str(row["option_type"]).strip() and str(row["option_type"]).strip().lower() != "none":
                        opt_t = str(row["option_type"]).strip().upper()

                    if not opt_t:
                        sym_upper = sym.upper()
                        m_sym = re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", sym_upper)
                        if m_sym:
                            opt_t = m_sym.group(1)
                        elif sym in (run_data.get("symbol", ""), "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                            cfg_opt = str(run_data.get("config", {}).get("option_type") or st.session_state.get("backtest_opt_type_choice") or "").upper()
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

            # Breakdowns
            b1, b2, b3 = st.columns(3)
            with b1:
                st.subheader("By Exit Reason")
                if not trades.empty and "exit_reason" in trades.columns and trades["exit_reason"].dropna().nunique() > 0:
                    by_exit = trades.groupby("exit_reason")["net_pnl"].agg(
                        Trades="count", Net_PnL="sum", Avg_Trade="mean"
                    ).round(2)
                    st.dataframe(by_exit, width="stretch")
                else:
                    empty_exit = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                    empty_exit.index.name = "exit_reason"
                    st.dataframe(empty_exit, width="stretch")

            with b2:
                st.subheader("Option Type PE: By Exit Reason")
                pe_trades = trades[trades["Option Type"].astype(str).str.upper() == "PE"] if "Option Type" in trades.columns else pd.DataFrame()
                if not pe_trades.empty and "exit_reason" in pe_trades.columns and pe_trades["exit_reason"].dropna().nunique() > 0:
                    by_exit_pe = pe_trades.groupby("exit_reason")["net_pnl"].agg(
                        Trades="count", Net_PnL="sum", Avg_Trade="mean"
                    ).round(2)
                    st.dataframe(by_exit_pe, width="stretch")
                else:
                    empty_pe = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                    empty_pe.index.name = "exit_reason"
                    st.dataframe(empty_pe, width="stretch")

            with b3:
                st.subheader("Option Type CE: By Exit Reason")
                ce_trades = trades[trades["Option Type"].astype(str).str.upper() == "CE"] if "Option Type" in trades.columns else pd.DataFrame()
                if not ce_trades.empty and "exit_reason" in ce_trades.columns and ce_trades["exit_reason"].dropna().nunique() > 0:
                    by_exit_ce = ce_trades.groupby("exit_reason")["net_pnl"].agg(
                        Trades="count", Net_PnL="sum", Avg_Trade="mean"
                    ).round(2)
                    st.dataframe(by_exit_ce, width="stretch")
                else:
                    empty_ce = pd.DataFrame(columns=["Trades", "Net_PnL", "Avg_Trade"])
                    empty_ce.index.name = "exit_reason"
                    st.dataframe(empty_ce, width="stretch")

            # Trades Table
            st.subheader("Trade Audit Trail (All Executions)")
            if not trades.empty:

                # Calculate Stop_Risk per trade: crossed percentage slab (5%, 10%, 15%, 20%, 25%...) reached before exit / Stop
                raw_candles_for_trades = res.get("raw_df")
                if raw_candles_for_trades is None or raw_candles_for_trades.empty:
                    raw_candles_for_trades = res.get("indicators_df")

                def calc_stop_risk(row):
                    side = row.get("side", "LONG")
                    entry_p = float(row.get("entry_price", 0.0))
                    if entry_p <= 0:
                        return "0% (+0.0%)"

                    peak_pct = 0.0
                    # 1. Use engine-tracked peak_gain_pct if present and > 0
                    if "peak_gain_pct" in row and pd.notna(row["peak_gain_pct"]) and float(row["peak_gain_pct"]) > 0:
                        peak_pct = float(row["peak_gain_pct"])
                    # 2. Otherwise calculate from historical candle high/low between entry and exit
                    elif raw_candles_for_trades is not None and not raw_candles_for_trades.empty:
                        try:
                            e_ts = pd.to_datetime(row.get("entry_time"))
                            x_ts = pd.to_datetime(row.get("exit_time"))
                            c_ts = pd.to_datetime(raw_candles_for_trades["timestamp"])
                            mask = (c_ts >= e_ts) & (c_ts <= x_ts)
                            c_slice = raw_candles_for_trades.loc[mask]
                            if not c_slice.empty:
                                if side == "LONG":
                                    max_h = float(c_slice["high"].max())
                                    peak_pct = max(0.0, (max_h - entry_p) / entry_p * 100.0)
                                else:
                                    min_l = float(c_slice["low"].min())
                                    peak_pct = max(0.0, (entry_p - min_l) / entry_p * 100.0)
                        except Exception:
                            pass

                    # Percentage slab crossed: 5%, 10%, 15%, 20%, 25%, etc.
                    if peak_pct >= 5.0:
                        slab = int(peak_pct // 5) * 5
                        return f"{slab}% (+{peak_pct:.1f}%)"
                    else:
                        return f"0% (+{peak_pct:.1f}%)"

                trades["peak_gain"] = trades.apply(calc_stop_risk, axis=1)
                trades["Stop_Risk"] = trades["peak_gain"]

                # ---------------- Milestone Slab Confluence Scores (5%, 10%, 15%, 20%, 25%, 30%) ----------------
                # Calculates the confluence score on the first candle when the trade touched each milestone slab
                ind_df_candles = res.get("indicators_df")
                if ind_df_candles is None or ind_df_candles.empty:
                    ind_df_candles = res.get("raw_df")

                slabs = [5, 10, 15, 20, 25, 30]
                slab_cols = [f"{s}%" for s in slabs]

                cfg_for_scoring = res.get("custom_cfg")
                if cfg_for_scoring is None:
                    try:
                        cfg_d = run_data.get("config", {})
                        ind_d = cfg_d.get("indicators") or st.session_state.get("indicators", {})
                        risk_d = cfg_d.get("risk") or st.session_state.get("risk", {})
                        cfg_for_scoring = CustomStrategyConfig(
                            name=run_data.get("strategy_version", "custom"),
                            ema_enabled=ind_d.get("ema_enabled", True), ema_fast=ind_d.get("ema_fast", 20), ema_slow=ind_d.get("ema_slow", 50), ema_weight=ind_d.get("ema_weight", 2),
                            rsi_enabled=ind_d.get("rsi_enabled", True), rsi_period=ind_d.get("rsi_period", 14), rsi_bull_low=ind_d.get("rsi_bull_low", 55.0),
                            rsi_bull_high=ind_d.get("rsi_bull_high", 70.0), rsi_bear_low=ind_d.get("rsi_bear_low", 30.0), rsi_bear_high=ind_d.get("rsi_bear_high", 45.0), rsi_weight=ind_d.get("rsi_weight", 1),
                            macd_enabled=ind_d.get("macd_enabled", True), macd_fast=ind_d.get("macd_fast", 12), macd_slow=ind_d.get("macd_slow", 26), macd_signal=ind_d.get("macd_signal", 9), macd_weight=ind_d.get("macd_weight", 2),
                            vwap_enabled=ind_d.get("vwap_enabled", True), vwap_weight=ind_d.get("vwap_weight", 1),
                            atr_enabled=ind_d.get("atr_enabled", True), atr_period=ind_d.get("atr_period", 14), atr_stop_multiplier=risk_d.get("atr_stop_multiplier", 1.5), atr_reward_risk_ratio=risk_d.get("reward_risk_ratio", 2.0),
                            adx_enabled=ind_d.get("adx_enabled", True), adx_period=ind_d.get("adx_period", 14), adx_min=ind_d.get("adx_min", 20.0), adx_weight=ind_d.get("adx_weight", 1),
                            volume_enabled=ind_d.get("volume_enabled", True), volume_period=ind_d.get("volume_period", 20), volume_ratio_min=ind_d.get("volume_ratio_min", 1.0),
                            volume_consecutive=int(ind_d.get("volume_consecutive", 3)), volume_weight=ind_d.get("volume_weight", 1),
                            bollinger_enabled=ind_d.get("bollinger_enabled", False), bollinger_period=ind_d.get("bollinger_period", 20), bollinger_std_dev=ind_d.get("bollinger_std_dev", 2.0), bollinger_weight=ind_d.get("bollinger_weight", 1),
                            stoch_enabled=ind_d.get("stoch_enabled", False), stoch_k=ind_d.get("stoch_k", 14), stoch_d=ind_d.get("stoch_d", 3), stoch_low=ind_d.get("stoch_low", 20.0), stoch_high=ind_d.get("stoch_high", 80.0), stoch_weight=ind_d.get("stoch_weight", 1),
                            obv_enabled=ind_d.get("obv_enabled", False), obv_ma_period=ind_d.get("obv_ma_period", 20), obv_weight=ind_d.get("obv_weight", 1),
                            higher_high_enabled=bool(ind_d.get("higher_high_enabled", False)), higher_high_period=int(ind_d.get("higher_high_period", 5)), higher_high_weight=int(ind_d.get("higher_high_weight", 1)),
                            lower_low_enabled=bool(ind_d.get("lower_low_enabled", False)), lower_low_period=int(ind_d.get("lower_low_period", 5)), lower_low_weight=int(ind_d.get("lower_low_weight", 1)),
                            hma_enabled=bool(ind_d.get("hma_enabled", False)), hma_period=int(ind_d.get("hma_period", 100)), hma_weight=int(ind_d.get("hma_weight", 1)),
                            supertrend_enabled=bool(ind_d.get("supertrend_enabled", False)), supertrend_period=int(ind_d.get("supertrend_period", 10)),
                            supertrend_mult_simple=float(ind_d.get("supertrend_mult_simple", 2.0)), supertrend_mult_complex=float(ind_d.get("supertrend_mult_complex", 4.0)),
                            supertrend_weight=int(ind_d.get("supertrend_weight", 1)),
                            long_threshold=ind_d.get("long_threshold", 4), short_threshold=ind_d.get("short_threshold", -4),
                        )
                    except Exception:
                        pass

                c_ts_series = None
                if ind_df_candles is not None and not ind_df_candles.empty:
                    try:
                        c_ts_series = pd.to_datetime(ind_df_candles["timestamp"])
                    except Exception:
                        c_ts_series = None

                for col_name in slab_cols:
                    trades[col_name] = "-"

                for idx, r_tr in trades.iterrows():
                    s_side = r_tr.get("side", "LONG")
                    e_p = float(r_tr.get("entry_price", 0.0))
                    if e_p <= 0 or c_ts_series is None:
                        continue
                    try:
                        tr_e_ts = pd.to_datetime(r_tr.get("entry_time"))
                        tr_x_ts = pd.to_datetime(r_tr.get("exit_time"))
                        s_mask = (c_ts_series >= tr_e_ts) & (c_ts_series <= tr_x_ts)
                        c_sub = ind_df_candles.loc[s_mask]
                        if c_sub.empty:
                            continue

                        for s_val, col_name in zip(slabs, slab_cols):
                            if s_side == "LONG":
                                tgt_p = e_p * (1.0 + s_val / 100.0)
                                r_cand = c_sub[c_sub["high"] >= tgt_p]
                            else:
                                tgt_p = e_p * (1.0 - s_val / 100.0)
                                r_cand = c_sub[c_sub["low"] <= tgt_p]

                            if not r_cand.empty:
                                f_cand = r_cand.iloc[0]
                                sc_val = f_cand.get("score")
                                if sc_val is None or pd.isna(sc_val):
                                    if cfg_for_scoring is not None:
                                        sc_val = sum(score_custom_row(f_cand, cfg_for_scoring).values())
                                    else:
                                        sc_val = r_tr.get("score", 0)
                                sc_int = int(sc_val)
                                trades.at[idx, col_name] = f"+{sc_int}" if sc_int > 0 else str(sc_int)
                    except Exception:
                        pass

            cols = [c for c in [
                "id", "Instrument", "Index-LTP", "Option Type", "side", "regime", "Calculated_Score", "entry_time", "exit_time",
                "quantity", "entry_price", "Margin used", "peak_gain",
                "5%", "10%", "15%", "20%", "25%", "30%",
                "exit_price", "stop_loss", "target",
                "exit_reason", "gross_pnl", "costs", "slippage_cost", "net_pnl"
            ] if c in trades.columns]
            col_t1, col_t2 = st.columns([3, 1])
            with col_t1:
                show_n = st.number_input(
                    "Rows to show (newest first)",
                    min_value=min(5, max(1, len(trades))),
                    max_value=max(len(trades), 5),
                    value=max(len(trades), min(5, max(1, len(trades)))),
                    step=5,
                )
            with col_t2:
                st.write("")
                st.download_button(
                    label="📥 Export Trail CSV",
                    data=trades[cols].to_csv(index=False).encode("utf-8"),
                    file_name="trade_audit_trail_executions.csv",
                    mime="text/csv",
                    key="dl_trade_audit_trail",
                )
            st.dataframe(
                trades[cols].tail(int(show_n)).iloc[::-1],
                width="stretch",
                hide_index=True,
                column_config={
                    "Instrument": st.column_config.TextColumn(
                        "Instrument",
                        help="Active constructed instrument / contract symbol (option chain name)",
                    ),
                    "Index-LTP": st.column_config.NumberColumn(
                        "Index-LTP",
                        help="Underlying Index LTP from oi_strength table at trade entry (used to validate constructed option strike)",
                        format="%.2f",
                    ),
                    "Option Type": st.column_config.TextColumn(
                        "Option Type",
                        help="Option contract type: PE (Put) or CE (Call)",
                    ),
                    "Margin used": st.column_config.NumberColumn(
                        "Margin used",
                        help="Margin used per trade = Quantity × Entry Price",
                        format="₹%.2f",
                    ),
                    "peak_gain": st.column_config.TextColumn(
                        "peak_gain",
                        help="Crossed profit slab (5%, 10%, 15%, 20%, 25%...) reached before reversing to exit / Stop.",
                    ),
                    "5%": st.column_config.TextColumn("5%", help="Milestone Confluence Score on the candle that first reached +5% gain"),
                    "10%": st.column_config.TextColumn("10%", help="Milestone Confluence Score on the candle that first reached +10% gain"),
                    "15%": st.column_config.TextColumn("15%", help="Milestone Confluence Score on the candle that first reached +15% gain"),
                    "20%": st.column_config.TextColumn("20%", help="Milestone Confluence Score on the candle that first reached +20% gain"),
                    "25%": st.column_config.TextColumn("25%", help="Milestone Confluence Score on the candle that first reached +25% gain"),
                    "30%": st.column_config.TextColumn("30%", help="Milestone Confluence Score on the candle that first reached +30% gain"),
                    "entry_price": st.column_config.NumberColumn("entry_price", format="%.2f"),
                    "exit_price": st.column_config.NumberColumn("exit_price", format="%.2f"),
                    "net_pnl": st.column_config.NumberColumn("net_pnl", format="₹%.2f"),
                },
            )

            # ---------------- Trade Audit Trail-2 (Candle Progression: C1 to C360) ----------------
            st.markdown("---")
            st.subheader("Trade Audit Trail-2 (Candle Progression: C1 to C360)")
            st.caption(
                "Candle-by-candle multi-factor confluence score and percentage gain from entry until exit. "
                "Format: Score(Gain%) e.g. +5(+10.0%), +4(+1.3%). "
                "Positive values (+1 to +8) indicate bullish momentum, negative values (-1 to -8) indicate bearish momentum. "
                "Unreached / post-exit candles are marked '-'."
            )

            c_cols = [f"C{i}" for i in range(1, 361)]
            c_data = {col: ["-"] * len(trades) for col in c_cols}

            if c_ts_series is not None and not ind_df_candles.empty:
                for row_idx, (_, r_tr) in enumerate(trades.iterrows()):
                    try:
                        tr_e_ts = pd.to_datetime(r_tr.get("entry_time"))
                        tr_x_ts = pd.to_datetime(r_tr.get("exit_time"))
                        if pd.isna(tr_e_ts) or pd.isna(tr_x_ts):
                            continue

                        # Handle timezone compatibility
                        if c_ts_series.dt.tz is not None:
                            if tr_e_ts.tzinfo is None:
                                tr_e_ts = tr_e_ts.tz_localize(c_ts_series.dt.tz)
                            else:
                                tr_e_ts = tr_e_ts.tz_convert(c_ts_series.dt.tz)
                            if tr_x_ts.tzinfo is None:
                                tr_x_ts = tr_x_ts.tz_localize(c_ts_series.dt.tz)
                            else:
                                tr_x_ts = tr_x_ts.tz_convert(c_ts_series.dt.tz)
                        else:
                            if tr_e_ts.tzinfo is not None:
                                tr_e_ts = tr_e_ts.tz_localize(None)
                            if tr_x_ts.tzinfo is not None:
                                tr_x_ts = tr_x_ts.tz_localize(None)

                        # Candlesticks from after entry_time up to exit_time
                        s_mask = (c_ts_series > tr_e_ts) & (c_ts_series <= tr_x_ts)
                        c_sub = ind_df_candles.loc[s_mask]
                        if c_sub.empty:
                            s_mask = (c_ts_series >= tr_e_ts) & (c_ts_series <= tr_x_ts)
                            c_sub = ind_df_candles.loc[s_mask]

                        s_side = r_tr.get("side", "LONG")
                        e_p = float(r_tr.get("entry_price", 0.0))
                        num_candles = min(len(c_sub), 360)
                        for k in range(num_candles):
                            cand_row = c_sub.iloc[k]
                            sc_val = cand_row.get("score")
                            if sc_val is None or pd.isna(sc_val):
                                if cfg_for_scoring is not None:
                                    sc_val = sum(score_custom_row(cand_row, cfg_for_scoring).values())
                                else:
                                    sc_val = r_tr.get("score", 0)
                            sc_int = int(sc_val)
                            sc_str = f"+{sc_int}" if sc_int > 0 else str(sc_int)

                            # Calculate current candle percentage gain from entry price
                            c_close = cand_row.get("close")
                            if e_p > 0 and c_close is not None and pd.notna(c_close):
                                c_close = float(c_close)
                                gain_pts = (c_close - e_p) if s_side == "LONG" else (e_p - c_close)
                                gain_pct = (gain_pts / e_p) * 100.0
                                c_data[f"C{k+1}"][row_idx] = f"{sc_str}({gain_pct:+.1f}%)"
                            else:
                                c_data[f"C{k+1}"][row_idx] = sc_str
                    except Exception:
                        pass

            c_df = pd.DataFrame(c_data, index=trades.index)
            base_cols_2 = [c for c in cols if c in trades.columns]
            trades_2 = pd.concat([trades[base_cols_2], c_df], axis=1)

            col_a, col_b = st.columns([3, 1])
            with col_a:
                show_n_2 = st.number_input(
                    "Rows to show (newest first)",
                    min_value=min(5, max(1, len(trades_2))),
                    max_value=max(len(trades_2), 5),
                    value=max(len(trades_2), min(5, max(1, len(trades_2)))),
                    step=5,
                    key="audit_trail_2_show_n",
                )
            with col_b:
                st.write("")
                st.download_button(
                    label="📥 Export Trail-2 CSV",
                    data=trades_2.to_csv(index=False).encode("utf-8"),
                    file_name="trade_audit_trail_2_candle_scores.csv",
                    mime="text/csv",
                    key="dl_trade_audit_trail_2",
                )

            st.dataframe(
                trades_2.tail(int(show_n_2)).iloc[::-1],
                width="stretch",
                hide_index=True,
                column_config={
                    "Instrument": st.column_config.TextColumn(
                        "Instrument",
                        help="Active constructed instrument / contract symbol (option chain name)",
                    ),
                    "Index-LTP": st.column_config.NumberColumn(
                        "Index-LTP",
                        help="Underlying Index LTP from oi_strength table at trade entry (used to validate constructed option strike)",
                        format="%.2f",
                    ),
                    "Option Type": st.column_config.TextColumn(
                        "Option Type",
                        help="Option contract type: PE (Put) or CE (Call)",
                    ),
                    "Margin used": st.column_config.NumberColumn(
                        "Margin used",
                        help="Margin used per trade = Quantity × Entry Price",
                        format="₹%.2f",
                    ),
                    "peak_gain": st.column_config.TextColumn(
                        "peak_gain",
                        help="Crossed profit slab (5%, 10%, 15%, 20%, 25%...) reached before reversing to exit / Stop.",
                    ),
                    "5%": st.column_config.TextColumn("5%", help="Milestone Confluence Score on the candle that first reached +5% gain"),
                    "10%": st.column_config.TextColumn("10%", help="Milestone Confluence Score on the candle that first reached +10% gain"),
                    "15%": st.column_config.TextColumn("15%", help="Milestone Confluence Score on the candle that first reached +15% gain"),
                    "20%": st.column_config.TextColumn("20%", help="Milestone Confluence Score on the candle that first reached +20% gain"),
                    "25%": st.column_config.TextColumn("25%", help="Milestone Confluence Score on the candle that first reached +25% gain"),
                    "30%": st.column_config.TextColumn("30%", help="Milestone Confluence Score on the candle that first reached +30% gain"),
                    "entry_price": st.column_config.NumberColumn("entry_price", format="%.2f"),
                    "exit_price": st.column_config.NumberColumn("exit_price", format="%.2f"),
                    "net_pnl": st.column_config.NumberColumn("net_pnl", format="₹%.2f"),
                },
            )
        else:
            st.info("No trades executed during this period with the current strategy threshold settings.")

        st.markdown("<br>", unsafe_allow_html=True)
        res_b1, res_b2 = st.columns(2)
        run_strat = run_data.get("strategy_name") or run_data.get("strategy_version") or st.session_state.get("selected_strategy_name", "")
        if res_b1.button("⬅️ Run Another Backtest (Step 1)", use_container_width=True):
            if run_strat:
                load_strategy_into_session(run_strat)
                st.session_state.selected_strategy_name = run_strat
                st.session_state.strategy_name_input = run_strat
            st.session_state.step = 1
            st.rerun()
        if res_b2.button("⚙️ Edit Strategy (Step 1)", use_container_width=True):
            if run_strat:
                load_strategy_into_session(run_strat)
                st.session_state.selected_strategy_name = run_strat
                st.session_state.strategy_name_input = run_strat
                try:
                    st.session_state["step1_strat_name_input_field"] = run_strat
                except Exception:
                    pass
                try:
                    st.session_state["step1_load_strat_select"] = run_strat
                except Exception:
                    pass
            st.session_state.step = 1
            st.rerun()

if __name__ == "__main__":
    st.set_page_config(
        page_title="AI Auto-Trading Backtest Studio",
        page_icon="📈",
        layout="wide",
    )
    render_backtest_module()

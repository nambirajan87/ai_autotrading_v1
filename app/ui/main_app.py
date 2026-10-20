import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in sys.path when executed via 'streamlit run'
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# Synchronize Streamlit Cloud secrets to os.environ
try:
    if hasattr(st, "secrets"):
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float, bool)):
                os.environ[str(k)] = str(v)
                os.environ[str(k).upper()] = str(v)
            elif isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    os.environ[f"{str(k).upper()}_{str(sub_k).upper()}"] = str(sub_v)
                    os.environ[str(sub_k)] = str(sub_v)
                    os.environ[str(sub_k).upper()] = str(sub_v)
except Exception:
    pass

from app.auth.auth_manager import init_user_db, is_full_access_user
from app.monitoring.audit_logger import log_audit_event, record_session_visit
from app.ui.auth_ui import render_auth_page
from app.ui.backtest_app import render_backtest_module
from app.ui.live_trade_app import render_live_trade_module
from app.ui.paper_trade_app import render_paper_trade_module

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database schema if not present
init_user_db()

# Auto-start OI Pulse Live Strength scheduler daemon if not already running
try:
    from scripts.schedule_oipulse_strength import start_oipulse_scheduler_background
    start_oipulse_scheduler_background(batch_minutes=1, offset_seconds=10)
except Exception as e:
    logger.warning("Could not auto-start OI Pulse scheduler: %s", e)

SUITE_MODES = [
    "⚡ Live Trading Terminal",
    "📈 AI Paper Trading Studio",
    "🧪 Strategy Backtesting Studio",
]


def render_unified_suite():
    # Page configuration - safe for reruns
    try:
        st.set_page_config(
            page_title="AI Auto-Trading Suite",
            page_icon="⚡",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        pass

    # ---------------- Custom Navigation Styling ----------------
    st.markdown("""
    <style>
        .unified-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 18px;
            background: linear-gradient(90deg, rgba(20, 25, 35, 0.85) 0%, rgba(15, 20, 30, 0.95) 100%);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            margin-bottom: 16px;
        }
        .user-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 16px;
            font-size: 0.85rem;
            background: rgba(0, 230, 118, 0.12);
            color: #00e676;
            border: 1px solid rgba(0, 230, 118, 0.3);
        }
        .nav-hint {
            font-size: 0.82rem;
            color: #90caf9;
            margin-top: -6px;
            margin-bottom: 10px;
        }
    </style>
    """, unsafe_allow_html=True)

    # ---------------- Authentication Gate ----------------
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        render_auth_page()
        return

    # ---------------- Unified Master Navigation ----------------
    current_user = st.session_state.get("current_user") or {}
    username = current_user.get("username", "Trader")
    full_name = current_user.get("full_name") or username
    role = current_user.get("role", "Trader").capitalize()

    is_full_access = is_full_access_user(username)

    BACKTEST_MODE = "🧪 Strategy Backtesting Studio"
    if is_full_access:
        available_modes = SUITE_MODES
    else:
        available_modes = [BACKTEST_MODE]

    # Master Sidebar Navigation
    st.sidebar.markdown("### 🧭 Trading Suite Navigator")

    if (
        st.session_state.pop("reset_suite_on_login", False)
        or "unified_suite_selector" not in st.session_state
        or st.session_state.unified_suite_selector not in available_modes
    ):
        st.session_state.unified_suite_selector = available_modes[0]

    def on_suite_change():
        new_module = st.session_state.get("unified_suite_selector")
        log_audit_event("MODULE_SWITCH", details={"username": username, "to_module": new_module})

    chosen_suite = st.sidebar.radio(
        "Active Workspace Module",
        available_modes,
        key="unified_suite_selector",
        on_change=on_suite_change,
        help="Switch between Live Execution Cockpit, Candle Replay Paper Trading, and Historical Backtest Lab." if is_full_access else "Strategy Backtesting Studio (Standard User Tier).",
    )

    if not is_full_access:
        st.sidebar.markdown("""
        <div style="background: rgba(255, 255, 255, 0.02); border: 1px dashed rgba(255, 255, 255, 0.15); border-radius: 8px; padding: 10px; margin-top: 8px; margin-bottom: 12px;">
            <div style="font-size: 0.78rem; font-weight: 600; color: #ffb74d; margin-bottom: 6px;">🔒 Restricted Modules:</div>
            <div style="font-size: 0.75rem; color: #777; margin-bottom: 3px;">🚫 ⚡ Live Trading Terminal</div>
            <div style="font-size: 0.75rem; color: #777;">🚫 📈 AI Paper Trading Studio</div>
            <div style="font-size: 0.70rem; color: #999; margin-top: 6px; font-style: italic;">Enabled only for authorized accounts (@admin, @nambirajan, @seetharaman).</div>
        </div>
        """, unsafe_allow_html=True)

    # User status & logout card in sidebar
    access_badge = "🛡️ Full Access" if is_full_access else "🔒 Backtest Only"
    badge_color = "#00e676" if is_full_access else "#ffb74d"
    st.sidebar.markdown(f"""
    <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 10px; margin-top: 10px; margin-bottom: 10px;">
        <div style="font-size: 0.85rem; font-weight: 700; color: #fff;">👤 {full_name}</div>
        <div style="font-size: 0.75rem; color: #888;">@{username} &bull; <span style="color:{badge_color};">{access_badge}</span></div>
    </div>
    """, unsafe_allow_html=True)

    if st.sidebar.button("🚪 Log Out", key="unified_logout_btn", use_container_width=True, type="secondary"):
        log_audit_event("USER_LOGOUT", details={"username": username})
        st.session_state.authenticated = False
        st.session_state.current_user = None
        st.session_state.reset_suite_on_login = True
        st.rerun()

    st.sidebar.markdown("---")

    # ---------------- Top Status Bar ----------------
    head_c1, head_c2 = st.columns([3, 1])
    with head_c1:
        st.caption(f"🚀 **Enterprise Suite** &bull; Active Workspace: **{chosen_suite}**")
    with head_c2:
        pill_style = f"color:{badge_color}; border-color:{badge_color}40; background:{badge_color}1a;"
        st.markdown(
            f"<div style='text-align: right;'><span class='user-pill' style='{pill_style}'>{access_badge} &bull; {username}</span></div>",
            unsafe_allow_html=True,
        )

    # ---------------- Workspace Module Dispatcher ----------------
    try:
        if chosen_suite == "⚡ Live Trading Terminal":
            if not is_full_access:
                st.error("⛔ Access Denied: Live Trading Terminal is restricted to authorized accounts (@admin, @nambirajan, @seetharaman).")
                render_backtest_module()
            else:
                render_live_trade_module()
        elif chosen_suite == "📈 AI Paper Trading Studio":
            if not is_full_access:
                st.error("⛔ Access Denied: AI Paper Trading Studio is restricted to authorized accounts (@admin, @nambirajan, @seetharaman).")
                render_backtest_module()
            else:
                render_paper_trade_module()
        elif chosen_suite == "🧪 Strategy Backtesting Studio":
            render_backtest_module()
        else:
            st.warning(f"Unrecognized workspace module: {chosen_suite}")
    except Exception as exc:
        logger.exception("Error rendering module %s: %s", chosen_suite, exc)
        st.error(f"⚠️ Error rendering **{chosen_suite}**: `{exc}`")
        st.exception(exc)


if __name__ == "__main__":
    render_unified_suite()

"""
Root entrypoint for the Unified AI Auto-Trading Platform.
Execute with:
    streamlit run main_app.py
"""
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Synchronize Streamlit Cloud secrets to os.environ for SDK and broker compatibility
try:
    import os
    import streamlit as st
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

# Run the unified application on every execution and rerun
from app.ui.main_app import render_unified_suite
from scripts.schedule_oipulse_strength import (
    start_oipulse_scheduler_background,
    ensure_chrome_running,
)

# Auto-launch Chrome with remote debugging on port 9222 for OI Pulse signin on server startup
try:
    ensure_chrome_running(url="https://oipulse.com/signin/", port=9222)
except Exception as e:
    print(f"[!] Warning: Could not auto-trigger Chrome on startup: {e}")

# Start OI Pulse Strength Scheduler daemon on server startup (triggers dynamically using ActiveSchedulerConfig)
try:
    start_oipulse_scheduler_background()
except Exception as e:
    print(f"[!] Warning: Could not initialize OI Pulse scheduler: {e}")

if __name__ == "__main__":
    render_unified_suite()

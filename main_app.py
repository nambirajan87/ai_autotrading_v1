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

if __name__ == "__main__":
    render_unified_suite()

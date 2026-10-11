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

# Run the unified application on every execution and rerun
from app.ui.main_app import render_unified_suite

if __name__ == "__main__":
    render_unified_suite()

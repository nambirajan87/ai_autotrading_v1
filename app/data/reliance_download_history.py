import sys
import time
from datetime import date

from app.data.upstox_loader import fetch_range
from app.monitoring.logging_setup import setup_logging

if __name__ == "__main__":
    setup_logging("INFO")
    key, start, end, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    df = fetch_range(key, "minutes", 5, date.fromisoformat(start), date.fromisoformat(end))
    df.to_csv(out, index=False)
    print("saved", out, df.shape)
"""
Compute 1-Minute OI Pulse Data
------------------------------
Reads data from data/oipulse_data.txt (treated as read-only backup).
Filters market hours to keep 09:15 to 15:15 (records after 15:15 are removed).
Expands 3-minute intervals to 1-minute records by interpolating:
  - T+2m and T+1m copy the values of existing record T.
  - Backfills 09:15:00, 09:16:00, 09:17:00 using the 09:18:00 values.
Writes strictly the 4 columns (Date,Time,LTP,Strength) without blank lines
to data/oipulse_data_new.txt.
"""

from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INPUT_TXT = DATA_DIR / "oipulse_data.txt"
OUTPUT_TXT = DATA_DIR / "oipulse_data_new.txt"

def compute_1m_data(input_file=INPUT_TXT, output_file=OUTPUT_TXT):
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # Read with dtype=str to preserve exact string representations (e.g. 23973.50)
    df = pd.read_csv(input_file, dtype=str)

    # Preserve original order of dates
    dates = []
    for d in df["Date"]:
        if d not in dates:
            dates.append(d)

    new_rows = []

    for d in dates:
        sub = df[df["Date"] == d]
        time_map = {}
        for _, row in sub.iterrows():
            time_map[row["Time"].strip()] = (row["LTP"].strip(), row["Strength"].strip())

        # 1. Start with 15:15:00 if present
        if "15:15:00" in time_map:
            ltp_1515, st_1515 = time_map["15:15:00"]
            new_rows.append({
                "Date": d,
                "Time": "15:15:00",
                "LTP": ltp_1515,
                "Strength": st_1515
            })

        # 2. Iterate descending 3m intervals from 15:12:00 down to 09:18:00
        curr = datetime.strptime("15:12:00", "%H:%M:%S")
        end = datetime.strptime("09:18:00", "%H:%M:%S")
        while curr >= end:
            t_str = curr.strftime("%H:%M:%S")
            if t_str in time_map:
                ltp_val, st_val = time_map[t_str]
                # Insert T+2m, T+1m, and T
                t_plus_2 = (curr + timedelta(minutes=2)).strftime("%H:%M:%S")
                t_plus_1 = (curr + timedelta(minutes=1)).strftime("%H:%M:%S")
                new_rows.append({"Date": d, "Time": t_plus_2, "LTP": ltp_val, "Strength": st_val})
                new_rows.append({"Date": d, "Time": t_plus_1, "LTP": ltp_val, "Strength": st_val})
                new_rows.append({"Date": d, "Time": t_str, "LTP": ltp_val, "Strength": st_val})
            curr -= timedelta(minutes=3)

        # 3. Backfill 09:17:00, 09:16:00, 09:15:00 using 09:18:00 record
        if "09:18:00" in time_map:
            ltp_18, st_18 = time_map["09:18:00"]
            new_rows.append({"Date": d, "Time": "09:17:00", "LTP": ltp_18, "Strength": st_18})
            new_rows.append({"Date": d, "Time": "09:16:00", "LTP": ltp_18, "Strength": st_18})
            new_rows.append({"Date": d, "Time": "09:15:00", "LTP": ltp_18, "Strength": st_18})

    df_new = pd.DataFrame(new_rows)[["Date", "Time", "LTP", "Strength"]]

    # Write without blank lines (continuous standard CSV)
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        df_new.to_csv(f, index=False, lineterminator="\n")

    print(f"[OK] Generated {len(df_new)} rows across {len(dates)} dates in {output_file}")
    print(f"     Rows per date: {len(df_new) // len(dates)}")
    return df_new

if __name__ == "__main__":
    compute_1m_data()

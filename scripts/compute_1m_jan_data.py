"""
Compute 1-Minute OI Pulse Data for January-August Dataset
---------------------------------------------------------
Reads data from:
  Oi Pulse/OI_RawData_from_Jan_new.txt

Transforms data:
1. Filters market hours to strictly 09:15:00 to 15:15:00 (removing EOD, >15:15, etc.).
2. Fills missing 1-minute intervals:
   - For T in descending order, missing minutes T+2 and T+1 use the value of record T
     (e.g., 15:14:00 and 15:13:00 use 15:12:00).
   - Backfills 09:17:00, 09:16:00, 09:15:00 using the 09:18:00 values.

Outputs to:
  Oi Pulse/OI_RawData_from_Jan_new_fill.txt
"""

from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd


def compute_1m_jan_data(
    input_file: Path | str | None = None,
    output_file: Path | str | None = None
) -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent.parent
    if input_file is None:
        input_file = base_dir / "Oi Pulse" / "OI_RawData_from_Jan_new.txt"
    else:
        input_file = Path(input_file)

    if output_file is None:
        output_file = base_dir / "Oi Pulse" / "OI_RawData_from_Jan_new_fill.txt"
    else:
        output_file = Path(output_file)

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # Read as string to keep exact decimal and sign representation
    df = pd.read_csv(input_file, dtype=str)

    # Preserve original order of dates
    dates = []
    for d in df["Date"]:
        if d not in dates:
            dates.append(d)

    # Prepare standard 1-minute time grid from 09:15:00 to 15:15:00
    grid = []
    c = datetime.strptime("09:15:00", "%H:%M:%S")
    e = datetime.strptime("15:15:00", "%H:%M:%S")
    while c <= e:
        grid.append(c.strftime("%H:%M:%S"))
        c += timedelta(minutes=1)

    new_rows = []

    for d in dates:
        sub = df[df["Date"] == d]
        time_map = {}
        for _, row in sub.iterrows():
            time_map[row["Time"].strip()] = (row["LTP"].strip(), row["Strength"].strip())

        val_map = {}
        last_val = None
        # Start value for 09:15..09:17 is 09:18:00
        start_val = time_map.get("09:18:00")

        for t_str in grid:
            if t_str in time_map:
                last_val = time_map[t_str]
            elif last_val is None:
                last_val = start_val
            val_map[t_str] = last_val

        # Order descending from 15:15:00 down to 09:15:00 for each day
        for t_str in reversed(grid):
            ltp_val, st_val = val_map[t_str]
            new_rows.append({
                "Date": d,
                "Time": t_str,
                "LTP": ltp_val,
                "Strength": st_val
            })

    df_new = pd.DataFrame(new_rows)[["Date", "Time", "LTP", "Strength"]]

    # Write without blank lines
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        df_new.to_csv(f, index=False, lineterminator="\n")

    print(f"[OK] Successfully generated {len(df_new)} rows across {len(dates)} dates.")
    print(f"     Rows per date: {len(df_new) // len(dates)} (361 minutes each day)")
    print(f"     Output saved to: {output_file}")
    return df_new


if __name__ == "__main__":
    compute_1m_jan_data()

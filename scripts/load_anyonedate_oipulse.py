"""
Reorganize and Load OI Pulse Data for Any Date
----------------------------------------------
Reusable utility to ingest raw copy-pasted table data from OI Pulse for ANY single trading date.

Workflow:
1. Detects the date (DD-MM-YYYY) automatically from the raw file content.
2. Extracts core readings: Date, Time, LTP, Strength.
3. Reorganizes and interpolates to standard 1-minute descending records matching:
   Date,Time,LTP,Strength
   DD-MM-YYYY,15:16:00,LTP,Strength
   DD-MM-YYYY,15:15:00,LTP,Strength
   ...
   DD-MM-YYYY,09:15:00,LTP,Strength
4. Saves a date-specific reorganized CSV: data/oipulse_<Date>_reorganized.csv.
5. Deletes existing records for that detected date ONLY from table 'oi_strength' in data/backtest.db.
6. Inserts the newly reorganized 1-minute records into 'oi_strength'.
7. Verifies database integrity and outputs summary report.

Usage:
    python scripts/load_anyonedate_oipulse.py
    python scripts/load_anyonedate_oipulse.py data/oipulse_27OCT.txt
    python scripts/load_anyonedate_oipulse.py "C:/path/to/any_oipulse_raw_file.txt"
"""

import os
import re
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_RAW = DATA_DIR / "oipulse_27OCT.txt"
DB_PATH = DATA_DIR / "backtest.db"


def parse_raw_oipulse_file(input_path: Path):
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # Pattern to match table row starts: row_idx, Date (DD-MM-YYYY), Time (HH:MM:SS), LTP
    pattern = re.compile(
        r"^\s*(\d+)\s+(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}:\d{2})\s+([\d\.,]+)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))

    time_map = {}
    records = []
    dates_found = set()

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]

        st_match = re.search(r"([+-]?\d+%)", chunk)
        st_val = st_match.group(1) if st_match else "0%"

        d_str = m.group(2).strip()
        t_str = m.group(3).strip()
        ltp_str = m.group(4).replace(",", "").strip()

        records.append((d_str, t_str, ltp_str, st_val))
        time_map[t_str] = (ltp_str, st_val)
        dates_found.add(d_str)

    return records, time_map, sorted(list(dates_found))


def build_1m_records(date_str: str, time_map: dict):
    sorted_times = sorted(time_map.keys())
    if not sorted_times:
        return []

    earliest_time_str = sorted_times[0]
    start_val = time_map[earliest_time_str]

    # Standard market grid: 09:15:00 to 15:16:00
    grid = []
    curr = datetime.strptime("09:15:00", "%H:%M:%S")
    end = datetime.strptime("15:16:00", "%H:%M:%S")
    while curr <= end:
        grid.append(curr.strftime("%H:%M:%S"))
        curr += timedelta(minutes=1)

    val_map = {}
    last_val = None

    for t_str in grid:
        if t_str in time_map:
            last_val = time_map[t_str]
        elif last_val is None:
            last_val = start_val
        val_map[t_str] = last_val

    # Produce rows sorted in DESCENDING order by Time (matching master sample format)
    rows_desc = []
    for t_str in reversed(grid):
        rows_desc.append({
            "Date": date_str,
            "Time": t_str,
            "LTP": float(val_map[t_str][0]),
            "Strength": val_map[t_str][1],
        })

    return rows_desc


def process_single_date(raw_file: Path, db_file: Path = DB_PATH):
    print(f"\n========================================================")
    print(f"OI PULSE SINGLE-DATE INGESTION PIPELINE")
    print(f"Source Raw File: {raw_file}")
    print(f"Target Database: {db_file}")
    print(f"========================================================")

    # 1. Parse raw file
    raw_records, time_map, dates_found = parse_raw_oipulse_file(raw_file)
    if not raw_records:
        raise ValueError(f"No valid OI Pulse records detected in: {raw_file}")

    print(f"[1/4] Parsed {len(raw_records)} discrete readings.")
    print(f"      Dates detected in file: {dates_found}")

    # Process each detected date (typically single date)
    for target_date in dates_found:
        sub_time_map = {r[1]: (r[2], r[3]) for r in raw_records if r[0] == target_date}
        print(f"\n>>> Processing Date: {target_date} ({len(sub_time_map)} raw rows)")

        # 2. Build 1-minute descending records
        print(f"[2/4] Generating 1-minute interpolated records...")
        rows_1m = build_1m_records(target_date, sub_time_map)
        df_1m = pd.DataFrame(rows_1m)
        print(f"      Generated {len(df_1m)} 1-minute records spanning {rows_1m[0]['Time']} down to {rows_1m[-1]['Time']}.")

        # Save date-specific CSV
        safe_date_tag = target_date.replace("-", "")
        reorg_csv = DATA_DIR / f"oipulse_{safe_date_tag}_reorganized.csv"
        df_1m.to_csv(reorg_csv, index=False)
        print(f"      Saved date-specific CSV to: {reorg_csv}")

        # 3. Database operations
        print(f"[3/4] Connecting to SQLite database...")
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Delete existing data for target_date ONLY
        cursor.execute("SELECT COUNT(*) FROM oi_strength WHERE Date = ?", (target_date,))
        prev_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM oi_strength WHERE Date = ?", (target_date,))
        deleted_count = cursor.rowcount
        conn.commit()
        print(f"      [OK] Deleted {deleted_count} existing records for Date '{target_date}' from oi_strength table.")

        # Check Trigger_Time column
        cursor.execute("PRAGMA table_info(oi_strength);")
        cols = [col[1] for col in cursor.fetchall()]
        has_trigger_time = "Trigger_Time" in cols

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if has_trigger_time:
            insert_records = [
                (r["Date"], r["Time"], float(r["LTP"]), r["Strength"], now_str)
                for r in rows_1m
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO oi_strength (Date, Time, LTP, Strength, Trigger_Time)
                VALUES (?, ?, ?, ?, ?)
                """,
                insert_records,
            )
        else:
            insert_records = [
                (r["Date"], r["Time"], float(r["LTP"]), r["Strength"])
                for r in rows_1m
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO oi_strength (Date, Time, LTP, Strength)
                VALUES (?, ?, ?, ?)
                """,
                insert_records,
            )
        conn.commit()
        print(f"      [OK] Inserted {len(rows_1m)} newly reorganized records for Date '{target_date}'.")

        # 4. Verification
        print(f"[4/4] Verifying database integrity...")
        cursor.execute("SELECT COUNT(*) FROM oi_strength WHERE Date = ?", (target_date,))
        today_cnt = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM oi_strength;")
        total_cnt = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT Date) FROM oi_strength;")
        dist_dates = cursor.fetchone()[0]

        cursor.execute(
            "SELECT Date, Time, LTP, Strength FROM oi_strength WHERE Date = ? ORDER BY Time DESC LIMIT 3;",
            (target_date,),
        )
        sample_head = cursor.fetchall()

        cursor.execute(
            "SELECT Date, Time, LTP, Strength FROM oi_strength WHERE Date = ? ORDER BY Time ASC LIMIT 3;",
            (target_date,),
        )
        sample_tail = cursor.fetchall()

        conn.close()

        print(f"--------------------------------------------------------")
        print(f"VERIFICATION SUMMARY for Date: {target_date}")
        print(f"  Rows in oi_strength for {target_date}: {today_cnt}")
        print(f"  Total rows in oi_strength (all dates): {total_cnt}")
        print(f"  Total distinct trading days in DB:     {dist_dates}")
        print(f"  Sample Head (Descending):")
        for r in sample_head:
            print(f"    {r[0]},{r[1]},{r[2]:.2f},{r[3]}")
        print(f"  Sample Tail (Ascending):")
        for r in sample_tail:
            print(f"    {r[0]},{r[1]},{r[2]:.2f},{r[3]}")
        print(f"--------------------------------------------------------")

    print(f"\n[ALL DONE] Single-date ingestion completed successfully.\n")


def main():
    if len(sys.argv) > 1:
        target_file = Path(sys.argv[1])
    else:
        target_file = DEFAULT_RAW

    if not target_file.exists():
        print(f"[ERROR] Target file does not exist: {target_file}")
        print(f"Usage: python scripts/load_anyonedate_oipulse.py [path_to_raw_oipulse_txt]")
        sys.exit(1)

    process_single_date(target_file)


if __name__ == "__main__":
    main()

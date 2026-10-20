"""
OI Pulse Pasted Data Reorganizer
---------------------------------
Reorganizes copy-pasted table data from OI Pulse Trending OI page.
Extracts strictly the 4 columns requested:
- Date
- Time
- LTP
- Strength

Usage:
    python scripts/reorganize_oipulse_data.py
"""

import os
import re
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INPUT_TXT = DATA_DIR / "oipulse_data.txt"
OUTPUT_CSV = DATA_DIR / "nifty_trending_oi_3m_sep_oct_2026.csv"
BACKUP_TXT = DATA_DIR / "oipulse_data_raw_backup_full.txt"

def reorganize_file(filepath=INPUT_TXT):
    if not filepath.exists():
        print(f"[!] File not found: {filepath}")
        return []

    # Read all lines and filter out empty lines (no blank lines)
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        raw_lines = [line.strip() for line in f if line.strip()]

    records = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]

        # Skip headers
        lower = line.lower()
        if (line == "Date,Time,LTP,Strength" or 
            "day h/l break" in lower or 
            "diff. in oi" in lower or
            ("sentiment" in lower and "bearish" not in lower and "bullish" not in lower)):
            i += 1
            continue

        # Case 1: Line is already CSV formatted (Date,Time,LTP,Strength)
        csv_match = re.match(r"^(\d{2}-\d{2}-\d{4})\s*,\s*([^,]+)\s*,\s*([\d\.,]+)\s*,\s*([+-]?\d+(?:\.\d+)?%)\s*$", line)
        if csv_match:
            d, t, ltp, s = csv_match.groups()
            records.append({
                "Date": d.strip(),
                "Time": t.strip(),
                "LTP": ltp.replace(",", "").strip(),
                "Strength": s.strip()
            })
            i += 1
            continue

        # Case 2: Tab-separated row from OI Pulse copy-paste
        if re.search(r"\d{2}-\d{2}-\d{4}", line):
            parts = [p.strip() for p in line.split("\t")] if "\t" in line else [p.strip() for p in line.split()]
            date_idx = -1
            for idx, p in enumerate(parts):
                if re.match(r"^\d{2}-\d{2}-\d{4}$", p):
                    date_idx = idx
                    break

            if date_idx != -1 and date_idx + 2 < len(parts):
                d = parts[date_idx]
                t = parts[date_idx + 1]
                ltp = parts[date_idx + 2].replace(",", "")

                # Strength is typically on the next line (e.g. -38%, +10%)
                strength = ""
                if i + 1 < len(raw_lines):
                    next_line = raw_lines[i + 1].strip()
                    if re.match(r"^[+-]?\d+(?:\.\d+)?%$", next_line) or "%" in next_line:
                        strength = next_line

                if strength:
                    records.append({
                        "Date": d,
                        "Time": t,
                        "LTP": ltp,
                        "Strength": strength
                    })
            i += 1
            continue

        i += 1

    if not records:
        print("[!] No records parsed.")
        return []

    # Create DataFrame and deduplicate while maintaining exact order
    df = pd.DataFrame(records)[["Date", "Time", "LTP", "Strength"]].drop_duplicates()

    # Write cleaned CSV directly into oipulse_data.txt without any blank lines
    with open(INPUT_TXT, "w", encoding="utf-8", newline="") as f:
        df.to_csv(f, index=False, lineterminator="\n")

    # Also sync into target CSV
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        df.to_csv(f, index=False, lineterminator="\n")

    print(f"[OK] Successfully reorganized {len(df)} rows across {df['Date'].nunique()} date(s).")
    print(f"     Updated: {INPUT_TXT}")
    print(f"     Synced to: {OUTPUT_CSV}")
    print("\nSample records (head):")
    print(df.head(5).to_string(index=False))
    print("...")
    print("Sample records (tail):")
    print(df.tail(5).to_string(index=False))

    return records

if __name__ == "__main__":
    reorganize_file()

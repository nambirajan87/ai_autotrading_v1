"""
Load OI Strength Data into SQLite Database
------------------------------------------
Loads 1-minute OI Pulse strength data from data/oipulse_data_new.txt
into the 'oi_strength' table in data/backtest.db.

Table Schema:
- id: INTEGER PRIMARY KEY AUTOINCREMENT
- Date: TEXT (DD-MM-YYYY)
- Time: TEXT (HH:MM:SS)
- LTP: REAL
- Strength: TEXT (e.g. -63%)
"""

import sqlite3
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "backtest.db"
CSV_PATH = DATA_DIR / "oipulse_data_new.txt"

SCHEMA = """
CREATE TABLE IF NOT EXISTS oi_strength (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Date TEXT NOT NULL,
    Time TEXT NOT NULL,
    LTP REAL NOT NULL,
    Strength TEXT NOT NULL,
    UNIQUE(Date, Time)
);

CREATE INDEX IF NOT EXISTS idx_oi_strength_date ON oi_strength(Date);
CREATE INDEX IF NOT EXISTS idx_oi_strength_datetime ON oi_strength(Date, Time);
"""

def load_data(csv_file=CSV_PATH, db_file=DB_PATH):
    if not csv_file.exists():
        raise FileNotFoundError(f"Source file not found: {csv_file}")

    print(f"Reading source data from: {csv_file}")
    df = pd.read_csv(csv_file, dtype=str)
    print(f"Loaded {len(df)} rows from CSV.")

    # Clean / convert LTP to numeric float for SQL queries, keeping Strength as string
    df["LTP"] = pd.to_numeric(df["LTP"], errors="coerce")

    # Connect to SQLite
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create table & indexes
    cursor.executescript(SCHEMA)

    # Insert or Replace records
    records = [
        (row["Date"].strip(), row["Time"].strip(), float(row["LTP"]), row["Strength"].strip())
        for _, row in df.iterrows()
    ]

    cursor.executemany(
        """
        INSERT OR REPLACE INTO oi_strength (Date, Time, LTP, Strength)
        VALUES (?, ?, ?, ?)
        """,
        records
    )
    conn.commit()

    # Verification
    cursor.execute("SELECT COUNT(*) FROM oi_strength;")
    total_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT Date) FROM oi_strength;")
    unique_dates = cursor.fetchone()[0]

    cursor.execute("SELECT id, Date, Time, LTP, Strength FROM oi_strength LIMIT 5;")
    sample_rows = cursor.fetchall()

    conn.close()

    print(f"\n[OK] Database updated: {db_file}")
    print(f"     Table: oi_strength")
    print(f"     Total rows in table: {total_count}")
    print(f"     Unique trading dates: {unique_dates}")
    print("\nSample records:")
    for r in sample_rows:
        print(f"  ID={r[0]} | Date={r[1]} | Time={r[2]} | LTP={r[3]:.2f} | Strength={r[4]}")

    return total_count

if __name__ == "__main__":
    load_data()

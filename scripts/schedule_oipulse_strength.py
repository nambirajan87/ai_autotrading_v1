"""
OI Pulse Live Strength Scheduler
---------------------------------
Schedules periodic extraction of Row 1 (Strength, LTP, Time, Date)
from the active OI Pulse session running in Chrome (port 9222).

Inserts extracted records into SQLite Database:
- Database: data/backtest.db
- Table:    oi_strength (Columns: id, Date, Time, LTP, Strength, Trigger_Time)

Trigger Schedule:
- Runs every `batchTime` minutes (configurable, e.g. 1 min or 3 mins).
- Fires precisely at `hh:mm:10` (every 10th second after interval close).
  Example (batchTime = 1m): 00:45:10, 00:46:10, 00:47:10 ...
  Example (batchTime = 3m): 00:45:10, 00:48:10, 00:51:10 ...

Usage:
    python scripts/schedule_oipulse_strength.py --batch 3     # 3-minute interval at :10s
    python scripts/schedule_oipulse_strength.py --batch 1     # 1-minute interval at :10s
    python scripts/schedule_oipulse_strength.py --once        # Run once immediately and exit
"""

import sys
import os
import time
import sqlite3
import argparse
import threading
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By

DEBUG_PORT = 9222
DEBUG_ADDRESS = f"127.0.0.1:{DEBUG_PORT}"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "backtest.db"
_THREAD_NAME = "OIPulseSchedulerWorker"

def ensure_db_schema(db_path: Path = DEFAULT_DB_PATH):
    """
    Ensures table 'oi_strength' exists with all required columns,
    including 'Trigger_Time'. Refills any NULL or empty Trigger_Time values.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS oi_strength (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Date TEXT,
            Time TEXT,
            LTP REAL,
            Strength TEXT,
            Trigger_Time TEXT
        )
    """)

    # Check if Trigger_Time column exists
    c.execute("PRAGMA table_info(oi_strength);")
    cols = [r[1] for r in c.fetchall()]
    if "Trigger_Time" not in cols:
        c.execute("ALTER TABLE oi_strength ADD COLUMN Trigger_Time TEXT;")

    # Refill any empty or NULL Trigger_Time with current datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "UPDATE oi_strength SET Trigger_Time = ? WHERE Trigger_Time IS NULL OR Trigger_Time = '';",
        (now_str,)
    )

    conn.commit()
    conn.close()

def save_record_to_db(record: dict, db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Inserts a single extracted Row 1 record into the oi_strength table.
    Returns the newly inserted row ID.
    """
    ensure_db_schema(db_path)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    date_val = str(record.get("Date", "")).strip()
    time_val = str(record.get("Time", "")).strip()

    try:
        ltp_val = float(str(record.get("LTP", 0)).replace(",", "").strip())
    except Exception:
        ltp_val = 0.0

    strength_val = str(record.get("Strength", "")).strip()
    trigger_time = str(record.get("Trigger_Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))).strip()

    c.execute("""
        INSERT OR REPLACE INTO oi_strength (Date, Time, LTP, Strength, Trigger_Time)
        VALUES (?, ?, ?, ?, ?)
    """, (date_val, time_val, ltp_val, strength_val, trigger_time))

    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id

MARKET_START_HOUR = 9
MARKET_START_MIN = 15
MARKET_END_HOUR = 15
MARKET_END_MIN = 15

def get_next_trigger(now: datetime, batch_minutes: int = 1, offset_seconds: int = 10, market_hours_only: bool = True) -> datetime:
    """
    Calculates the exact next trigger datetime aligned to:
      minute % batch_minutes == 0 AND second == offset_seconds

    If market_hours_only is True:
    - Restricts triggers to Monday - Friday (weekdays only)
    - Restricts triggers to between 09:15:offset_seconds and 15:15:offset_seconds
    """
    base_minute = now.replace(second=0, microsecond=0)
    rem = base_minute.minute % batch_minutes
    aligned_minute = base_minute - timedelta(minutes=rem)
    candidate = aligned_minute.replace(second=offset_seconds)

    if candidate <= now:
        candidate = candidate + timedelta(minutes=batch_minutes)

    if not market_hours_only:
        return candidate

    # Enforce weekday and 09:15 - 15:15 constraints
    from datetime import time as dtime
    market_open_time = dtime(MARKET_START_HOUR, MARKET_START_MIN, offset_seconds)
    market_close_time = dtime(MARKET_END_HOUR, MARKET_END_MIN, offset_seconds)

    while True:
        # 1. Skip weekend (Saturday=5, Sunday=6) -> advance to Monday at 09:15:offset_seconds
        if candidate.weekday() == 5:  # Saturday
            candidate = candidate + timedelta(days=2)
            candidate = candidate.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MIN, second=offset_seconds, microsecond=0)
            continue
        elif candidate.weekday() == 6:  # Sunday
            candidate = candidate + timedelta(days=1)
            candidate = candidate.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MIN, second=offset_seconds, microsecond=0)
            continue

        # 2. Before market open on weekday (before 09:15:offset_seconds)
        if candidate.time() < market_open_time:
            candidate = candidate.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MIN, second=offset_seconds, microsecond=0)
            break

        # 3. After market close on weekday (after 15:15:offset_seconds)
        if candidate.time() > market_close_time:
            candidate = candidate + timedelta(days=1)
            candidate = candidate.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MIN, second=offset_seconds, microsecond=0)
            continue  # Loop back to re-check weekday for the new day

        # Valid candidate within Monday-Friday 09:15 - 15:15
        break

    return candidate

class OIPulseDriver:
    """Maintains a persistent connection to Chrome on port 9222."""
    def __init__(self, address=DEBUG_ADDRESS):
        self.address = address
        self.driver = None

    def connect(self):
        if self.driver:
            try:
                _ = self.driver.current_url
                return self.driver
            except Exception:
                self.driver = None

        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", self.address)
        try:
            self.driver = webdriver.Chrome(options=options)
            return self.driver
        except Exception:
            self.driver = None
            return None

    def fetch_row1(self):
        """Extracts Row 1 values: Date, Time, LTP, Strength."""
        driver = self.connect()
        if not driver:
            return None

        js_extract = """
        var row1 = null;
        var trs = document.querySelectorAll('tbody tr');
        for (var r = 0; r < trs.length; r++) {
            var lineTh = trs[r].querySelector('th.line-numbers');
            if (lineTh && lineTh.innerText.trim() === '1') {
                row1 = trs[r];
                break;
            }
        }
        if (!row1 && trs.length > 0) {
            row1 = trs[0];
        }
        if (!row1) return null;

        var lineTh = row1.querySelector('th.line-numbers');
        var lineNum = lineTh ? lineTh.innerText.trim() : '1';
        var tds = row1.querySelectorAll('td');

        var rDate = (tds.length > 0 && tds[0]) ? tds[0].innerText.trim() : '';
        var rTime = (tds.length > 1 && tds[1]) ? tds[1].innerText.trim() : '';
        var rLtp = (tds.length > 2 && tds[2]) ? tds[2].innerText.trim().replace(/,/g, '') : '';
        
        var pill = row1.querySelector('.strength-pill__num');
        var rStrength = '';
        if (pill) {
            rStrength = pill.innerText.trim().replace(/\\u2212/g, '-');
        } else if (tds.length > 7 && tds[7]) {
            rStrength = tds[7].innerText.trim().replace(/\\u2212/g, '-');
        }

        return {
            Row: lineNum,
            Date: rDate,
            Time: rTime,
            LTP: rLtp,
            Strength: rStrength
        };
        """
        try:
            return driver.execute_script(js_extract)
        except Exception as e:
            print(f"[!] Extraction error: {e}")
            return None

class ActiveSchedulerConfig:
    batch_minutes = 1
    offset_seconds = 10
    market_hours_only = True

def run_scheduler(batch_minutes: int = 1, offset_seconds: int = 10, db_path: Path = DEFAULT_DB_PATH, run_once: bool = False, market_hours_only: bool = True, verbose: bool = True):
    ActiveSchedulerConfig.batch_minutes = batch_minutes
    ActiveSchedulerConfig.offset_seconds = offset_seconds
    ActiveSchedulerConfig.market_hours_only = market_hours_only

    if verbose:
        print("=" * 65)
        print("[OI PULSE LIVE STRENGTH SCHEDULER]")
        print("=" * 65)
        print(f"Batch Time     : {ActiveSchedulerConfig.batch_minutes} minute(s)")
        print(f"Trigger Target : Every {ActiveSchedulerConfig.batch_minutes}m at :{ActiveSchedulerConfig.offset_seconds:02d}s (hh:mm:{ActiveSchedulerConfig.offset_seconds:02d})")
        print(f"Market Window  : {'Monday - Friday, 09:15 AM - 03:15 PM' if ActiveSchedulerConfig.market_hours_only else '24x7 (Anytime)'}")
        print(f"Attached Port  : {DEBUG_PORT}")
        print(f"Target DB      : {db_path} -> table 'oi_strength'")
        print("=" * 65 + "\n")

    ensure_db_schema(db_path)
    pulse_client = OIPulseDriver()

    if run_once:
        if verbose:
            print("[*] Executing single fetch now...")
        data = pulse_client.fetch_row1()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if data:
            data["Trigger_Time"] = now_str
            row_id = save_record_to_db(data, db_path)
            if verbose:
                print("\n----------------------------------------")
                print("OI PULSE ROW 1 DATA:")
                print("----------------------------------------")
                print(f"  DB ID       : {row_id}")
                for k in ["Trigger_Time", "Row", "Date", "Time", "LTP", "Strength"]:
                    print(f"  {k:12}: {data.get(k, '')}")
                print("----------------------------------------")
                print(f"[OK] Record successfully inserted into {db_path.name} -> oi_strength (ID: {row_id})\n")
            return data
        else:
            if verbose:
                print("[!] Could not fetch Row 1. Ensure OI Pulse tab is open and table is rendered.")
            return None

    if verbose:
        print(">> Scheduler is active! Press Ctrl+C to stop.\n")
    trigger_count = 0

    while True:
        curr_batch = ActiveSchedulerConfig.batch_minutes
        curr_offset = ActiveSchedulerConfig.offset_seconds
        curr_market_only = ActiveSchedulerConfig.market_hours_only

        now = datetime.now()
        next_trigger = get_next_trigger(now, curr_batch, curr_offset, market_hours_only=curr_market_only)
        sleep_sec = (next_trigger - now).total_seconds()

        if verbose:
            if sleep_sec > 300:
                hours_left = sleep_sec / 3600.0
                print(f"[WAITING] Market closed. Next trigger at {next_trigger.strftime('%a %Y-%m-%d %H:%M:%S')} (in {hours_left:.1f}h)...", end="\r", flush=True)
            else:
                print(f"[WAITING] Next trigger at {next_trigger.strftime('%H:%M:%S')} (in {sleep_sec:.1f}s, batch={curr_batch}m)...", end="\r", flush=True)

        if sleep_sec > 0:
            time.sleep(sleep_sec)

        # Trigger execution at hh:mm:10
        trigger_time = datetime.now()
        trigger_str = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        trigger_count += 1

        if verbose:
            print(f"\n[{trigger_str}] [TRIGGER #{trigger_count}] Fetching Row 1...")
        data = pulse_client.fetch_row1()

        if data:
            data["Trigger_Time"] = trigger_str
            row_id = save_record_to_db(data, db_path)
            if verbose:
                print("+-------------------------------------------------------------+")
                print(f"| Trigger   : {trigger_str:<48}|")
                print(f"| DB Row ID : {row_id:<48}|")
                print(f"| Date      : {data.get('Date', ''):<48}|")
                print(f"| Time      : {data.get('Time', ''):<48}|")
                print(f"| LTP       : {data.get('LTP', ''):<48}|")
                print(f"| Strength  : {data.get('Strength', ''):<48}|")
                print("+-------------------------------------------------------------+")
                print(f"   -> Inserted into oi_strength table (ID: {row_id})\n")
        else:
            if verbose:
                print("[!] Notice: Chrome session on port 9222 not reachable or table not ready.")
                print("    Will retry automatically on next trigger interval.\n")

        # Brief pause to ensure we don't double trigger in the same second
        time.sleep(1.0)

def start_oipulse_scheduler_background(batch_minutes: int = 1, offset_seconds: int = 10, db_path: Path = DEFAULT_DB_PATH, market_hours_only: bool = True) -> bool:
    """
    Starts the scheduler as a background daemon thread.
    Safe across Streamlit server reruns (checks if already running and updates config dynamically).
    """
    ActiveSchedulerConfig.batch_minutes = batch_minutes
    ActiveSchedulerConfig.offset_seconds = offset_seconds
    ActiveSchedulerConfig.market_hours_only = market_hours_only

    for t in threading.enumerate():
        if t.name == _THREAD_NAME and t.is_alive():
            return True  # Already running; dynamic config updated above

    def _worker():
        try:
            window_desc = "Mon-Fri 09:15-15:15" if market_hours_only else "24x7"
            print(f"[OI Pulse Scheduler] Background thread started (Interval: {batch_minutes}m, Trigger: :{offset_seconds:02d}s, Window: {window_desc}, DB: {db_path.name})")
            run_scheduler(
                batch_minutes=batch_minutes,
                offset_seconds=offset_seconds,
                db_path=db_path,
                run_once=False,
                market_hours_only=market_hours_only,
                verbose=True
            )
        except Exception as e:
            print(f"[!] [OI Pulse Scheduler] Background thread terminated with error: {e}")

    thread = threading.Thread(target=_worker, name=_THREAD_NAME, daemon=True)
    thread.start()
    return True

def main():
    parser = argparse.ArgumentParser(description="OI Pulse Live Strength Scheduler")
    parser.add_argument("-b", "--batch", type=int, default=1, help="Batch time in minutes (default: 1)")
    parser.add_argument("-s", "--offset", type=int, default=10, help="Offset seconds in minute to trigger on (default: 10)")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="Target SQLite DB path")
    parser.add_argument("--once", action="store_true", help="Fetch Row 1 once immediately, insert into DB, and exit")
    parser.add_argument("--anytime", action="store_true", help="Run 24x7 without restricting to Mon-Fri 09:15-15:15 market hours")

    args = parser.parse_args()

    try:
        run_scheduler(
            batch_minutes=args.batch,
            offset_seconds=args.offset,
            db_path=Path(args.db),
            run_once=args.once,
            market_hours_only=not args.anytime,
            verbose=True
        )
    except KeyboardInterrupt:
        print("\n\n[INFO] Scheduler stopped by user (Ctrl+C). Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main()

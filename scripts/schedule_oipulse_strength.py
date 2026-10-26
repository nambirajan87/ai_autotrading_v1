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
import json
import socket
import shutil
import subprocess
import sqlite3
import argparse
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By

DEBUG_PORT = 9222
DEBUG_ADDRESS = f"127.0.0.1:{DEBUG_PORT}"
OIPULSE_SIGNIN_URL = "https://oipulse.com/signin/"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "backtest.db"
_THREAD_NAME = "OIPulseSchedulerWorker"
_LAST_CHROME_LAUNCH_TIME = 0.0

def is_port_open(port: int = DEBUG_PORT, host: str = "127.0.0.1") -> bool:
    """Checks if the Chrome remote debugging port is open and listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def get_chrome_executable() -> str | None:
    """Discovers the Chrome executable path across standard installation locations."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p

    for name in ["chrome", "chrome.exe", "google-chrome"]:
        found = shutil.which(name)
        if found and os.path.exists(found):
            return found
    return None

def ensure_chrome_running(url: str = OIPULSE_SIGNIN_URL, port: int = DEBUG_PORT, timeout: float = 6.0) -> bool:
    """
    Ensures Google Chrome is running with remote debugging enabled on the specified port.
    If port is not active, triggers Chrome to open with the specified URL (https://oipulse.com/signin/).
    """
    global _LAST_CHROME_LAUNCH_TIME

    if is_port_open(port):
        return True

    now = time.time()
    # Debounce launch attempts within 10 seconds to avoid duplicate windows on reruns
    if now - _LAST_CHROME_LAUNCH_TIME < 10.0:
        deadline = now + min(timeout, 3.0)
        while time.time() < deadline:
            if is_port_open(port):
                return True
            time.sleep(0.3)
        return is_port_open(port)

    _LAST_CHROME_LAUNCH_TIME = now

    chrome_path = get_chrome_executable()
    if not chrome_path:
        print("[!] Warning: Google Chrome executable not found in standard paths.")
        return False

    profile_dir = os.path.expandvars(r"%USERPROFILE%\ChromeOIProfile")
    os.makedirs(profile_dir, exist_ok=True)

    print("=" * 65)
    print(f"[*] Port {port} not active. Launching Chrome with remote debugging...")
    print(f"    Target URL  : {url}")
    print(f"    Debug Port  : {port}")
    print(f"    User Profile: {profile_dir}")
    print("=" * 65)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url
    ]

    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.4)
            if is_port_open(port):
                print(f"[OK] Chrome remote debugging successfully started on port {port}!\n")
                return True

        if is_port_open(port):
            return True

        print(f"[!] Notice: Chrome process initiated, waiting for port {port} to bind.\n")
        return False
    except Exception as e:
        print(f"[!] Error launching Chrome on port {port}: {e}")
        return False


def ensure_db_schema(db_path: Path = DEFAULT_DB_PATH):
    """
    Ensures table 'oi_strength' exists with all required columns,
    including 'Trigger_Time' and 'Trigger_Count'. Enables WAL mode for high concurrency.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")

    c.execute("""
        CREATE TABLE IF NOT EXISTS oi_strength (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Date TEXT,
            Time TEXT,
            LTP REAL,
            Strength TEXT,
            Trigger_Time TEXT,
            Trigger_Count INTEGER
        )
    """)

    # Check if columns exist
    c.execute("PRAGMA table_info(oi_strength);")
    cols = [r[1] for r in c.fetchall()]
    if "Trigger_Time" not in cols:
        c.execute("ALTER TABLE oi_strength ADD COLUMN Trigger_Time TEXT;")
    if "Trigger_Count" not in cols:
        c.execute("ALTER TABLE oi_strength ADD COLUMN Trigger_Count INTEGER;")

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
    Using the same transaction / thread id, updates ActiveSchedulerConfig.last_row and live cache JSON
    so the results page UI reflects the current values with zero DB transactions.
    """
    ensure_db_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=5.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")

    date_val = str(record.get("Date", "")).strip()
    time_val = str(record.get("Time", "")).strip()

    try:
        ltp_val = float(str(record.get("LTP", 0)).replace(",", "").strip())
    except Exception:
        ltp_val = 0.0

    strength_val = str(record.get("Strength", "")).strip()
    trigger_time = str(record.get("Trigger_Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))).strip()
    trigger_count = int(record.get("Trigger_Count", 0) or 0)

    c.execute("""
        INSERT OR REPLACE INTO oi_strength (Date, Time, LTP, Strength, Trigger_Time, Trigger_Count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date_val, time_val, ltp_val, strength_val, trigger_time, trigger_count))

    new_id = c.lastrowid
    conn.commit()
    conn.close()

    # Using the same execution/thread, update in-memory cache directly (zero UI-to-DB transactions)
    curr_thread_id = threading.get_ident()
    auto_triggered = is_paper_trade_auto_triggered()
    payload = {
        "id": new_id,
        "Date": date_val,
        "Time": time_val,
        "LTP": ltp_val,
        "Strength": strength_val,
        "Trigger_Time": trigger_time,
        "Trigger_Count": trigger_count,
        "thread_id": curr_thread_id,
        "ts": time.time(),
        "auto_paper_trade_active": auto_triggered,
        "auto_paper_trade_time": "09:16:00",
        "market_window": "Monday - Friday, 09:15 AM - 03:15 PM",
        "status": "WATCHING_OI_STRENGTH" if auto_triggered else "STANDBY",
    }
    ActiveSchedulerConfig.last_row = payload
    ActiveSchedulerConfig.trigger_count = trigger_count
    ActiveSchedulerConfig.last_update_ts = time.time()
    ActiveSchedulerConfig.last_thread_id = curr_thread_id

    # Write atomic live cache JSON so any external process or UI tab syncs with zero SQLite transactions
    try:
        cache_path = db_path.parent / "oipulse_live_cache.json" if isinstance(db_path, Path) else Path(db_path).parent / "oipulse_live_cache.json"
        tmp_path = cache_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        tmp_path.replace(cache_path)
    except Exception:
        pass

    return new_id

AUTO_PAPER_TRIGGER_HOUR = 9
AUTO_PAPER_TRIGGER_MIN = 16
MARKET_START_HOUR = 9
MARKET_START_MIN = 15
MARKET_END_HOUR = 15
MARKET_END_MIN = 15

def is_paper_trade_auto_triggered(now: Optional[datetime] = None) -> bool:
    """
    Checks if the current time and market window satisfies the 09:16 AM auto-trigger condition:
    - Market Window: Monday - Friday, 09:15 AM - 03:15 PM
    - Auto-trigger condition: fires at/after 09:16:00 AM up to 03:15:00 PM
    - If market_hours_only is False (24x7 / MockLive): returns True anytime
    """
    if not getattr(ActiveSchedulerConfig, "market_hours_only", True):
        return True

    from datetime import time as dtime
    if now is None:
        now = datetime.now()

    # Skip weekends (Saturday=5, Sunday=6)
    if now.weekday() >= 5:
        return False

    t = now.time()
    start_t = dtime(AUTO_PAPER_TRIGGER_HOUR, AUTO_PAPER_TRIGGER_MIN, 0)
    end_t = dtime(MARKET_END_HOUR, MARKET_END_MIN, 0)
    return start_t <= t <= end_t

def get_next_trigger(now: datetime, batch_minutes: int = 1, offset_seconds: int = 13, market_hours_only: bool = True) -> datetime:
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

        if not is_port_open(DEBUG_PORT):
            ensure_chrome_running(url=OIPULSE_SIGNIN_URL, port=DEBUG_PORT)
            if not is_port_open(DEBUG_PORT):
                return None

        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", self.address)
        try:
            self.driver = webdriver.Chrome(options=options)
            return self.driver
        except Exception:
            self.driver = None
            return None

    def fetch_row1(self, max_retries: int = 3, retry_delay: float = 0.8):
        """Extracts Row 1 values: Date, Time, LTP, Strength with immediate retries if table DOM is re-rendering."""
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
        for attempt in range(max_retries):
            driver = self.connect()
            if not driver:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                continue

            try:
                res = driver.execute_script(js_extract)
                if res and res.get("Date") and res.get("LTP") and res.get("Strength"):
                    return res
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"[!] Extraction error: {e}")

            if attempt < max_retries - 1:
                time.sleep(retry_delay)

        return None

class ActiveSchedulerConfig:
    batch_minutes = 1
    offset_seconds = 13
    market_hours_only = True
    trigger_count = 0
    last_row = {}
    last_update_ts = 0.0
    last_thread_id = None
    auto_paper_trade_triggered = False
    auto_paper_trade_time = "09:16:00"
    market_window_desc = "Monday - Friday, 09:15 AM - 03:15 PM"

def set_scheduler_market_hours_mode(market_hours_only: bool):
    """Dynamically sets whether the scheduler restricts triggers to market hours."""
    ActiveSchedulerConfig.market_hours_only = market_hours_only

def get_latest_oipulse_row(db_path: Path = DEFAULT_DB_PATH, prefer_memory: bool = False) -> Optional[dict]:
    """Retrieves the latest triggered OI Pulse Row 1 record, always ensuring the latest trigger is reflected."""
    in_mem = getattr(ActiveSchedulerConfig, "last_row", None)
    cache_path = db_path.parent / "oipulse_live_cache.json" if isinstance(db_path, Path) else Path(db_path).parent / "oipulse_live_cache.json"

    # Sync from live cache file if newer
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                file_row = json.load(f)
            if file_row and file_row.get("LTP") is not None and file_row.get("Strength"):
                if not in_mem or file_row.get("Trigger_Count", 0) > in_mem.get("Trigger_Count", 0) or file_row.get("id", 0) > in_mem.get("id", 0):
                    in_mem = file_row
                    ActiveSchedulerConfig.last_row = file_row
                    ActiveSchedulerConfig.trigger_count = file_row.get("Trigger_Count", 0)
        except Exception:
            pass

    if not os.path.exists(db_path):
        return in_mem if in_mem else None

    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA busy_timeout=5000;")
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oi_strength'")
        if not c.fetchone():
            conn.close()
            return in_mem if in_mem else None

        c.execute("PRAGMA table_info(oi_strength);")
        cols = [r[1] for r in c.fetchall()]
        has_tc = "Trigger_Count" in cols

        c.execute("SELECT id, Date, Time, LTP, Strength, Trigger_Time" + (", Trigger_Count" if has_tc else "") + " FROM oi_strength ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if not row:
            return in_mem if in_mem else None

        tc = row[6] if has_tc and len(row) > 6 and row[6] else None
        if tc is None or tc > 50000:
            active_tc = getattr(ActiveSchedulerConfig, "trigger_count", 0)
            if active_tc and 0 < active_tc < 50000:
                tc = active_tc
            else:
                baseline = 82680
                try:
                    conn2 = sqlite3.connect(db_path, timeout=3.0)
                    c2 = conn2.cursor()
                    c2.execute("SELECT MAX(id) FROM oi_strength WHERE Date != ?", (row[1],))
                    m = c2.fetchone()
                    conn2.close()
                    if m and m[0]:
                        baseline = m[0]
                except Exception:
                    pass
                tc = max(1, row[0] - baseline)

        db_res = {
            "id": row[0],
            "Date": row[1] or "",
            "Time": row[2] or "",
            "LTP": float(row[3]) if row[3] is not None else 0.0,
            "Strength": str(row[4]) if row[4] else "0%",
            "Trigger_Time": row[5] or "",
            "Trigger_Count": tc,
        }

        # If in_mem has a higher trigger count or higher id, prefer in_mem; otherwise use db_res and sync in_mem
        if in_mem and (in_mem.get("id", 0) > db_res["id"] or in_mem.get("Trigger_Count", 0) > db_res.get("Trigger_Count", 0)):
            return in_mem

        ActiveSchedulerConfig.last_row = db_res
        ActiveSchedulerConfig.trigger_count = tc
        return db_res
    except Exception:
        return in_mem if in_mem else None

def run_scheduler(batch_minutes: Optional[int] = None, offset_seconds: Optional[int] = None, db_path: Path = DEFAULT_DB_PATH, run_once: bool = False, market_hours_only: Optional[bool] = None, verbose: bool = True):
    if batch_minutes is not None:
        ActiveSchedulerConfig.batch_minutes = batch_minutes
    if offset_seconds is not None:
        ActiveSchedulerConfig.offset_seconds = offset_seconds
    if market_hours_only is not None:
        ActiveSchedulerConfig.market_hours_only = market_hours_only

    if verbose:
        print("=" * 65)
        print("[OI PULSE LIVE STRENGTH SCHEDULER]")
        print("=" * 65)
        print(f"Batch Time     : {ActiveSchedulerConfig.batch_minutes} minute(s)")
        print(f"Trigger Target : Every {ActiveSchedulerConfig.batch_minutes}m at :{ActiveSchedulerConfig.offset_seconds:02d}s (hh:mm:{ActiveSchedulerConfig.offset_seconds:02d})")
        print(f"Market Window  : {'Monday - Friday, 09:15 AM - 03:15 PM' if ActiveSchedulerConfig.market_hours_only else '24x7 (Anytime)'}")
        print(f"Paper Trigger  : Auto-trigger 'Execute Paper Trade' at 09:16 AM (Watching OI Strength)")
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
        auto_trig = is_paper_trade_auto_triggered(now)
        if auto_trig and not getattr(ActiveSchedulerConfig, "auto_paper_trade_triggered", False):
            ActiveSchedulerConfig.auto_paper_trade_triggered = True
            if verbose:
                print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [AUTO-TRIGGER] 09:16 AM reached -> Auto-triggering 'Execute Paper Trade' (Watching OI Strength)...\n")
        elif not auto_trig:
            ActiveSchedulerConfig.auto_paper_trade_triggered = False

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

        # Trigger execution at hh:mm:offset_seconds
        trigger_time = datetime.now()
        trigger_str = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        trigger_count += 1
        ActiveSchedulerConfig.trigger_count = trigger_count

        if verbose:
            print(f"\n[{trigger_str}] [TRIGGER #{trigger_count}] Fetching Row 1...")
        data = pulse_client.fetch_row1()

        if data:
            data["Trigger_Time"] = trigger_str
            data["Trigger_Count"] = trigger_count
            row_id = save_record_to_db(data, db_path)
            data["id"] = row_id
            ActiveSchedulerConfig.last_row = data
            ActiveSchedulerConfig.trigger_count = trigger_count
            ActiveSchedulerConfig.last_update_ts = time.time()
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
                if not is_port_open(DEBUG_PORT):
                    print(f"[!] Notice: Chrome session on port {DEBUG_PORT} not reachable. Attempting auto-launch...")
                else:
                    print(f"[!] Notice: Chrome session on port {DEBUG_PORT} is connected, but OI Pulse table not loaded yet.")
                    print("    (Please log in at https://oipulse.com/signin/ and keep the Trending OI table open)")
                print("    Will retry automatically on next trigger interval.\n")

        # Brief pause to ensure we don't double trigger in the same second
        time.sleep(1.0)

def start_oipulse_scheduler_background(batch_minutes: Optional[int] = None, offset_seconds: Optional[int] = None, db_path: Path = DEFAULT_DB_PATH, market_hours_only: Optional[bool] = None, auto_launch_chrome: bool = True) -> bool:
    """
    Starts the scheduler as a background daemon thread.
    Safe across Streamlit server reruns (checks if already running and updates config dynamically).
    """
    if batch_minutes is not None:
        ActiveSchedulerConfig.batch_minutes = batch_minutes
    if offset_seconds is not None:
        ActiveSchedulerConfig.offset_seconds = offset_seconds
    if market_hours_only is not None:
        ActiveSchedulerConfig.market_hours_only = market_hours_only

    b_min = ActiveSchedulerConfig.batch_minutes
    o_sec = ActiveSchedulerConfig.offset_seconds
    m_only = ActiveSchedulerConfig.market_hours_only

    if auto_launch_chrome:
        try:
            ensure_chrome_running(url=OIPULSE_SIGNIN_URL, port=DEBUG_PORT)
        except Exception as e:
            print(f"[!] Warning: Chrome auto-launch encountered error: {e}")

    for t in threading.enumerate():
        if t.name == _THREAD_NAME and t.is_alive():
            return True  # Already running; dynamic config updated above

    def _worker():
        try:
            window_desc = "Mon-Fri 09:15-15:15" if m_only else "24x7"
            print(f"[OI Pulse Scheduler] Background thread started (Interval: {b_min}m, Trigger: :{o_sec:02d}s, Window: {window_desc}, DB: {db_path.name})")
            run_scheduler(
                batch_minutes=b_min,
                offset_seconds=o_sec,
                db_path=db_path,
                run_once=False,
                market_hours_only=m_only,
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
    parser.add_argument("-s", "--offset", type=int, default=13, help="Offset seconds in minute to trigger on (default: 13)")
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

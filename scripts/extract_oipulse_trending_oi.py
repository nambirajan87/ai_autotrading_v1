"""
OI Pulse Trending OI Historical Extractor (Approach B: Open Session Attachment)
-------------------------------------------------------------------------------
Attaches to an already-open, authenticated Chrome browser session.
No automatic login, username, or password filling is performed.
The user logs in manually, and this script fetches the data from the open session.

Target Page: https://oipulse.com/app/options-analysis/trending-oi
Symbol: NIFTY
Interval: 3 min
Date Range: 1-Sep-2026 to 19-Oct-2026

Extracted Columns:
- Date
- Time
- LTP
- Strength
"""

import os
import sys
import time
import socket
import subprocess
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Output file path
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "data" / "nifty_trending_oi_3m_sep_oct_2026.csv"

# Known market holidays in India (Sep-Oct 2026)
KNOWN_HOLIDAYS_2026 = {
    date(2026, 10, 2),   # Gandhi Jayanti
}

DEBUG_PORT = 9222
DEBUG_ADDRESS = f"127.0.0.1:{DEBUG_PORT}"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR = os.path.expandvars(r"%USERPROFILE%\ChromeOIProfile")

def is_port_open(port=DEBUG_PORT):
    """Checks if Chrome remote debugging port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0

def ensure_chrome_running():
    """
    Checks if Chrome with remote debugging is active.
    If not, launches Chrome on port 9222 directly to OI Pulse.
    """
    if is_port_open(DEBUG_PORT):
        print(f"[OK] Detected existing Chrome session with remote debugging on {DEBUG_ADDRESS}")
        return True

    print("------------------------------------------------------------------")
    print(f"[*] Port {DEBUG_PORT} not detected. Launching Chrome with remote debugging...")
    print("------------------------------------------------------------------")
    
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "https://oipulse.com/app/options-analysis/trending-oi"
    ]
    try:
        subprocess.Popen(cmd)
        for _ in range(15):
            time.sleep(1)
            if is_port_open(DEBUG_PORT):
                print(f"[OK] Chrome successfully launched on {DEBUG_ADDRESS}")
                return True
    except Exception as e:
        print(f"[!] Error launching Chrome: {e}")
        print(f"👉 Please start Chrome manually using:")
        print(f'   "{CHROME_PATH}" --remote-debugging-port={DEBUG_PORT} --user-data-dir="{PROFILE_DIR}" "https://oipulse.com/app/options-analysis/trending-oi"')
        return False

    return False

def attach_to_chrome():
    """Attaches Selenium to the already running Chrome session."""
    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", DEBUG_ADDRESS)
    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        print(f"[!] Could not attach to Chrome on {DEBUG_ADDRESS}: {e}")
        return None

def find_or_open_oipulse_tab(driver):
    """Finds the open OI Pulse tab or navigates to Trending OI."""
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            if "oipulse.com" in driver.current_url:
                print(f"[OK] Switched to open OI Pulse tab: {driver.title} ({driver.current_url})")
                return True
        except Exception:
            pass

    print("[*] Navigating current tab to OI Pulse Trending OI...")
    driver.get("https://oipulse.com/app/options-analysis/trending-oi")
    time.sleep(3)
    return True

def wait_for_manual_login(driver):
    """
    Waits until the user completes manual login and reaches the dashboard.
    Zero automated typing or credential filling is done.
    """
    cur_url = driver.current_url
    if "/app/" in cur_url or "trending-oi" in cur_url:
        print(f"[OK] Active logged-in session confirmed on: {cur_url}")
        return True

    print("\n" + "=" * 70)
    print("👉 ACTION REQUIRED IN YOUR CHROME BROWSER:")
    print("   Please complete your manual login on OI Pulse now.")
    print("   Once logged in, open: https://oipulse.com/app/options-analysis/trending-oi")
    print("=" * 70)
    
    # Wait for the user to finish login
    while True:
        try:
            cur_url = driver.current_url
            if "/app/" in cur_url or "dashboard" in cur_url:
                print(f"[SUCCESS] Login detected! URL: {cur_url}")
                break
        except Exception:
            pass
        time.sleep(2)

    # Ensure on trending-oi page
    if "trending-oi" not in driver.current_url:
        print("[*] Directing browser to Trending OI page...")
        driver.get("https://oipulse.com/app/options-analysis/trending-oi")
        time.sleep(4)

    return True

def get_trading_days(start_date: date, end_date: date):
    """Generates all weekday dates excluding known market holidays."""
    cur = start_date
    days = []
    while cur <= end_date:
        if cur.weekday() < 5 and cur not in KNOWN_HOLIDAYS_2026:
            days.append(cur)
        cur += timedelta(days=1)
    return days

def set_date_and_interval(driver, target_date: date):
    """
    Selects the target date and interval in the OI Pulse UI.
    Supports Vue inputs, datepickers, and standard date input controls.
    """
    d_iso = target_date.strftime("%Y-%m-%d")
    d_dmy = target_date.strftime("%d-%m-%Y")
    
    js_code = f"""
    var targetDateIso = '{d_iso}';
    var targetDateDmy = '{d_dmy}';
    var changed = false;

    // 1. Look for date inputs
    var inputs = document.querySelectorAll('input');
    for (var i = 0; i < inputs.length; i++) {{
        var inp = inputs[i];
        var isDate = inp.type === 'date' || 
                     (inp.className && inp.className.toLowerCase().includes('date')) || 
                     (inp.placeholder && inp.placeholder.toLowerCase().includes('date'));
        if (isDate) {{
            inp.value = targetDateIso;
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
            changed = true;
        }}
    }}

    // 2. Look for flatpickr or custom datepicker
    if (window._flatpickr) {{
        try {{
            window._flatpickr.setDate(targetDateIso, true);
            changed = true;
        }} catch(e) {{}}
    }}

    // 3. Ensure 3-min interval if interval dropdown/buttons are present
    var buttons = document.querySelectorAll('button, .btn, .dropdown-item, label');
    for (var b = 0; b < buttons.length; b++) {{
        var txt = buttons[b].innerText.trim().toLowerCase();
        if (txt === '3 min' || txt === '3m' || txt === '3 mins') {{
            buttons[b].click();
            break;
        }}
    }}

    return changed;
    """
    try:
        driver.execute_script(js_code)
    except Exception as e:
        print(f"      Date selection notice: {e}")

def extract_4_columns(driver, current_date_str=None, row1_only=False):
    """
    Extracts the 4 columns requested:
    - Date
    - Time
    - LTP
    - Strength
    
    Uses exact DOM selectors from OI Pulse Vue Good Table:
    - Row line number: th.line-numbers
    - Strength pill: .strength-pill__num
    - Time: td[1]
    - LTP: td[2]
    """
    js_extract = f"""
    var results = [];
    var row1Only = {'true' if row1_only else 'false'};
    var defaultDate = '{current_date_str or ""}';

    var trs = document.querySelectorAll('tbody tr');
    for (var r = 0; r < trs.length; r++) {{
        var tr = trs[r];
        var lineTh = tr.querySelector('th.line-numbers');
        var lineNum = lineTh ? lineTh.innerText.trim() : (r + 1).toString();

        if (row1Only && lineNum !== '1') {{
            continue;
        }}

        var tds = tr.querySelectorAll('td');
        if (tds.length >= 3) {{
            var rDate = (tds.length > 0 && tds[0]) ? tds[0].innerText.trim() : defaultDate;
            var rTime = (tds.length > 1 && tds[1]) ? tds[1].innerText.trim() : '';
            var rLtp = (tds.length > 2 && tds[2]) ? tds[2].innerText.trim().replace(/,/g, '') : '';
            
            // Extract strength directly from .strength-pill__num
            var pill = tr.querySelector('.strength-pill__num');
            var rStrength = '';
            if (pill) {{
                // Replace unicode minus \\u2212 with ASCII hyphen -
                rStrength = pill.innerText.trim().replace(/\\u2212/g, '-');
            }} else if (tds.length > 7 && tds[7]) {{
                rStrength = tds[7].innerText.trim().replace(/\\u2212/g, '-');
            }}

            if (rTime || rStrength) {{
                results.push({{
                    Row: lineNum,
                    Date: rDate || defaultDate,
                    Time: rTime,
                    LTP: rLtp,
                    Strength: rStrength
                }});
            }}

            if (row1Only && lineNum === '1') {{
                break;
            }}
        }}
    }}
    return results;
    """
    try:
        records = driver.execute_script(js_extract)
        if records and isinstance(records, list):
            return records
    except Exception as e:
        print(f"      Extraction notice: {e}")
        return []

def main():
    print("==================================================================")
    print("OI PULSE TRENDING OI HISTORICAL EXTRACTOR (OPEN SESSION ATTACH)")
    print("==================================================================")
    print("Columns: Date, Time, LTP, Strength")
    print("Interval: 3 min | Symbol: NIFTY")
    print("Range: 1-Sep-2026 to 19-Oct-2026")
    print("Output: data/nifty_trending_oi_3m_sep_oct_2026.csv\n")

    # 1. Ensure Chrome is running with debugging port
    if not ensure_chrome_running():
        print("[!] Cannot connect to Chrome. Exiting.")
        return

    # 2. Attach to open Chrome session
    driver = attach_to_chrome()
    if not driver:
        print("[!] Failed attaching to Chrome session. Exiting.")
        return

    # 3. Locate or open OI Pulse tab
    find_or_open_oipulse_tab(driver)

    # 4. Confirm user is manually logged in
    wait_for_manual_login(driver)

    # Check if user requested current active tab row 1 only
    if "--current" in sys.argv or "--row1" in sys.argv:
        print("\n[*] Fetching Row 1 from currently active OI Pulse view...")
        r1 = extract_4_columns(driver, row1_only=True)
        if r1:
            data = r1[0]
            print("------------------------------------------------------------------")
            print("OI PULSE ROW 1 EXTRACTED DATA:")
            print("------------------------------------------------------------------")
            for k in ["Row", "Date", "Time", "LTP", "Strength"]:
                if k in data:
                    print(f"  {k:10}: {data[k]}")
            print("------------------------------------------------------------------")
            return
        else:
            print("[!] Could not find Row 1 in current view.")
            return

    # 5. Date list
    start_date = date(2026, 9, 1)
    end_date = date(2026, 10, 19)
    trading_days = get_trading_days(start_date, end_date)
    print(f"\n[OK] Identified {len(trading_days)} trading days to extract ({start_date} to {end_date})\n")

    # 6. Load existing data if resuming
    all_data = []
    done_dates = set()
    if OUTPUT_CSV.exists():
        try:
            df_old = pd.read_csv(OUTPUT_CSV)
            if not df_old.empty and "Date" in df_old.columns:
                done_dates = set(df_old["Date"].astype(str).unique())
                all_data = df_old.to_dict("records")
                print(f"[INFO] Resuming from existing CSV: {len(df_old)} rows across {len(done_dates)} dates already saved.")
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("🚀 STARTING EXTRACTION LOOP...")
    print("   Ensure 'NIFTY' and '3 min' are selected on the page.")
    print("=" * 70 + "\n")

    for idx, t_date in enumerate(trading_days, 1):
        d_str = t_date.strftime("%Y-%m-%d")
        if d_str in done_dates:
            print(f"[{idx}/{len(trading_days)}] {d_str} already extracted. Skipping.")
            continue

        print(f"[{idx}/{len(trading_days)}] Fetching Date: {d_str}...")
        
        # Change date in UI
        set_date_and_interval(driver, t_date)
        time.sleep(2.5)

        # Extract the 4 columns
        rows = extract_4_columns(driver, d_str)
        if rows:
            print(f"   -> Extracted {len(rows)} intervals for {d_str}")
            all_data.extend(rows)
            done_dates.add(d_str)

            # Flush to CSV incrementally
            OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
            df_flush = pd.DataFrame(all_data)[["Date", "Time", "LTP", "Strength"]].drop_duplicates()
            df_flush.to_csv(OUTPUT_CSV, index=False)
        else:
            print(f"   -> No rows detected for {d_str} (market holiday / no session data).")

        time.sleep(1.0)

    # 7. Final Summary
    if all_data:
        df_final = pd.DataFrame(all_data)[["Date", "Time", "LTP", "Strength"]].drop_duplicates()
        df_final.to_csv(OUTPUT_CSV, index=False)
        print("\n==================================================================")
        print("🎉 EXTRACTION COMPLETED SUCCESSFULLY!")
        print(f"Total Rows Saved: {len(df_final):,}")
        print(f"Output File: {OUTPUT_CSV}")
        print("==================================================================")
        print(df_final.head(10))
    else:
        print("\n[!] No records could be extracted. Please check the browser view.")

if __name__ == "__main__":
    main()

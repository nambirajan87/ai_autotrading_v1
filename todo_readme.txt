You are the AI Functional QA Agent for an Indian F&O trading application.

The application has three modules: Backtest, Paper Trade, and Live Trade.

Use the existing Python, pytest, and Playwright test infrastructure to audit the application.

For each module:

1. Inspect the available UI, source code, API endpoints, business rules, and existing tests.
2. Create a comprehensive test matrix covering positive, negative, boundary, calculation, integration, persistence, recovery, and regression scenarios.
3. Execute safe, repeatable tests and capture actual evidence.
4. Compare actual behavior against documented requirements and independently calculated expected results.
5. Distinguish confirmed defects, suspected defects, missing requirements, and improvement suggestions.
6. Record reproduction steps, expected behavior, actual behavior, severity, evidence, and acceptance criteria.
7. Save screenshots, test logs, and machine-readable test results.
8. Generate a JSON defect register and a detailed Markdown functional audit report suitable for a separate coding agent.

Never fabricate results. Never mark unexecuted tests as passed. Do not modify application source code during the audit.

Use mock or sandbox broker interfaces for Live Trade tests. Do not submit real orders, alter real positions, or risk real funds.

The final output must enable another agent to fix one issue at a time and verify every fix using automated regression tests.
================================================
python scripts/extract_oipulse_trending_oi.py
.\launch_chrome_for_oipulse.bat

================================================
How to Use It
Step 1: Open Chrome for Manual Login
Double-click 

launch_chrome_for_oipulse.bat
 (or run in PowerShell/terminal):

bash
.\launch_chrome_for_oipulse.bat ######################################
(This launches Chrome with port 9222 enabled so Python can communicate with it).

Step 2: Log in Manually
In the opened Chrome window, sign in with your OI Pulse account.
Navigate to the Trending OI page: https://oipulse.com/app/options-analysis/trending-oi
Ensure NIFTY and 3 min are selected.
Step 3: Run the Extraction Script
In your terminal, execute:

bash
python scripts/extract_oipulse_trending_oi.py
The script will attach to your open tab, cycle through all dates from 1-Sep-2026 to 19-Oct-2026, extract the Date, Time, LTP, and Strength columns, and save the final dataset to: data/nifty_trending_oi_3m_sep_oct_2026.csv

================================================
<th class="line-numbers">1</th>
<span data-v-3d44062a="" class="strength-pill__num">-109%</span>

can you create a table called: oi_strength
Load all data into this table, and create columns as in file header.

python scripts/load_oi_strength_to_db.py ###################################
================================================ CRONJOB TRIGGER STARTS
Option 1: 3-Minute Interval (Triggers at :10s)
bash
python scripts/schedule_oipulse_strength.py --batch 3
Option 2: 1-Minute Interval (Triggers at :10s)
bash
python scripts/schedule_oipulse_strength.py --batch 1
Option 3: Immediate Single Check
bash
python scripts/schedule_oipulse_strength.py --once

=================================================================
Live Console Output Preview
text
=================================================================
[OI PULSE LIVE STRENGTH SCHEDULER]
=================================================================
Batch Time     : 3 minute(s)
Trigger Target : Every 3m at :10s (hh:mm:10)
Attached Port  : 9222
Output CSV     : C:\AI Course\ai_autotrading_v1\data\oipulse_live_stream.csv
=================================================================
>> Scheduler is active! Press Ctrl+C to stop.
[2026-10-21 00:48:10] [TRIGGER #1] Fetching Row 1...
+-------------------------------------------------------------+
| Trigger : 2026-10-21 00:48:10                               |
| Row     : 1                                                 |
| Date    : 19-10-2026                                        |
| Time    : EOD                                               |
| LTP     : 22888.95                                          |
| Strength: -109%                                             |
+-------------------------------------------------------------+
   -> Saved to oipulse_live_stream.csv
[WAITING] Next trigger at 00:51:10 (in 179.8s)...

Manual Override Options
Run Once Anytime: python scripts/schedule_oipulse_strength.py --once 
(fetches Row 1 and inserts into oi_strength immediately regardless of market hours for testing).
Run Continuous 24x7 (Ignore Market Hours): python scripts/schedule_oipulse_strength.py --anytime
================================================ CRONJOB TRIGGER ENDS


================================================ 

def find_value(LTP, current_day, OIStrength):
    # Step 1: Calculate the 50-point bounds
    previous_bound = ((LTP - 1) // 50) * 50 + 1
    next_bound = previous_bound + 49

    # Step 2: Define weekday adjustments
    adjustments = {
        "Wednesday": 350,
        "Thursday": 250,
        "Friday": 150,
        "Monday": 0,
        "Tuesday": -100
    }

    # Step 3: Validate current day
    current_day = current_day.strip().capitalize()

    if current_day not in adjustments:
        print("Invalid day. Enter Monday to Friday.")
        return None, None

    # Step 4: Determine OIStrengthResult
    if OIStrength > 50:
        OIStrengthResult = "CE"
    elif OIStrength < -50:
        OIStrengthResult = "PE"
    else:
        OIStrengthResult = "No Signal"

    # Step 5: Calculate LTPresult based on signal and weekday
    adjustment = adjustments[current_day]

    if OIStrengthResult == "CE":
        LTPresult = previous_bound + adjustment - 1

    elif OIStrengthResult == "PE":
        LTPresult = previous_bound - adjustment - 1

    else:
        LTPresult = None

    # Step 6: Print results
    print("LTPresult        :", LTPresult)
    print("OIStrengthResult :", OIStrengthResult)

    return LTPresult, OIStrengthResult


# Dynamic user inputs
LTP = int(input("Enter LTP: "))
current_day = input("Enter Current Day: ")
OIStrength = int(input("Enter OIStrength: "))

find_value(LTP, current_day, OIStrength)

================================================

Need to fix in all 3 apps: backtest,paper,live
In strategy & Config Rules page, in Instrument Configuration section:
in (NSE F&O (Options & Futures) [Kotak Live Priority]) mode selection: 
i need to add 2 check box: (But anyone should be selected t a time)
1 with Normal Mode: 
2 with OIPulse Mode:
If "OIPulse Mode" is selected, then only :
"Underlying Index / Stock" should be displayed
"Rolling Expiry Date (Kotak Live)
" should not be displayed
"Option Type"(PE/CE) both should not be displayed
"Strike Price" should not be displayed
finally  "Active Selection" will become: NIFTY
(we are going to find it out dynamically using our oi_strength table, i will explain)
If "Normal Mode" is selected, then:
"Underlying Index / Stock" should be displayed
"Rolling Expiry Date (Kotak Live)
" should be displayed
"Option Type"(PE/CE) both should be displayed
"Strike Price" should be displayed
 finally Active Selection will be as usual: NIFTY26OCT22900PE | Expiry: 27-OCT-2026 | Key: nse_fo|51349 | Lot: 65

So here the key is: if "OIPulse Mode" is selected then we are going to apply this below plan to identify the "Expiry Date", "Option Type" and "Strike Price" dynamically.
can you read through the attached file, which is getting 3 input values:
LTP: need to fetch it from the LTP column in oi_strength table. (where date and Time should be equal to candle date time)
OIStrength: need to fetch it from the Strength column in oi_strength table. (-109%) (where date and Time should be equal to candle date time)
current_day: (
backtest: need to use the day of the Historical data vs backtest execution selected date
Paper: need to use the current day
Live: need to use the current day)
The output result of the attached file is;
strikePriceResult
optionTypeResult
And to identify the Expiry date: using the current date we have to identify the nearest Expiry date.
 So finally we have to derive Active Selection: NIFTY26OCT22900PE, and then using this we have to take trade .

Before fixing can you plan first and after my confirmation start fixing it
================================================
\ai_autotrading_v1 > python scratch/test_bt_exec.py
Total raw records: 11913
Signal value counts with interpolated 1m candles:
HOLD    7504
LONG      77
Name: count, dtype: int64
Traceback (most recent call last):
  File "C:\AI Course\ai_autotrading_v1\scratch\test_bt_exec.py", line 62, in <module>
    cfg_both = CustomStrategyConfig(
               ^^^^^^^^^^^^^^^^^^^^
NameError: name 'CustomStrategyConfig' is not defined
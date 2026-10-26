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
================================================
C:\AI Course\ai_autotrading_v1\scripts\schedule_oipulse_strength.py
    market_hours_only = False
start_oipulse_scheduler_background(batch_minutes: int = 1, offset_seconds: int = 10, db_path: Path = DEFAULT_DB_PATH, market_hours_only: bool = False) -> bool:
Line no:377
================================================

========================================================= Task1 only for Paper and Live app
[2026-10-25 18:37:10] [TRIGGER #27] Fetching Row 1...
+-------------------------------------------------------------+
| Trigger   : 2026-10-25 18:37:10                             |
| DB Row ID : 82707                                           |
| Date      : 23-10-2026                                      |
| Time      : EOD                                             |
| LTP       : 22600.00                                        |
| Strength  : -117%                                           |
+-------------------------------------------------------------+

Need to add this new section as first under the "Current Date Paper Trade" section : Make plan and do proper ui/ux alignments with new section and existing section, as required.
OI Pulse Section:
Trigger no: #27
Trigger Time: [2026-10-25 18:37:10] 
OI Pulse Date: 23-10-2026
OI Pulse Time: 12:15
OI Pulse LTP: 22600.00 
OI Pulse Strength: -117%
Values here are sample: But need to update the dynamic values here based on each 1 min backend triggered data from oipulse.

========================================================= Task2 only for Paper and Live app
Live Tick (LTP)
Day High
Day Low
Candles Today
Live Current Date Chart 

these 5 items should be displayed/enabled only after constructing the Active Instrument (as per working "OI Pulse logic": is decided based on the OI Pulse Strength>CE/PE>OI Pulse LTP>Strike price)
as per "OI Pulse logic", the Active Instrument will be keep rotating or keep rolling, so based on that above 5 items should be handled. plan and fix accordinlgy.
========================================================= Task3 for all 3 apps
Remove Historical Raw Data (All Records) sections 


For this PAPERTRADE_TEST_OI_V1 strategy, date range is: Period: 2026-10-25 to 2026-10-25, so expecting the latest batch run data, and should be change for every minute(always keep sync oi-ui with oi-batch for every min) 
Also change the value font size(very bigger, unable to read) to same as label font size(normal size)
Trigger no:
#82812 (Not changing dynamically for every min)
Trigger Time:
[2026-10-25 20:22:10]
OI Pulse Date:
23-10-2026
OI Pulse Time:
EOD
OI Pulse LTP:
?22,600.00
OI Pulse Strength:
-117%
======================================================
NIFTY26OCT22400PE
NIFTY26OCT22400PE
whether this is a valid instrument
9:43 PM
Yes, NIFTY26OCT22400PE is a valid F&O option contract.

Instrument Specification Breakdown
Component	Value	Description
Underlying	NIFTY	NSE Nifty 50 Benchmark Index
Expiry Code	26OCT	October 2026 monthly expiry (27-OCT-2026, Tuesday)
Strike Price	22400	Valid exchange strike (multiple of Nifty's 50-pt strike step)
Option Type	PE	European Put Option
Lot Size	65	Standard NSE Nifty contract lot size
Tick Size	?0.05	Minimum price movement
How It Was Derived via OI Pulse Logic Pipeline
LTP: ?22,600.00
Day Adjustment: Friday = +150
OI Pulse Strength: -117% (Bearish Momentum < -50% ? PE)
Strike Calculation for PE: $$\text{Previous Bound} = \left(\left\lfloor\frac{22600 - 1}{50}\right\rfloor \times 50\right) + 1 = 22551$$ $$\text{Strike} = 22551 - 150 - 1 = \mathbf{22400}$$
Nearest Tuesday Expiry: 27-OCT-2026 ? 26OCT
Constructed Symbol: NIFTY + 26OCT + 22400 + PE = NIFTY26OCT22400PE
Verified via 

validate_oipulse_contract_symbol
 (Valid: True).
 ======================================================
 
 =============================================================================
 can you give work flow chart or kind of mind map, for illiustrating one single trade between "OI pulse strength" and "Trade Close"
 2:00 PM
 Here is a complete workflow chart and mind map illustrating the lifecycle of a single trade—from the moment OI Pulse Strength is evaluated to the final Trade Close.
 
 1. Visual Trade Workflow (Flowchart)
 mermaid
 flowchart TD
     %% Styling
     classDef startEnd fill:#1e293b,stroke:#64748b,stroke-width:2px,color:#fff
     classDef trigger fill:#0284c7,stroke:#38bdf8,stroke-width:2px,color:#fff
     classDef decision fill:#d97706,stroke:#f59e0b,stroke-width:2px,color:#fff
     classDef execution fill:#16a34a,stroke:#4ade80,stroke-width:2px,color:#fff
     classDef monitor fill:#4f46e5,stroke:#818cf8,stroke-width:2px,color:#fff
     classDef exit fill:#dc2626,stroke:#f87171,stroke-width:2px,color:#fff
     subgraph Phase1 ["Phase 1: Scheduler & Signal Detection"]
         T13["Trigger at :13s (Every Minute)<br/>get_next_trigger()"]:::trigger
         Fetch["Fetch Latest OI Pulse Row 1<br/>(In-Memory Cache / DB fallback)"]
         EvalSignal{"Evaluate Strength<br/>vs Previous State"}:::decision
         T13 --> Fetch --> EvalSignal
     end
     subgraph Phase2 ["Phase 2: Confirmation & Strike Setup"]
         EvalSignal -->|"Strength != Bullish/Bearish<br/>or No Signal"| NoTrade["Sleep until next :13s"]:::startEnd
         EvalSignal -->|"Strong Bullish / Bearish"| CheckFilter{"Kotak Chart Filter<br/>(Price > EMA / VWAP / Breakout?)"}:::decision
         CheckFilter -->|No Alignment| Skip["Skip (Filter Rejection)"]:::startEnd
         CheckFilter -->|Aligned| SelectStrike["Select Strike & Contract<br/>• CE for Bullish / PE for Bearish<br/>• Calculate ATM / ITM strike<br/>• Set Initial SL & Target"]
     end
     subgraph Phase3 ["Phase 3: Order Execution"]
         SelectStrike --> SendOrder["Place Paper Trade Order<br/>(Kotak / Engine simulated fill)"]:::execution
         SendOrder --> OpenPos["Position State: OPEN<br/>Record: Entry Price, Qty, Time, Initial SL"]:::execution
     end
     subgraph Phase4 ["Phase 4: In-Trade Monitoring Loop"]
         OpenPos --> TrackLTP["Kotak Live Feed Loop (Ticks / 1s)<br/>Track Option LTP & Underlying Spot"]:::monitor
         TrackLTP --> CalcPnL["Calculate Unrealized PnL<br/>(LTP - Entry) × Qty"]:::monitor
         CalcPnL --> CheckExit{"Evaluate Exit Conditions"}:::decision
         %% Exit checks
         CheckExit -->|"Target Hit (LTP >= Target)"| CloseTarget["Exit: Target Reached"]:::exit
         CheckExit -->|"Hard SL Hit (LTP <= SL)"| CloseSL["Exit: Stop-Loss Hit"]:::exit
         CheckExit -->|"Trailing Condition Met"| UpdateSL["Trail SL upward to lock profits"]:::monitor
         UpdateSL --> TrackLTP
         CheckExit -->|"Next :13s OI Reversal<br/>(Strength flips against trade)"| CloseReversal["Exit: OI Trend Invalidation"]:::exit
         CheckExit -->|"Time == 15:15"| CloseIntraday["Exit: Market Close Square-off"]:::exit
         CheckExit -->|"None Hit"| TrackLTP
     end
     subgraph Phase5 ["Phase 5: Trade Close & Post-Trade"]
         CloseTarget --> ExecuteClose["Execute Close Paper Order<br/>Position State: CLOSED"]:::execution
         CloseSL --> ExecuteClose
         CloseReversal --> ExecuteClose
         CloseIntraday --> ExecuteClose
         ExecuteClose --> Finalize["Finalize Trade Log<br/>• Net Realized PnL<br/>• Exit Reason<br/>• Duration & Trade History DB"]
         Finalize --> UIUpdate["Update Streamlit Dashboard<br/>• Metric Cards (Win/Loss, PnL)<br/>• Chart Marker / Audio Alert"]:::startEnd
     end
 2. Trade Lifecycle Mind Map
 Single Trade Lifecycle
 ¦
 +-- 1. Trigger & Signal (at :13s of minute)
 ¦   +-- Exact clock alignment (:13s offset allows candle & OI data to freeze)
 ¦   +-- In-memory extraction (ActiveSchedulerConfig.last_row)
 ¦   +-- Fields: Strength (Bullish/Bearish), LTP, PCR, Net Diff, Trigger_Count
 ¦   +-- Condition: Did strength cross threshold? (e.g., NEUTRAL ? STRONG BULLISH)
 ¦
 +-- 2. Confirmation & Risk Parameters
 ¦   +-- Kotak Price Action Filter (Does Spot agree? Above VWAP/Supertrend?)
 ¦   +-- Strike Selection (ATM / 1-Strike ITM Option)
 ¦   ¦   +-- Bullish ? NIFTY CE
 ¦   ¦   +-- Bearish ? NIFTY PE
 ¦   +-- Risk Management Rules:
 ¦   ¦   +-- Capital Allocation / Fixed Lot size
 ¦   ¦   +-- Fixed Stop-Loss (e.g., -15 pts or swing low)
 ¦   ¦   +-- Take-Profit Target (e.g., +30 pts or 1:2 Risk-Reward)
 ¦   ¦   +-- Trailing Stop Step (e.g., if +15 pts, move SL to Cost)
 ¦
 +-- 3. Order Placement & State Entry
 ¦   +-- State Transition: WAITING ? ORDER_PLACED ? IN_POSITION
 ¦   +-- Timestamp, strike symbol, order side (BUY), filled price recorded
 ¦   +-- Active monitoring flag activated
 ¦
 +-- 4. Active In-Position Engine (High-frequency loop)
 ¦   +-- Continuous stream of Kotak ticks / Option LTP
 ¦   +-- Real-time Unrealized PnL = (Current LTP - Entry Price) × Quantity
 ¦   +-- Trailing SL Engine (Dynamic ratchet: moves up, never moves down)
 ¦   +-- Concurrent OI Pulse monitor (Next minute's :13s check)
 ¦
 +-- 5. Exit Decision Hierarchy (First to trigger wins)
 ¦   +-- Condition A: Target Hit ? Profit exit
 ¦   +-- Condition B: Trailing SL Hit ? Locked-profit exit
 ¦   +-- Condition C: Hard Stop-Loss Hit ? Risk protection exit
 ¦   +-- Condition D: OI Trend Reversal ? Early exit if OI strength turns opposite
 ¦   +-- Condition E: Time Cut-off ? Mandatory square-off at 15:15 IST
 ¦
 +-- 6. Trade Close & Accounting
     +-- State Transition: IN_POSITION ? CLOSED
     +-- Realized PnL & Trade Duration logged to SQLite / Memory
     +-- Streamlit table update (Trade log row appended)
     +-- Strategy returns to WAITING state for next valid :13s trigger
     
=============================================================================    
Instrument and Index-LTP 2 columns are missing in "Trade Audit Trail" and "Trade Audit Trail-2" sections

Create a new App called "Mock Live"
create a mock test data similar to OI Pulse: 
Todays date is 26-10-2026
Time should be each minute cover all 60 minutes*24 hours=1440 minutes.
LTP should be start from 20,000 and 30,000
create Mock data: 
like keep LTP range between 20,000 to 20,050 for every 5 minutes and keep increase 50 points for each next 5 minutes(During this upside keep Strength:>60 range), if it reaches 30,000, then keep reducing 50 points and go upto 20,000 (During this downside keep Strength: <-60 range)
total 1440 records should be created that is every 1440 minutes. 
create a csv filr review, and after my approval will create into db.
CSV Columns are:
Date, Time, LTP, Strength

=================================================================
[OI PULSE LIVE STRENGTH SCHEDULER]    
=================================================================
Batch Time     : 1 minute(s)
Trigger Target : Every 1m at :13s (hh:mm:13)
Market Window  : Monday - Friday, 09:15 AM - 03:15 PM
Attached Port  : 9222
Target DB      : C:\AI Course\ai_autotrading_v1\data\backtest.db -> table 'oi_strength'
==================================================================
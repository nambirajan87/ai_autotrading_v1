Backtest no 86: have good results.

Intraday Candle Data: 
https://upstox.com/developer/api-documentation/get-intra-day-candle-data/

Historical Candle Data Deprecated
https://upstox.com/developer/api-documentation/get-historical-candle-data/#sample-code-python

Upstox Token:
Set your UPSTOX_ACCESS_TOKEN in .env (refer to .env.example)

Supported APIs:
https://upstox.com/developer/api-documentation/analytics-token/#supported-apis

KOTAK APP TOKEN:
Set your KOTAK_CONSUMER_KEY in .env (refer to .env.example)

KOTAK ENDPOINTS:
https://github.com/Kotak-Neo/Kotak-neo-api-v2

===============================================================
LOAD DATA from upstox:
PS C:\AI Course\ai_autotrading> python -m app.data.upstox_loader

===============================================================
Rule		Bullish					                Bearish
EMA trend	EMA20 > EMA50: +2			            EMA20 < EMA50: -2
VWAP		close > VWAP: +1			            close < VWAP: -1
RSI		    55 to 70: +1				            30 to 45: -1
MACD		histogram > 0: +2			            histogram < 0: -2
ADX		    ADX ≥ 20 and +DI > -DI: +1      		ADX ≥ 20 and -DI > +DI: -1
Volume		consecutive trend + ratio ≥ 1.2: +1     consecutive trend + ratio ≥ 1.2: -1
Bollinger	mid < close ≤ upper: +1                 lower ≤ close < mid: -1
Stochastic	20 ≤ %K ≤ 80 and %K > %D: +1            20 ≤ %K ≤ 80 and %K < %D: -1
OBV		    OBV > OBV_MA: +1                        OBV < OBV_MA: -1
Higher High	Current High > Previous Swing High: +1  (Indicator 11: Locks to LONG only trades)
Lower Low	(Indicator 12: Locks to SHORT only)     Current Low < Previous Swing Low: -1

RSI above 70 or below 30 scores 0. The reason is that I treat extreme RSI as "stretched, don't chase". That is a design choice, not a fact, and it's exactly the kind of thing the backtest should test.

https://www.biz4group.com/blog/ai-paper-trading-app-development#id41

===============================================================
Backtest :
python -m app.backtest.runner
python -m pytest -v

python -m app.backtest.runner > data\backtest_report.txt
python -m app.backtest.runner | more

python -m app.backtest.analyze data/backtest_trades.csv > data\backtest_analyse_report.txt

python -m app.backtest.seed_check
===============================================================

Most exits should be STOP, TARGET and SQUARE_OFF.
can you give one formula how this each trade is found and added in csv.?
===============================================================
Try it yourself
python -c "from app.data.regime_simulator import generate_regime_sample as g; print(g(seed=7)['close'].iloc[[0,100,500]].values); print(g(seed=6)['close'].iloc[[0,100,500]].values); print(g(seed=7)['close'].iloc[[0,100,500]].values)"
You'll see seed 7 and seed 6 give different prices, and the second seed 7 call repeats the first exactly.
===============================================================
stop distance   = atr_stop_multiplier (1.5) × ATR
target distance = reward_risk_ratio (2.0) × stop distance = 3 × ATR

LONG:  stop = close - 1.5×ATR    target = close + 3×ATR
SHORT: stop = close + 1.5×ATR    target = close - 3×ATR

ATR (Average True Range)

ATR measures how much a price typically moves per candle. It is a volatility gauge, in price units (rupees), and it says nothing about direction.

How it's calculated

For each candle, first compute the True Range: the largest of these three numbers.

text
1. high - low                    (the candle's own range)
2. |high - previous close|       (captures an upward gap)
3. |low  - previous close|       (captures a downward gap)

Items 2 and 3 are there so overnight gaps count as movement. Then ATR is a smoothed average of True Range over 14 candles (Wilder smoothing, as in atr.py).

Tiny example

Previous close 2500. The new candle has high 2510 and low 2504.

text
high - low         = 6
|2510 - 2500|      = 10   ← largest
|2504 - 2500|      = 4
True Range         = 10

The candle's own range was only 6, but price jumped 10 from the previous close, so True Range is 10.

What ATR = 2.64 means

In your signal output, atr: 2.6432 with a close of 2450.99 means the price has been moving about ₹2.64 per 5-minute candle recently (about 0.1%). A higher ATR means a more volatile market, and a lower one means a quieter market.

Where we use it
Stop-loss: stop distance = 1.5 × ATR. In calm markets the stop is tight, and in wild markets it's wide, so you aren't stopped out by normal noise.
Target: 2 × the stop distance (3 × ATR).
Position sizing: a wider stop means fewer shares for the same risk amount (quantity = risk amount / stop distance).
Why not a fixed stop

A fixed ₹5 stop might be far too wide on a quiet day and far too tight on a volatile one. ATR adapts the stop to current conditions.

Limits
ATR is backward-looking. A sudden news spike can exceed it, which is why STOP_GAP exits exist.
It's not a prediction of the next candle's size, only a recent typical size.
Its value depends on timeframe: ATR on 5-minute candles is much smaller than on daily candles.
===============================================================
python -m app.execution.paper_broker
python -m pytest tests/test_paper_broker.py -v
python -m pytest -v

python -m app.execution.paper_trader
python -m pytest tests/test_paper_trading.py -v
python -m pytest -v
===============================================================
To inspect the audit trail for one trade, answering "why did it enter, and was the risk check approved?":

powershell
python -c "from app.database.database import Database; db=Database('data/paper_trading.db'); [print(r) for r in db.query('SELECT t.id, t.side, t.score, s.reasons_json, r.reason, r.risk_amount FROM trades t JOIN signals s ON s.id=t.signal_id JOIN risk_events r ON r.signal_id=t.signal_id ORDER BY t.id LIMIT 3')]"

Each run appends a new run_id to data/paper_trading.db. Delete the file to start fresh.
===============================================================
Query data from DB:
PS C:\AI Course\ai_autotrading> python -c "from app.database.database import Database; db=Database('data/paper_trading.db'); print(db.query('SELECT run_id, status, COUNT(*) AS n FROM orders GROUP BY run_id, status'))"
===============================================================
LOADER DATA COMMANDS:
1. To LOAD six month data of Reliance:
    PS C:\AI Course\ai_autotrading> python -m app.data.reliance_download_history "NSE_EQ|INE002A01018" 2026-04-01 2026-10-05 data/reliance_5m.csv

2. Run backtest Python file:
    python -m app.backtest.upstox_backtest data/reliance_5m.csv
===============================================================
===============================================================
3. Run UNIFIED ALL-IN-ONE Trading Suite (Live + Paper + Backtest + Auth):
    streamlit run main_app.py
    (or streamlit run app/ui/main_app.py --server.port 8501)
    
    Security & Authentication:
    - User Database: SQLite data/users.db (PBKDF2-HMAC-SHA256 encrypted, 100k rounds)
    - Default Credentials: admin / admin123 (or Register any new account on Sign Up tab)
    - Full Signup, Login, and Session Logout support
    - Master Sidebar Navigator to effortlessly switch between:
        1. ⚡ Live Trading Terminal
        2. 📈 AI Paper Trading Studio
        3. 🧪 Strategy Backtesting Studio

    Standalone App Launchers (Optional):
    streamlit run app/ui/backtest_app.py --server.port 8501
    streamlit run app/ui/paper_trade_app.py --server.port 8502
    streamlit run app/ui/live_trade_app.py --server.port 8503

4. Run AI Paper Trading Studio (Candle Replay Simulator & Persistent Trade Journal):
    streamlit run app/ui/paper_trade_app.py
    Features:
    - Mode A: Paper Trade Setup
      * 1. Setup & Configuration (Instrument, Strategy, Lookback/Date)
      * 2. Interactive Trading Desk (Candle step, Auto-play, Run All, Live P&L, Chart)
      * 3. Analytics & Trade Log (Session equity curve, trade ledger, CSV export)
    - Mode B: Paper Trading History
      * Strategy-List Dropdown (Persistent across server restarts via SQLite data/paper_trading.db)
      * Full Historical Executed Trade Ledger mapped to the chosen strategy
      * Cumulative Net P&L Curve & Trade Distribution Bar Chart
      * Performance Metrics (Win Rate, Profit Factor, Gross & Net Realized P&L, Charges)
      * Session Runs History & Benchmark Comparison Across All Strategies
      * Symbol & Outcome (Wins/Losses) Filters, plus CSV/JSON Download buttons
===============================================================

Run All Pytest Tests:

powershell
python -m pytest -v
===============================================================
Local URL:
http://localhost:8501/

Public URL:
your url is: https://sad-hands-wish.loca.lt

First-time access note: When you open that link, it will ask for a "Tunnel Password". Your tunnel password is your public IP address. You can get it by running:
C:\Users\NAMBI>curl.exe https://loca.lt/mytunnelpassword
27.62.32.44
===============================================================
Option 2: Cloudflare Tunnel (Fastest & Most Reliable, No Sign-up)
Cloudflare provides free, high-speed, secure HTTPS tunnels without requiring an account:

Install Cloudflare CLI via Windows Package Manager:
powershell
        winget install --id Cloudflare.cloudflared
Start the tunnel to your Streamlit port:
powershell
        cloudflared tunnel --url http://localhost:8501
Look for the line in the terminal output that says:
text
https://xxxx-xxxx-xxxx.trycloudflare.com
Anyone with this URL can view and interact with your app.

^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ (WORKING FINE)
                Option 1: Run with the full path (Immediate)
                In your PowerShell terminal, run:

                powershell
                & "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8501
===============================================================
Option 3: Ngrok (Industry Standard)
If you already use or prefer Ngrok:

Install ngrok via winget:
powershell
        winget install ngrok
Connect your free auth token (from ngrok.com):
powershell
        ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN_HERE

Forward port 8501:
powershell
        ngrok http 8501

        # With your static domain (recommended):
        ngrok http --url=your-domain.ngrok-free.dev 8501

        # If ngrok command is not found in an existing terminal session, use full path:
        & "<Path-To-Ngrok>\ngrok.exe" http --url=your-domain.ngrok-free.dev 8501



TIP

Streamlit CORS / WebSocket Warning: If you see a warning about WebSocket connections when accessing via the public URL, you can run Streamlit with CORS bypass enabled:

powershell
streamlit run app/ui/backtest_app.py --server.enableCORS false --server.enableXsrfProtection false
===============================================================
Server startup command:
 streamlit run app/ui/backtest_app.py

   You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://<LAN_IP>:8501
===============================================================
Access & Audit Logging (Localhost & Public URL)
===============================================================
The application automatically tracks and audits every visitor session and key actions across:
- Localhost (127.0.0.1)
- Local Area Network / Wi-Fi (192.168.x.x)
- Ngrok Public Tunnel (*.ngrok-free.dev)
- Cloudflare Tunnel (*.trycloudflare.com)

Logs Created:
- logs/audit.log     : Human-readable timestamped access trail
- logs/audit.jsonl   : Structured JSON Lines format for querying/metrics

What is captured:
- Timestamp (Local & UTC ISO)
- Access Source (Localhost, Public Tunnel Ngrok, LAN, etc.)
- Real Client IP (resolves X-Forwarded-For, CF-Connecting-IP, etc.)
- Host header & Protocol (http / https)
- Browser, OS, and Device (parsed from User-Agent)
- Session ID & Event (SESSION_START, BACKTEST_INITIATED, BACKTEST_COMPLETED)

How to view audit logs:
1. In the Streamlit UI: Open the sidebar expander "🛡️ Access & Audit"
2. In PowerShell terminal:
   powershell
   python -m app.monitoring.audit_logger -n 20       # View last 20 access entries
   python -m app.monitoring.audit_logger --summary   # Summary statistics & IP breakdown
   Get-Content logs\audit.log -Tail 20               # Live raw log file
===============================================================
#86 | NIFTY26OCT22500PE | 2026-09-01 to 2026-10-07 | 10-08 07:55 
#93 | NIFTY26OCT22500PE | 2026-09-01 to 2026-10-07 | 10-08 16:24 (After Removing SHORT, Net P&L (₹)34,396.05)

===============================================================

4. Other Guardrails Active Alongside Daily Loss
In addition to the daily loss limit, the following risk rules are enforced concurrently:

Max Consecutive Losses: Default 3 losses in a row halts trading for the day.
Risk Per Trade: Default 1.0% of total capital per position.
Stop-Loss per Trade: Default 2.0% (or ATR dynamic stop).
Max Open Positions: Default 1 position at a time.
Intraday Square-off: Automatic exit at 15:15 IST.

===============================================================
SCORE:
5. Leveraging Your Existing Confluence Engine: "The Milestone Candle Score"
Your codebase already implements a multi-factor scoring engine in 

app/strategy/scoring.py
 that evaluates:

EMA 20 vs 50 Trend (+2 points)
VWAP alignment (+1 point)
RSI band (+1 point)
MACD Histogram (+2 points)
ADX trend (+1 point)
Volume ratio (+1 point) (Maximum Bullish Confluence Score = +8)
===============================================================

===============================================================
TRADE AUDIT TRAIL & TRADE AUDIT TRAIL-2:
- Column "peak_gain" (formerly Stop_Risk): displays the crossed percentage slab reached (e.g. 5%, 10%, 15%, 20%, 25%, 30%) and maximum peak gain reached before trade exit.
- Companion table "Trade Audit Trail-2" features 360 consecutive candle columns: C1 through C360.
- Columns C1 to C360 format: Score(Gain%) e.g. +5(+10.0%), +4(+1.3%), -2(-1.5%), displaying the multi-factor confluence score and live percentage profit from entry price on each candle.
- For LONG trades: Starting from entry_time, records candle score & gain% (C1 = 1st candle after entry, C2 = 2nd candle, etc.) up to exit_time to evaluate momentum and unrealized profit evolution.
- For SHORT trades: Records candle score & gain% from entry_time up to exit_time.
- Unreached / post-exit candles remain marked with "-" for duration tracking.
- Exportable to CSV via "Export Trail-2 CSV" button.
===============================================================


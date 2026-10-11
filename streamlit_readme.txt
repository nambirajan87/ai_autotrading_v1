1. Risk Configuration User Inputs
Under Step 2 > ??? Risk Management Parameters tab in 

app/ui/backtest_app.py
, all 14 parameters from 

RiskConfig
 are displayed with their default values and can be modified:

Parameter	Type	Default Value	UI Widget & Description
capital	float	100_000.0	Number input for starting capital in ?
risk_per_trade_pct	float	0.01 (1%)	Number input formatted as fraction (0.0100 = 1%)
max_daily_loss	float	2_000.0	Number input for maximum daily loss latch in ?
max_open_positions	int	3	Integer input for concurrent open positions limit
max_exposure_pct	float	1.0	Number input for exposure limit ratio (1.0 = 100% unleveraged)
max_quantity	int	5_000	Integer input for max order size in shares
max_consecutive_losses	int	3	Integer input for consecutive losing trades stop
lot_size	int	1	Integer input for trading lot size
tick_size	float	0.05	Number input for price tick resolution in ?
atr_stop_multiplier	float	1.5	Number input for ATR stop distance (multiplier × ATR)
reward_risk_ratio	float	2.0	Number input for profit target ratio (ratio × stop distance)
entry_start	time	09:20	Time picker for intraday trading start
entry_end	time	15:00	Time picker for cutoff time for new entries
square_off_time	time	15:15	Time picker for mandatory intraday square-off
2. Summary of Implementation
Risk & Cost Configuration Integration:



app/ui/backtest_app.py
: Reads and binds all 

RiskConfig
 parameters in session state, displays them in editable widgets, summarizes them in the configuration review step, and instantiates 

RiskConfig
 during execution.


app/config/cost_config.py
: Extended with cost_mode ("PERCENT_MODEL" / "FLAT_PER_ORDER", ?20/order) and slippage_mode ("PERCENT" / "FLAT_PER_ORDER").


app/backtest/costs.py
: Updated leg_cost() and apply_slippage() to support fixed ?20/order execution models.


app/backtest/engine.py
: Updated apply_slippage invocations to pass quantity and cost configuration.
Technical Indicators:

Added causal implementations for Bollinger Bands in 

app/indicators/bollinger.py
, Stochastic Oscillator in 

app/indicators/stochastic.py
, and On-Balance Volume in 

app/indicators/obv.py
.
Dynamic 10-indicator scoring engine built in 

app/strategy/custom_strategy.py
 and linked to 

app/strategy/signal_engine.py
.
NSE Equities Directory & Verification:



app/data/instrument_loader.py
: Automatically downloads and caches 9,775 active NSE equities from Upstox to data/nse_equities_cache.csv (24-hour TTL) with ISO 6166 Luhn check-digit validation.
Audit Database & History:



app/database/backtest_database.py
: Stores backtest runs and trade audits in data/backtest_runs.db, prunes older runs to retain the last 10, and enables 1-click configuration reload from the sidebar.
Tests & Documentation:

Added test suite in 

tests/test_ui_backtest.py
.
Created 

README.md
 and updated 

Functional_Readme.txt
.
3. How to Launch and Verify
Run the Streamlit Backtesting UI:

powershell
streamlit run app/ui/backtest_app.py
Run All Pytest Tests:

powershell
python -m pytest -v
Result: All 97 tests passing (86 pre-existing tests + 11 new tests).
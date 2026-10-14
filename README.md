# AI Autotrading & Backtesting Platform

A professional algorithmic intraday trading, paper-trading, and backtesting system built for Indian Equities (NSE) with Upstox V3 integration, modular indicators, risk management, and an interactive Streamlit UI.

---

## Features

- **NSE Equities Directory & Validation**: Automatic download, caching (24-hour TTL), and lookup of 9,700+ NSE equities with ISO 6166 ISIN Luhn check-digit verification.
- **10 Modular Indicators**:
  1. Exponential Moving Average (EMA)
  2. Volume-Weighted Average Price (VWAP)
  3. Relative Strength Index (RSI)
  4. Moving Average Convergence Divergence (MACD)
  5. Average Directional Index (ADX)
  6. Volume Ratio
  7. Average True Range (ATR)
  8. Bollinger Bands (BB Mid, Upper, Lower, %B) *(Causal / Lookahead-free)*
  9. Stochastic Oscillator (Fast/Slow %K, %D) *(Causal / Lookahead-free)*
  10. On-Balance Volume (OBV & MA) *(Causal / Lookahead-free)*
- **Interactive Multi-Step Streamlit Backtester**:
  - Step 1: Instrument & Date Range Picker with real Upstox V3 candle loader or local fallback.
  - Step 2: In-sample (IS) / Out-of-sample (OOS) data split with visual timeline.
  - Step 3: Strategy Builder (enable/disable 10 indicators, configure weights and thresholds).
  - Step 4: Editable Risk & Cost Parameters (all fields from `RiskConfig` default & editable, flat ₹20/order vs percent model, slippage).
  - Step 5: Execution, KPI Dashboard (CAGR, Sharpe, Max Drawdown, Win Rate, Expectancy, Profit Factor, Total Costs), equity curves, trade audit tables, and CSV exports.
  - Sidebar: SQLite-backed execution history (last 10 runs stored in `data/backtest_runs.db`), with 1-click config reload.
- **Execution & Paper Broker**: Complete SQLite audit trail for signals, orders, positions, and trades.
- **Kotak Neo Live Trading & Gateway**: Full live execution with Kotak Neo API, including bracket orders (Market Entry, Limit Target, Stop-Loss), live 5-level market depth, real-time risk checks, and emergency square-off. Detailed in [docs/KOTAK_LIVE_TRADE_USE_CASES.md](docs/KOTAK_LIVE_TRADE_USE_CASES.md).
- **Zero Lookahead Bias & Strict Intraday Risk Management**: ATR dynamic stop-losses, daily loss limits, exposure limits, and auto square-off at 15:15 IST.

---

## Quickstart

### 1. Environment Setup

Ensure Python 3.11+ is installed. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file from `.env.example`:

```bash
cp .env.example .env
```

Add your `UPSTOX_ACCESS_TOKEN` in `.env` if you wish to fetch real-time and historical candles directly from Upstox.

### 2. Run the Streamlit Backtest UI

Launch the interactive Backtester UI:

```bash
streamlit run app/ui/backtest_app.py
```

### 3. Run the Paper Trading Dashboard (Read-Only)

Launch the paper-trading monitoring dashboard:

```bash
streamlit run app/dashboard/dashboard.py
```

### 4. Running Backtest CLI & Tests

Run test suite (all 97 tests):
```bash
python -m pytest -v
```

Run CLI backtest runner:
```bash
python -m app.backtest.runner
```

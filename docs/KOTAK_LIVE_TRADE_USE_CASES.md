# Kotak Neo Live Trade API Integration & Use Cases

This document details all the functional use cases and touchpoints where the **Live Trade Engine** (`app/ui/live_trade_app.py` & `app/execution/live_trade_engine.py`) connects and communicates with the **Kotak Neo API Gateway** (`app/gateways/kotak_gateway.py`).

---

## Architecture Overview

```
                      +------------------------------------------+
                      |      Streamlit Live Trade Studio         |
                      |       (app/ui/live_trade_app.py)         |
                      +------------------------------------------+
                                           |
                                           v
                      +------------------------------------------+
                      |           Kotak Neo Gateway              |
                      |     (app/gateways/kotak_gateway.py)      |
                      +------------------------------------------+
                                           |
         +---------------------------------+---------------------------------+
         |                                 |                                 |
         v                                 v                                 v
+------------------+             +--------------------+            +-------------------+
| Auth & Sessions  |             | Market Data Feeds  |            | Order Execution   |
| - Login / TOTP   |             | - Live Depth (LTP) |            | - Market Orders   |
| - Token Refresh  |             | - 1m/3m Candles    |            | - Bracket Orders  |
| - Connection Info|             | - Option Chain     |            | - Square-off All  |
+------------------+             +--------------------+            +-------------------+
         |                                 |                                 |
         +---------------------------------+---------------------------------+
                                           |
                                           v
                      +------------------------------------------+
                      |           Kotak Neo Cloud API            |
                      |         (https://napi.kotaksecurities.com)|
                      +------------------------------------------+
```

---

## 1. Authentication & Session Management

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **1.1** | **User Login & 2FA** | `login()` | `client.login(mobilenumber, password)` + `client.session_2fa(OTP=totp)` | Authenticates with mobile number, password, and TOTP. Obtains JWT session credentials (`bearer_token`, `sid`) required for subsequent requests. |
| **1.2** | **Silent Re-Authentication** | `ensure_authenticated()` | Auto-relogin on session expiry | Transparently checks if token is valid before placing any order. If expired (HTTP 401 or code `100022`), it automatically re-executes 2FA login. |
| **1.3** | **Connection Status Diagnostic** | `get_connection_info()` / `is_connected()` | `client.limits()` ping probe | Queries response latency to render the `🟢 Kotak Neo Live API: Connected` status badge in the UI sidebar. |

---

## 2. Real-Time Market Data Feeds

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **2.1** | **Live 5-Level Market Depth & Quotes** | `get_live_market_depth(symbol)` | `client.quotes(..., quote_type="depth")` | Polls real-time LTP, 5-level Best Bid & Best Ask (prices & quantities), volume, open, high, low, and % change every 2 seconds during active monitoring. |
| **2.2** | **Lightweight Tick (LTP)** | `get_ltp(symbol)` | `client.quotes(..., quote_type="ltp")` | Fetches the latest traded price for real-time unrealized P&L (MTM) calculations and candlestick overlay prices. |
| **2.3** | **Historical & Intraday Candles** | `fetch_historical_candles(...)` | `client.historical_data(...)` | Retrieves 1-minute, 3-minute, or 5-minute OHLCV candles strictly between **09:15:00 and 15:30:00 IST** (post-market auction candles up to 15:39 are filtered out). |

---

## 3. Pre-Trade Risk Management Checks (From Strategy RiskConfig)

Before any trade is executed on a consensus signal, Live Trade queries Kotak API to enforce account-level safety guardrails:

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **3.1** | **Check Open Positions Limit** | `get_open_positions_count()` | `client.positions()` | Queries all open intraday (`netTrdQty != 0`) positions from the broker book. If `current_open >= max_open_positions` (e.g. 3), **new entries are blocked**. |
| **3.2** | **Check Daily Consecutive Losses** | `get_loss_trades_today()` | `client.positions()` / `client.trade_report()` | Analyzes today's closed positions. If consecutive losses reach `max_consecutive_losses` (e.g. 3), the engine halts trading for the rest of the day. |
| **3.3** | **Check Capital & Margin Limits** | `get_balance()` | `client.limits()` | Verifies available cash, collateral margin, and margin requirements before transmitting order instructions. |

---

## 4. Live Order Execution (Bracket / OCO Order Flow)

When consensus score satisfies entry rules ($\ge \text{Long Threshold}$ or $\le \text{Short Threshold}$) during market hours:

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **4.1** | **Place Market Entry Order** | `place_market_order(...)` | `client.place_order(product="MIS", order_type="MKT", ...)` | Submits a `BUY` (for LONG) or `SELL` (for SHORT) market order with the configured lot size. Retrieves broker `order_id`, `fill_price`, and `fill_time`. |
| **4.2** | **Place Target Limit Order** | `place_target_order(...)` | `client.place_order(product="MIS", order_type="L", price=target_price)` | Automatically places a profit-booking limit exit order at the calculated target price (e.g., +30% above entry). |
| **4.3** | **Place Stop-Loss Order** | `place_stoploss_order(...)` | `client.place_order(product="MIS", order_type="SL-M", trigger_price=sl_price)` | Automatically places a stop-loss market trigger order at the calculated stop price (e.g., -5% below entry). |
| **4.4** | **Cancel Pending Orders** | `cancel_order(order_id)` | `client.cancel_order(order_id)` | When Target is executed, cancels the pending Stop Loss order (and vice versa) to prevent double fills. |

---

## 5. Position Square-Off & Exits

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **5.1** | **Mandatory Strategy Square-Off Time** | `place_market_order(...)` / `emergency_square_off_all()` | Market exit order on Kotak API | When current wall-clock IST time crosses the strategy's **Square-off Time** (e.g. `15:15 IST` from RiskConfig), open positions are squared off immediately. |
| **5.2** | **Target / Stop-Loss Exit** | `place_market_order(...)` | Market exit order on Kotak API | When live price hits Target or Stop Loss, an opposite market order is transmitted to close the position. |
| **5.3** | **Emergency Square Off All** | `emergency_square_off_all()` | `client.positions()` + `client.order_report()` + `client.place_order(...)` | Triggered by clicking the red **🚨 EMERGENCY SQUARE OFF** button: <br>1. Cancels all pending orders on Kotak.<br>2. Fetches all open positions.<br>3. Places opposite market orders to square off every open position to 0. |
| **5.4** | **Market Close Square-Off (15:30 IST)** | `emergency_square_off_all()` | Opposite market orders | If any position remains open at 15:30:00 IST, it triggers a square-off to avoid overnight carryover. |

---

## 6. Contract & Instrument Discovery

| # | Use Case / Event | Gateway Method | Kotak Neo API / SDK Call | Functional Description |
|---|---|---|---|---|
| **6.1** | **Search Instrument / Scrip** | `search_scrip(...)` / `smart_search_instruments(...)` | `client.search_scrip(...)` | Looks up instrument tokens, expiry dates, and lot sizes for options contracts (e.g. `NIFTY26OCT22500PE`). |
| **6.2** | **Fetch Option Chain** | `get_option_chain(...)` | Option chain endpoint | Retrieves strikes, CE/PE quotes, and open interest for the active underlying. |

---

## Summary Checklist of Kotak API Endpoints

1. **`/login` & `/2fa`**: Account authentication & session creation.
2. **`/quotes` (depth & ltp)**: Real-time price feed & 5-level market depth.
3. **`/historical`**: Intraday candle bars for indicators and scoring (filtered strictly 09:15 - 15:30).
4. **`/limits`**: Margin, balance, and account connectivity check.
5. **`/positions`**: Position sizing, open positions count, and square-off verification.
6. **`/trade_report`**: Daily loss and win/loss streak evaluation.
7. **`/place_order`**: Market entry, target limit, stop-loss trigger, and square-off orders.
8. **`/cancel_order`**: Cancelling bracket order legs.

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Optional
import pandas as pd

from app.backtest.costs import apply_slippage
from app.config.cost_config import CostConfig
from app.execution.broker_interface import BUY, FILLED, MARKET, SELL
from app.execution.market_feed import HistoricalReplayFeed
from app.execution.paper_broker import PaperBroker
from app.execution.streaming_strategy_engine import StreamingSignal, StreamingStrategyEngine
from app.strategy.strategy_store import get_strategy


class PaperSimulationSession:
    """
    Orchestrates a realistic candle-by-candle Paper Trading simulation session.
    Connects HistoricalReplayFeed, StreamingStrategyEngine, and PaperBroker.
    """

    def __init__(
        self,
        symbol: str,
        isin: str,
        date_str: str,
        strategy_name: str,
        candles_df: pd.DataFrame,
        capital: float = 100_000.0,
        warmup_df: Optional[pd.DataFrame] = None,
    ):
        if candles_df.empty:
            raise ValueError(f"No candles provided for {symbol} on {date_str}.")

        self.symbol = symbol.upper()
        self.isin = isin
        self.date_str = date_str
        self.strategy_name = strategy_name
        self.initial_capital = float(capital)
        self.candles_df = candles_df.copy()
        self.warmup_df = warmup_df.copy() if warmup_df is not None else None

        # 1. Load Strategy Definition
        strat_dict = get_strategy(strategy_name)
        if not strat_dict:
            raise ValueError(f"Strategy '{strategy_name}' not found.")

        self.strategy_dict = strat_dict
        self.ind_cfg = strat_dict.get("indicators", {})
        self.risk_cfg = strat_dict.get("risk", {})
        self.cost_dict = strat_dict.get("cost", {})

        # Setup Cost Config
        self.cost_cfg = CostConfig(
            cost_mode=self.cost_dict.get("cost_mode", "FLAT_PER_ORDER"),
            flat_cost_per_order=float(self.cost_dict.get("flat_cost_per_order", 20.0)),
            slippage_mode=self.cost_dict.get("slippage_mode", "FLAT_PER_ORDER"),
            flat_slippage_per_order=float(self.cost_dict.get("flat_slippage_per_order", 20.0)),
            slippage_pct=float(self.cost_dict.get("slippage_pct", 0.0005)),
        )

        # 2. Setup Market Feed (only target day candles)
        self.feed = HistoricalReplayFeed(self.candles_df, symbol=self.symbol)

        # 3. Setup Strategy Engine & Paper Broker
        total_len = len(self.candles_df) + (len(self.warmup_df) if self.warmup_df is not None else 0)
        self.engine = StreamingStrategyEngine.from_saved_strategy(
            self.symbol, self.strategy_name, max_buffer_size=max(1000, total_len)
        )
        self.broker = PaperBroker(capital=self.initial_capital, cost_cfg=self.cost_cfg)

        # 4. Pre-populate indicators with warmup bars if available
        self._apply_warmup()

        # 5. Risk & Trade Management State
        self.open_trade: Optional[Dict[str, Any]] = None
        self.trade_history: List[Dict[str, Any]] = []
        self.order_history: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self.events_log: List[Dict[str, Any]] = []

        # Time bounds
        self.entry_start = self._parse_time(self.risk_cfg.get("entry_start"), time(9, 20))
        self.entry_end = self._parse_time(self.risk_cfg.get("entry_end"), time(15, 0))
        self.square_off_time = self._parse_time(self.risk_cfg.get("square_off_time"), time(15, 15))

        self.target_pct = float(self.ind_cfg.get("fixed_target_pct", self.risk_cfg.get("fixed_target_pct", 3.0)))
        self.stoploss_pct = float(self.ind_cfg.get("fixed_stoploss_pct", self.risk_cfg.get("fixed_stoploss_pct", 2.0)))
        self.trailing_sl_enabled = bool(self.ind_cfg.get("trailing_sl_enabled", self.risk_cfg.get("trailing_sl_enabled", False)))
        self.trail_beyond_target = bool(self.ind_cfg.get("trail_beyond_target", self.risk_cfg.get("trail_beyond_target", False)))

        self.max_daily_loss = float(self.risk_cfg.get("max_daily_loss", 2000.0))
        self.lot_size = max(1, int(self.risk_cfg.get("lot_size", 1)))
        self.short_lot_size = max(1, int(self.ind_cfg.get("short_lot_size", self.risk_cfg.get("short_lot_size", 1))))
        self.risk_per_trade_pct = float(self.risk_cfg.get("risk_per_trade_pct", 0.01))

        self._log_event(datetime.now(), "INFO", f"Initialized Paper Trading session for {self.symbol} with {self.strategy_name}")

    def _apply_warmup(self) -> None:
        """Feed warmup bars into engine so indicators are calibrated before 09:15 open."""
        if self.warmup_df is not None and not self.warmup_df.empty:
            for _, w_candle in self.warmup_df.iterrows():
                self.broker.on_candle(self.symbol, w_candle)
                self.engine.on_candle(w_candle)

    def reset(self) -> None:
        """Reset session to market open (09:15), recalibrating warmup indicators."""
        self.feed.reset()
        self.broker = PaperBroker(capital=self.initial_capital, cost_cfg=self.cost_cfg)
        total_len = len(self.candles_df) + (len(self.warmup_df) if self.warmup_df is not None else 0)
        self.engine = StreamingStrategyEngine.from_saved_strategy(
            self.symbol, self.strategy_name, max_buffer_size=max(1000, total_len)
        )
        self._apply_warmup()
        self.open_trade = None
        self.trade_history.clear()
        self.order_history.clear()
        self.equity_curve.clear()
        self.events_log.clear()
        self._log_event(datetime.now(), "INFO", f"Reset session to 09:15 open for {self.symbol}")

    def _parse_time(self, val: Any, fallback: time) -> time:
        if isinstance(val, time):
            return val
        if isinstance(val, str) and ":" in val:
            parts = [int(p) for p in val.split(":")]
            return time(parts[0], parts[1])
        return fallback

    def _log_event(self, ts: Any, level: str, msg: str) -> None:
        self.events_log.append({
            "timestamp": str(ts),
            "level": level,
            "message": msg,
        })

    def step(self) -> Optional[Dict[str, Any]]:
        """Advance simulation by 1 candle, evaluate strategy and manage positions."""
        candle = self.feed.step()
        if candle is None:
            return None

        ts = candle["timestamp"]
        c_time = pd.to_datetime(ts).time()
        close_p = float(candle["close"])
        high_p = float(candle["high"])
        low_p = float(candle["low"])
        open_p = float(candle["open"])

        # 1. Update Paper Broker's candle state
        self.broker.on_candle(self.symbol, candle)

        # 2. Check Exits for Open Trade (Square-off, Stop Loss, Target, Trailing SL)
        if self.open_trade is not None:
            self._evaluate_open_trade_exits(candle, c_time, open_p, high_p, low_p, ts)

        # 3. Feed candle into Streaming Strategy Engine
        signal = self.engine.on_candle(candle)

        # 4. Check Entries if no position is open
        if self.open_trade is None:
            self._evaluate_new_entry(signal, c_time, close_p, ts)

        # 5. Snapshot Balance & Equity
        bal = self.broker.get_balance()
        mtm_pnl = 0.0
        if self.open_trade is not None:
            side = self.open_trade["side"]
            entry_p = self.open_trade["entry_price"]
            qty = self.open_trade["quantity"]
            mtm_pnl = round(qty * (close_p - entry_p) if side == "LONG" else qty * (entry_p - close_p), 2)

        equity = bal["cash"] + mtm_pnl

        self.equity_curve.append({
            "timestamp": ts,
            "close": close_p,
            "cash": bal["cash"],
            "mtm_pnl": mtm_pnl,
            "equity": equity,
            "realized_pnl": bal["realized_pnl"],
            "total_charges": bal["charges"],
            "net_realized_pnl": bal["net_realized_pnl"],
            "has_open_position": self.open_trade is not None,
        })

        return self.get_state()

    def _evaluate_open_trade_exits(
        self, candle: pd.Series, c_time: time, open_p: float, high_p: float, low_p: float, ts: Any
    ) -> None:
        t = self.open_trade
        side = t["side"]
        exit_triggered = False
        exit_price = 0.0
        exit_reason = ""

        # A. Auto Intraday Square-off
        if c_time >= self.square_off_time:
            exit_triggered = True
            exit_price = open_p
            exit_reason = "SQUARE_OFF"

        # B. Long Trade Stop Loss / Target Checks
        elif side == "LONG":
            if low_p <= t["stop_loss"]:
                exit_triggered = True
                exit_price = min(t["stop_loss"], open_p)
                exit_reason = "STOP_LOSS"
            elif high_p >= t["target"]:
                if self.trail_beyond_target:
                    # Runner Mode: lock in target and keep trailing
                    t["target_reached"] = True
                    t["stop_loss"] = max(t["stop_loss"], t["target"])
                    t["target"] = t["target"] * (1 + self.target_pct / 100.0)
                    self._log_event(ts, "INFO", f"LONG Target reached! Runner active: new SL locked at {t['stop_loss']:.2f}")
                else:
                    exit_triggered = True
                    exit_price = max(t["target"], open_p)
                    exit_reason = "TARGET"
            elif self.trailing_sl_enabled:
                # Ratchet Trailing SL
                offset = t["entry_price"] * (self.stoploss_pct / 100.0)
                new_sl = float(candle["close"]) - offset
                if new_sl > t["stop_loss"]:
                    t["stop_loss"] = round(new_sl, 2)

        # C. Short Trade Stop Loss / Target Checks
        elif side == "SHORT":
            if high_p >= t["stop_loss"]:
                exit_triggered = True
                exit_price = max(t["stop_loss"], open_p)
                exit_reason = "STOP_LOSS"
            elif low_p <= t["target"]:
                if self.trail_beyond_target:
                    t["target_reached"] = True
                    t["stop_loss"] = min(t["stop_loss"], t["target"])
                    t["target"] = t["target"] * (1 - self.target_pct / 100.0)
                    self._log_event(ts, "INFO", f"SHORT Target reached! Runner active: new SL locked at {t['stop_loss']:.2f}")
                else:
                    exit_triggered = True
                    exit_price = min(t["target"], open_p)
                    exit_reason = "TARGET"
            elif self.trailing_sl_enabled:
                offset = t["entry_price"] * (self.stoploss_pct / 100.0)
                new_sl = float(candle["close"]) + offset
                if new_sl < t["stop_loss"]:
                    t["stop_loss"] = round(new_sl, 2)

        if exit_triggered:
            self._close_position(exit_price, exit_reason, ts)

    def _close_position(self, exit_price: float, exit_reason: str, ts: Any) -> None:
        t = self.open_trade
        side = t["side"]
        qty = t["quantity"]

        # Place closing order via PaperBroker
        exit_order_side = SELL if side == "LONG" else BUY
        order = self.broker.place_order(self.symbol, exit_order_side, qty, order_type=MARKET)

        # Apply slippage on exit execution
        adj_exit_price = apply_slippage(exit_price, exit_order_side, self.cost_cfg.slippage_pct)

        gross_pnl = qty * (adj_exit_price - t["entry_price"]) if side == "LONG" else qty * (t["entry_price"] - adj_exit_price)
        charges = round(order.charges if hasattr(order, "charges") else 20.0, 2)
        net_pnl = round(gross_pnl - charges, 2)

        closed_trade = {
            "trade_id": len(self.trade_history) + 1,
            "symbol": self.symbol,
            "side": side,
            "quantity": qty,
            "entry_time": str(t["entry_time"]),
            "entry_price": t["entry_price"],
            "exit_time": str(ts),
            "exit_price": round(adj_exit_price, 2),
            "exit_reason": exit_reason,
            "gross_pnl": round(gross_pnl, 2),
            "charges": charges,
            "net_pnl": net_pnl,
            "stop_loss": t["stop_loss"],
            "target": t["target"],
            "score": t["score"],
        }
        self.trade_history.append(closed_trade)
        self.order_history.append({
            "order_id": order.order_id,
            "timestamp": str(ts),
            "symbol": self.symbol,
            "side": exit_order_side,
            "quantity": qty,
            "price": round(adj_exit_price, 2),
            "type": "MARKET",
            "status": "FILLED",
            "reason": exit_reason,
        })

        try:
            from app.database.paper_database import get_paper_db
            get_paper_db().save_single_trade(
                strategy_name=self.strategy_name,
                symbol=self.symbol,
                session_date=self.date_str,
                trade=closed_trade,
                charges=charges,
            )
        except Exception as e:
            logger.warning("Failed to auto-persist closed trade: %s", e)

        self.engine.record_exit(pd.to_datetime(ts))
        self._log_event(ts, "INFO", f"Closed {side} trade at {adj_exit_price:.2f} ({exit_reason}) | PnL: ₹{gross_pnl:+,.2f}")
        self.open_trade = None

    def _evaluate_new_entry(self, signal: StreamingSignal, c_time: time, close_p: float, ts: Any) -> None:
        # Check Entry Window
        if not (self.entry_start <= c_time <= self.entry_end):
            return

        # Check Daily Loss Circuit Breaker
        bal = self.broker.get_balance()
        if bal["net_realized_pnl"] <= -abs(self.max_daily_loss):
            return

        decision = signal.decision
        if decision not in ("LONG", "SHORT"):
            return

        # Position Sizing
        curr_cash = max(0.0, bal["cash"])
        if curr_cash < 5000.0:
            return

        risk_amount = curr_cash * self.risk_per_trade_pct
        sl_points = close_p * (self.stoploss_pct / 100.0)
        calc_qty = max(1, int(risk_amount / max(sl_points, 1.0)))
        lot = self.lot_size if decision == "LONG" else self.short_lot_size
        qty = max(lot, (calc_qty // lot) * lot)

        # Cap by available cash
        max_possible = max(1, int(curr_cash / close_p))
        qty = min(qty, max_possible)

        order_side = BUY if decision == "LONG" else SELL
        order = self.broker.place_order(self.symbol, order_side, qty, order_type=MARKET)
        adj_entry_p = apply_slippage(close_p, order_side, self.cost_cfg.slippage_pct)

        if decision == "LONG":
            sl_price = round(adj_entry_p * (1.0 - self.stoploss_pct / 100.0), 2)
            tp_price = round(adj_entry_p * (1.0 + self.target_pct / 100.0), 2)
        else:
            sl_price = round(adj_entry_p * (1.0 + self.stoploss_pct / 100.0), 2)
            tp_price = round(adj_entry_p * (1.0 - self.target_pct / 100.0), 2)

        self.open_trade = {
            "side": decision,
            "quantity": qty,
            "entry_time": ts,
            "entry_price": round(adj_entry_p, 2),
            "stop_loss": sl_price,
            "target": tp_price,
            "score": signal.score,
            "target_reached": False,
        }

        self.order_history.append({
            "order_id": order.order_id,
            "timestamp": ts,
            "symbol": self.symbol,
            "side": order_side,
            "quantity": qty,
            "price": round(adj_entry_p, 2),
            "type": "MARKET",
            "status": "FILLED",
            "reason": f"SIGNAL_{decision} (Score: {signal.score})",
        })

        self._log_event(ts, "INFO", f"Opened {decision} {qty} qty at {adj_entry_p:.2f} | SL: {sl_price} | TP: {tp_price}")

    def run_all(self) -> Dict[str, Any]:
        """Run all remaining candles to completion."""
        while not self.feed.is_finished():
            self.step()

        try:
            from app.database.paper_database import get_paper_db
            bal = self.broker.get_balance()
            n = len(self.trade_history)
            wins = len([t for t in self.trade_history if float(t.get("gross_pnl", 0)) > 0])
            get_paper_db().save_run({
                "strategy_name": self.strategy_name,
                "symbol": self.symbol,
                "isin": self.isin,
                "session_date": self.date_str,
                "timeframe": 5,
                "initial_capital": self.initial_capital,
                "final_equity": bal["cash"],
                "total_trades": n,
                "win_rate": (wins / n * 100.0) if n > 0 else 0.0,
                "gross_pnl": bal["realized_pnl"],
                "charges": bal["charges"],
                "net_pnl": bal["net_realized_pnl"],
                "status": "COMPLETED",
            }, trades=[])
        except Exception as e:
            logger.warning("Failed to auto-persist completed run: %s", e)

        return self.get_state()

    def get_state(self) -> Dict[str, Any]:
        """Returns snapshot of current simulator state for real-time UI rendering."""
        curr_candle = self.feed.current_candle()
        last_signal = self.engine.latest_signal
        bal = self.broker.get_balance()

        ltp = float(curr_candle["close"]) if curr_candle is not None else 0.0
        mtm_pnl = 0.0
        if self.open_trade is not None:
            side = self.open_trade["side"]
            entry_p = self.open_trade["entry_price"]
            qty = self.open_trade["quantity"]
            mtm_pnl = round(qty * (ltp - entry_p) if side == "LONG" else qty * (entry_p - ltp), 2)

        return {
            "symbol": self.symbol,
            "isin": self.isin,
            "date": self.date_str,
            "strategy": self.strategy_name,
            "current_index": self.feed.current_index,
            "total_candles": self.feed.total_candles,
            "progress_pct": self.feed.progress_pct,
            "is_finished": self.feed.is_finished(),
            "current_candle": curr_candle.to_dict() if curr_candle is not None else None,
            "ltp": ltp,
            "open_trade": self.open_trade,
            "mtm_pnl": mtm_pnl,
            "total_equity": round(bal["cash"] + mtm_pnl, 2),
            "cash": bal["cash"],
            "realized_pnl": bal["realized_pnl"],
            "net_realized_pnl": bal["net_realized_pnl"],
            "total_charges": bal["charges"],
            "trade_count": len(self.trade_history),
            "trades": list(self.trade_history),
            "orders": list(self.order_history),
            "events": list(self.events_log[-20:]),
            "latest_signal": asdict(last_signal) if last_signal is not None else None,
            "equity_curve": list(self.equity_curve),
        }

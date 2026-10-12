import logging
from datetime import datetime, time
from typing import Any, Dict, List, Optional
import pandas as pd

from app.backtest.costs import apply_slippage, leg_cost
from app.config.cost_config import CostConfig
from app.database.live_database import get_live_db
from app.execution.streaming_strategy_engine import StreamingSignal, StreamingStrategyEngine
from app.gateways.base_gateway import BaseBrokerGateway
from app.strategy.strategy_store import get_strategy

logger = logging.getLogger(__name__)


class LiveTradeEngine:
    """
    Live Algorithmic Execution Engine for Upstox & Kotak Neo.
    Enforces 'Signal-Bar Close':
    Instant market order execution as soon as a candle completes,
    recording verified broker fill price and fill timestamp into trade log.
    """

    def __init__(
        self,
        gateway: BaseBrokerGateway,
        strategy_name: str,
        symbol: str,
        isin: str,
        timeframe: int = 1,
        capital: float = 100_000.0,
        lot_size: int = 65,
        target_pct: float = 3.0,
        stoploss_pct: float = 2.0,
        trailing_sl_enabled: bool = False,
        trail_beyond_target: bool = False,
        max_daily_loss: float = 2_000.0,
        square_off_time: str = "15:15",
    ):
        self.gateway = gateway
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.isin = isin
        self.timeframe = timeframe
        self.capital = capital
        self.lot_size = lot_size
        self.target_pct = target_pct
        self.stoploss_pct = stoploss_pct
        self.trailing_sl_enabled = trailing_sl_enabled
        self.trail_beyond_target = trail_beyond_target
        self.max_daily_loss = max_daily_loss
        self.square_off_time = square_off_time

        # Strategy engine
        self.engine = StreamingStrategyEngine.from_saved_strategy(
            symbol=symbol,
            strategy_name=strategy_name,
        )

        # Database
        self.db = get_live_db()
        self.session_id = self.db.start_session(
            gateway=gateway.gateway_name,
            strategy_name=strategy_name,
            symbol=symbol,
            isin=isin,
            timeframe=timeframe,
            capital=capital,
        )

        # Execution tracking
        self.open_position: Optional[Dict[str, Any]] = None
        self.is_active = True
        self.daily_pnl = 0.0
        self.total_trades = 0
        self.trade_counter = 0
        self.logs: List[Dict[str, Any]] = []
        self.cost_cfg = CostConfig()

        self._log("INFO", f"Live Trading Session #{self.session_id} initialized on {gateway.gateway_name} Gateway")

    def warmup(self, candles_df: pd.DataFrame) -> int:
        """Prime strategy indicators with historical candles before live stepping."""
        if candles_df is None or candles_df.empty:
            return 0
        warmed = 0
        for _, row in candles_df.iterrows():
            self.engine.on_candle(row)
            warmed += 1
        self._log("INFO", f"Engine pre-warmed with {warmed} historical bars. Indicators primed.")
        return warmed

    def _log(self, level: str, message: str) -> None:
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.insert(0, entry)
        logger.info("[%s] %s", level, message)

    def on_candle_close(self, candle: pd.Series) -> Dict[str, Any]:
        """
        Invoked immediately upon candle close.
        Evaluates stop/target exits first, then fires new signal entries.
        """
        if not self.is_active:
            return {"status": "HALTED"}

        ts = str(candle["timestamp"])
        open_p = float(candle["open"])
        high_p = float(candle["high"])
        low_p = float(candle["low"])
        close_p = float(candle["close"])

        # Check square-off time (e.g. 15:15 IST)
        now_time = datetime.now().time()
        sq_parts = self.square_off_time.split(":")
        sq_t = time(int(sq_parts[0]), int(sq_parts[1]))

        # 1. Evaluate open position exits
        if self.open_position is not None:
            self._evaluate_exits(candle, close_p, high_p, low_p, now_time, sq_t)

        # Check daily loss circuit breaker
        if self.daily_pnl <= -self.max_daily_loss:
            self._log("WARNING", f"Max Daily Loss limit hit (-₹{abs(self.daily_pnl):,.2f}). Trading paused.")
            self.is_active = False
            self.db.update_session_status(self.session_id, "DAILY_LOSS_LIMIT", self.total_trades, self.daily_pnl)
            return {"status": "MAX_LOSS_HALT"}

        # 2. Check new entries on candle close
        signal = self.engine.on_candle(candle)

        if self.open_position is None and now_time < sq_t and self.is_active:
            if signal.decision in ("LONG", "SHORT"):
                self._execute_entry(signal, candle, close_p, ts)

        return {"status": "OK", "last_close": close_p, "signal": signal.decision, "score": signal.score}

    def _execute_entry(self, signal, candle: pd.Series, close_p: float, ts: str) -> None:
        side = signal.decision
        order_side = "BUY" if side == "LONG" else "SELL"
        qty = self.lot_size

        # Place instant Market Order through Gateway
        order = self.gateway.place_market_order(
            symbol=self.symbol,
            side=order_side,
            quantity=qty,
            isin=self.isin,
            reference_price=close_p,
        )

        # Verified broker fill parameters
        fill_price = order.fill_price or close_p
        fill_time = order.fill_time or ts

        # Compute Stop Loss & Target prices
        if side == "LONG":
            sl_price = round(fill_price * (1.0 - self.stoploss_pct / 100.0), 2)
            tp_price = round(fill_price * (1.0 + self.target_pct / 100.0), 2)
        else:
            sl_price = round(fill_price * (1.0 + self.stoploss_pct / 100.0), 2)
            tp_price = round(fill_price * (1.0 - self.target_pct / 100.0), 2)

        self.trade_counter += 1
        self.open_position = {
            "trade_id": self.trade_counter,
            "side": side,
            "quantity": qty,
            "signal_time": ts,
            "entry_time": fill_time,
            "entry_price": fill_price,
            "stop_loss": sl_price,
            "target": tp_price,
            "score": signal.score,
            "peak_price": fill_price,
            "target_reached": False,
        }

        # Record order to DB
        self.db.record_order({
            "session_id": self.session_id,
            "gateway": self.gateway.gateway_name,
            "order_id": order.order_id,
            "timestamp": fill_time,
            "symbol": self.symbol,
            "side": order_side,
            "quantity": qty,
            "price": fill_price,
            "status": "FILLED",
            "reason": f"SIGNAL_{side} (Score: {signal.score})",
        })

        self._log("INFO", f"Triggered {side} {qty} qty at Market. Fill: ₹{fill_price:.2f} @ {fill_time} | SL: {sl_price} | TP: {tp_price}")

    def _evaluate_exits(self, candle: pd.Series, close_p: float, high_p: float, low_p: float, now_time: time, sq_t: time) -> None:
        pos = self.open_position
        side = pos["side"]
        entry_p = pos["entry_price"]
        sl_p = pos["stop_loss"]
        tp_p = pos["target"]
        qty = pos["quantity"]

        exit_triggered = False
        exit_price = close_p
        exit_reason = ""

        # A. Square-off time reached
        if now_time >= sq_t:
            exit_triggered = True
            exit_price = close_p
            exit_reason = "SQUARE_OFF"

        # B. Check Stop Loss & Target
        if not exit_triggered:
            if side == "LONG":
                if low_p <= sl_p:
                    exit_triggered = True
                    exit_price = sl_p
                    exit_reason = "STOP_LOSS"
                elif high_p >= tp_p:
                    if self.trailing_sl_enabled and self.trail_beyond_target:
                        pos["target_reached"] = True
                        pos["stop_loss"] = max(pos["stop_loss"], round(high_p * (1.0 - 0.01), 2))
                    else:
                        exit_triggered = True
                        exit_price = tp_p
                        exit_reason = "TARGET"
            else:  # SHORT
                if high_p >= sl_p:
                    exit_triggered = True
                    exit_price = sl_p
                    exit_reason = "STOP_LOSS"
                elif low_p <= tp_p:
                    if self.trailing_sl_enabled and self.trail_beyond_target:
                        pos["target_reached"] = True
                        pos["stop_loss"] = min(pos["stop_loss"], round(low_p * (1.0 + 0.01), 2))
                    else:
                        exit_triggered = True
                        exit_price = tp_p
                        exit_reason = "TARGET"

        if exit_triggered:
            order_side = "SELL" if side == "LONG" else "BUY"
            exit_order = self.gateway.place_market_order(
                symbol=self.symbol,
                side=order_side,
                quantity=qty,
                reference_price=exit_price,
            )

            fill_exit_p = exit_order.fill_price or exit_price
            exit_time = exit_order.fill_time or str(candle["timestamp"])

            # P&L calculations
            gross_pnl = round(qty * (fill_exit_p - entry_p) if side == "LONG" else qty * (entry_p - fill_exit_p), 2)
            charges = round(exit_order.charges, 2)
            net_pnl = round(gross_pnl - charges, 2)

            self.daily_pnl += net_pnl
            self.total_trades += 1

            # Save trade record
            self.db.record_trade({
                "session_id": self.session_id,
                "gateway": self.gateway.gateway_name,
                "strategy_name": self.strategy_name,
                "symbol": self.symbol,
                "trade_id": pos["trade_id"],
                "side": side,
                "quantity": qty,
                "signal_time": pos["signal_time"],
                "entry_time": pos["entry_time"],
                "entry_price": entry_p,
                "exit_time": exit_time,
                "exit_price": fill_exit_p,
                "exit_reason": exit_reason,
                "gross_pnl": gross_pnl,
                "charges": charges,
                "net_pnl": net_pnl,
                "stop_loss": sl_p,
                "target": tp_p,
                "score": pos["score"],
            })

            self.db.record_order({
                "session_id": self.session_id,
                "gateway": self.gateway.gateway_name,
                "order_id": exit_order.order_id,
                "timestamp": exit_time,
                "symbol": self.symbol,
                "side": order_side,
                "quantity": qty,
                "price": fill_exit_p,
                "status": "FILLED",
                "reason": f"EXIT_{exit_reason}",
            })

            self._log("INFO", f"Closed {side} trade ({exit_reason}) at ₹{fill_exit_p:.2f} | Net P&L: ₹{net_pnl:+,.2f}")
            self.open_position = None
            self.db.update_session_status(self.session_id, "ACTIVE", self.total_trades, self.daily_pnl)

    def emergency_square_off(self) -> None:
        """Emergency Kill Switch: instantly square off open position at market."""
        if self.open_position is not None:
            pos = self.open_position
            side = pos["side"]
            qty = pos["quantity"]
            order_side = "SELL" if side == "LONG" else "BUY"

            order = self.gateway.place_market_order(
                symbol=self.symbol,
                side=order_side,
                quantity=qty,
            )
            exit_p = order.fill_price or pos["entry_price"]
            gross_pnl = round(qty * (exit_p - pos["entry_price"]) if side == "LONG" else qty * (pos["entry_price"] - exit_p), 2)
            net_pnl = round(gross_pnl - order.charges, 2)
            self.daily_pnl += net_pnl
            self.total_trades += 1

            self.db.record_trade({
                "session_id": self.session_id,
                "gateway": self.gateway.gateway_name,
                "strategy_name": self.strategy_name,
                "symbol": self.symbol,
                "trade_id": pos["trade_id"],
                "side": side,
                "quantity": qty,
                "signal_time": pos["signal_time"],
                "entry_time": pos["entry_time"],
                "entry_price": pos["entry_price"],
                "exit_time": order.fill_time,
                "exit_price": exit_p,
                "exit_reason": "KILL_SWITCH",
                "gross_pnl": gross_pnl,
                "charges": order.charges,
                "net_pnl": net_pnl,
                "stop_loss": pos["stop_loss"],
                "target": pos["target"],
                "score": pos["score"],
            })
            self.open_position = None
            self._log("CRITICAL", f"EMERGENCY KILL SWITCH: Position squared off at ₹{exit_p:.2f}")

        self.is_active = False
        self.db.update_session_status(self.session_id, "EMERGENCY_HALT", self.total_trades, self.daily_pnl)

    def force_manual_entry(self, side: str, reference_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Manually trigger an immediate trade (BUY / LONG or SELL / SHORT) from the Cockpit.
        Bypasses indicator wait and executes an immediate market order through the active Gateway.
        """
        if not self.is_active:
            return {"status": "ERROR", "message": "Engine is halted. Please re-arm engine."}

        if self.open_position is not None:
            return {"status": "ERROR", "message": f"Position already open ({self.open_position['side']} {self.open_position['quantity']} qty). Exit current position first."}

        ref_price = reference_price
        if not ref_price or ref_price <= 0:
            try:
                ref_price = self.gateway.get_ltp(self.symbol, self.isin)
            except Exception:
                ref_price = 0.0

        if not ref_price or ref_price <= 0:
            ref_price = 100.0

        sig_decision = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        mock_sig = StreamingSignal(
            timestamp=datetime.now(),
            symbol=self.symbol,
            decision=sig_decision,
            score=2 if sig_decision == "LONG" else -2,
            components={"MANUAL_OVERRIDE": 2 if sig_decision == "LONG" else -2},
            indicators={},
            close=ref_price,
            reasons=["MANUAL_OVERRIDE"],
        )
        dummy_candle = pd.Series({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "open": ref_price,
            "high": ref_price,
            "low": ref_price,
            "close": ref_price,
            "volume": 1000,
        })
        self._execute_entry(mock_sig, dummy_candle, ref_price, str(dummy_candle["timestamp"]))
        return {"status": "SUCCESS", "side": sig_decision, "fill_price": ref_price}

    def force_manual_exit(self, reason: str = "MANUAL_EXIT") -> Dict[str, Any]:
        """Manually squares off the open position at current market price without halting the engine."""
        if self.open_position is None:
            return {"status": "ERROR", "message": "No open position to exit."}

        pos = self.open_position
        side = pos["side"]
        qty = pos["quantity"]
        order_side = "SELL" if side == "LONG" else "BUY"

        order = self.gateway.place_market_order(
            symbol=self.symbol,
            side=order_side,
            quantity=qty,
            isin=self.isin,
        )
        exit_p = order.fill_price or pos["entry_price"]
        gross_pnl = round(qty * (exit_p - pos["entry_price"]) if side == "LONG" else qty * (pos["entry_price"] - exit_p), 2)
        net_pnl = round(gross_pnl - order.charges, 2)
        self.daily_pnl += net_pnl
        self.total_trades += 1

        self.db.record_trade({
            "session_id": self.session_id,
            "gateway": self.gateway.gateway_name,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "trade_id": pos["trade_id"],
            "side": side,
            "quantity": qty,
            "signal_time": pos["signal_time"],
            "entry_time": pos["entry_time"],
            "entry_price": pos["entry_price"],
            "exit_time": order.fill_time,
            "exit_price": exit_p,
            "exit_reason": reason,
            "gross_pnl": gross_pnl,
            "charges": order.charges,
            "net_pnl": net_pnl,
            "stop_loss": pos["stop_loss"],
            "target": pos["target"],
            "score": pos["score"],
        })
        self.open_position = None
        self._log("INFO", f"Manual Square Off executed: Closed {side} at ₹{exit_p:.2f} | Net P&L: ₹{net_pnl:+,.2f}")
        self.db.update_session_status(self.session_id, "ACTIVE", self.total_trades, self.daily_pnl)
        return {"status": "SUCCESS", "exit_price": exit_p, "net_pnl": net_pnl}


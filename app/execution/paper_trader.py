import json
import logging
from dataclasses import asdict, dataclass

import pandas as pd

from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.database.database import Database
from app.execution.broker_interface import BUY, CANCELLED, FILLED, REJECTED, SELL, Broker
from app.portfolio.portfolio import Portfolio
from app.risk.risk_engine import RiskEngine
from app.strategy.signal_engine import generate_signal

logger = logging.getLogger(__name__)
TERMINAL = (FILLED, CANCELLED, REJECTED)


@dataclass
class PaperTraderConfig:
    allow_short: bool = True


class PaperTrader:
    def __init__(self, symbol: str, strategy_cfg: StrategyConfig, risk_cfg: RiskConfig,
                 broker: Broker, db: Database, trader_cfg: PaperTraderConfig = None):
        if getattr(broker, "participation", None) is not None:
            raise ValueError("PaperTrader does not handle partial fills yet; use participation=None")
        self.symbol = symbol
        self.strategy_cfg = strategy_cfg
        self.cfg = trader_cfg or PaperTraderConfig()
        self.broker = broker
        self.db = db
        self.risk = RiskEngine(risk_cfg)
        self.portfolio = Portfolio()

        snapshot = {"strategy": asdict(strategy_cfg), "risk": asdict(risk_cfg),
                    "cost": asdict(broker.cost_cfg) if hasattr(broker, "cost_cfg") else None}
        self.run_id = db.start_run(symbol, strategy_cfg.version, json.dumps(snapshot, default=str))

        self.pending_entry = None     # dict while an entry order waits for its fill
        self.open_trade = None        # dict while a position is open
        self._open_order_ids = set()  # orders not yet in a final state
        self._exec_cursor = 0         # how many broker executions are already saved
        self.current_day = None
        self.last_ts = None

    # ---------- main loop ----------
    def run(self, df: pd.DataFrame) -> dict:
        for _, row in df.iterrows():
            self.on_candle(row)
        return self.finish()

    def on_candle(self, row: pd.Series) -> None:
        ts = row["timestamp"]
        self.last_ts = ts
        if ts.date() != self.current_day:
            self._start_new_day(ts)

        # an entry order still waiting at square-off time is too late: cancel it
        if self.pending_entry is not None and self.risk.should_square_off(ts):
            self.broker.cancel_order(self.pending_entry["order_id"])
            self._sync_orders(ts)
            self.pending_entry = None
            self._event(ts, "INFO", "pending entry cancelled at square-off time")

        self.broker.on_candle(self.symbol, row)     # 1. orders from EARLIER candles fill now
        self._record_fills(ts)
        self._check_entry_fill(row)                 # 2. did our entry fill?
        self._check_trade_closed(ts)                #    did our exit fill?
        self._check_exit(row)                       # 3. stop / target / square-off?
        if self.open_trade is None and self.pending_entry is None:
            self._look_for_entry(row)               # 4. new signal from THIS candle's close

    def finish(self) -> dict:
        if self.open_trade is not None:
            self._event(self.last_ts, "WARNING", "run ended with an open position")
        summary = {"balance": self.broker.get_balance(),
                   "closed_trades": self.portfolio.trade_count,
                   "open_position": self.open_trade is not None}
        self.db.end_run(self.run_id, json.dumps(summary))
        self.db.commit()
        return summary

    # ---------- steps ----------
    def _start_new_day(self, ts) -> None:
        if self.open_trade is not None:
            self._event(ts, "CRITICAL", "position carried into a new day (square-off failed)")
        if self.pending_entry is not None:
            self.broker.cancel_order(self.pending_entry["order_id"])
            self._sync_orders(ts)
            self.pending_entry = None
        self.risk.reset_for_new_day()
        self.current_day = ts.date()
        self.db.commit()

    def _record_fills(self, ts) -> None:
        fills = self.broker.executions        # PaperBroker-specific (see limits)
        for e in fills[self._exec_cursor:]:
            self.db.insert_execution(self.run_id, e)
        changed = len(fills) > self._exec_cursor
        self._exec_cursor = len(fills)
        self._sync_orders(ts)
        if changed:
            for p in self.broker.get_positions():
                self.db.insert_position(self.run_id, ts, p)

    def _sync_orders(self, ts) -> None:
        for order_id in list(self._open_order_ids):
            order = self.broker.get_order_status(order_id)
            self.db.upsert_order(self.run_id, order, ts)
            if order.status in TERMINAL:
                self._open_order_ids.discard(order_id)

    def _check_entry_fill(self, row: pd.Series) -> None:
        p = self.pending_entry
        if p is None:
            return
        order = self.broker.get_order_status(p["order_id"])
        if order.status == FILLED:
            decision, signal = p["decision"], p["signal"]
            self.open_trade = {
                "entry_order_id": order.order_id, "exit_order_id": None, "exit_reason": None,
                "exit_attempts": 0, "side": decision.side, "quantity": order.filled_quantity,
                "stop_loss": decision.stop_loss, "target": decision.target,
                "signal": signal, "decision": decision, "signal_id": p["signal_id"],
                "entry_time": row["timestamp"],
            }
            self.pending_entry = None
        elif order.status in (REJECTED, CANCELLED):
            self._event(row["timestamp"], "WARNING",
                        f"entry order {order.order_id} {order.status}: {order.reject_reason}")
            self.pending_entry = None

    def _check_trade_closed(self, ts) -> None:
        t = self.open_trade
        if t is None or t["exit_order_id"] is None:
            return
        order = self.broker.get_order_status(t["exit_order_id"])
        if order.status == FILLED:
            self._close_trade(ts)
        elif order.status in (REJECTED, CANCELLED):
            self._event(ts, "CRITICAL", f"exit order {order.order_id} {order.status}: "
                                        f"{order.reject_reason}; will retry")
            t["exit_order_id"] = None

    def _check_exit(self, row: pd.Series) -> None:
        t = self.open_trade
        if t is None or t["exit_order_id"] is not None:
            return
        ts, reason = row["timestamp"], None
        if self.risk.should_square_off(ts):
            reason = "SQUARE_OFF"
        elif t["side"] == "LONG":
            if row["low"] <= t["stop_loss"]:
                reason = "STOP"
            elif row["high"] >= t["target"]:
                reason = "TARGET"
        else:
            if row["high"] >= t["stop_loss"]:
                reason = "STOP"
            elif row["low"] <= t["target"]:
                reason = "TARGET"
        if reason is None:
            return

        t["exit_attempts"] += 1
        order = self.broker.place_order(
            self.symbol, SELL if t["side"] == "LONG" else BUY, t["quantity"],
            client_order_id=f"run{self.run_id}-exit-{t['entry_order_id']}-{t['exit_attempts']}")
        self._open_order_ids.add(order.order_id)
        self._sync_orders(ts)
        if order.status == REJECTED:
            self._event(ts, "CRITICAL", f"exit order rejected: {order.reject_reason}")
            return
        t["exit_order_id"], t["exit_reason"] = order.order_id, reason

    def _look_for_entry(self, row: pd.Series) -> None:
        ts = row["timestamp"]
        signal = generate_signal(row, self.symbol, self.strategy_cfg)
        signal_id = self.db.insert_signal(self.run_id, signal.to_dict())
        if signal.signal == "HOLD":
            return
        if signal.signal == "SHORT" and not self.cfg.allow_short:
            self._event(ts, "INFO", "SHORT signal ignored: shorting disabled")
            return

        state = self.portfolio.risk_state(self.broker, ts.date())
        decision = self.risk.evaluate(signal, state)
        self.db.insert_risk_event(self.run_id, signal_id, ts, decision.to_dict())
        if not decision.approved:
            return

        order = self.broker.place_order(
            self.symbol, BUY if decision.side == "LONG" else SELL, decision.quantity,
            client_order_id=f"run{self.run_id}-{self.symbol}-{signal.signal}-{ts}")
        self._open_order_ids.add(order.order_id)
        self._sync_orders(ts)
        if order.status == REJECTED:
            self._event(ts, "WARNING", f"entry order rejected: {order.reject_reason}")
            return
        self.pending_entry = {"order_id": order.order_id, "decision": decision,
                              "signal": signal, "signal_id": signal_id}

    def _close_trade(self, ts) -> None:
        t = self.open_trade
        entry_id, exit_id = t["entry_order_id"], t["exit_order_id"]
        entry = [e for e in self.broker.executions if e.order_id == entry_id]
        exits = [e for e in self.broker.executions if e.order_id == exit_id]
        qty = sum(e.quantity for e in entry)
        entry_price = sum(e.price * e.quantity for e in entry) / qty
        exit_price = sum(e.price * e.quantity for e in exits) / sum(e.quantity for e in exits)
        gross = sum(e.realized_pnl for e in exits)           # entry fills realize 0
        costs = sum(e.charges for e in entry + exits)
        net = round(gross - costs, 2)

        trade = {
            "signal_id": t["signal_id"], "symbol": self.symbol, "side": t["side"],
            "strategy_version": self.strategy_cfg.version, "score": int(t["signal"].score),
            "signal_time": str(t["signal"].timestamp), "entry_time": str(t["entry_time"]),
            "exit_time": str(ts), "quantity": int(qty),
            "entry_price": round(float(entry_price), 4), "exit_price": round(float(exit_price), 4),
            "stop_loss": float(t["stop_loss"]), "target": float(t["target"]),
            "exit_reason": t["exit_reason"], "gross_pnl": round(float(gross), 2),
            "costs": round(float(costs), 2), "net_pnl": float(net),
            "entry_order_id": entry_id, "exit_order_id": exit_id,
            "indicators_json": json.dumps(t["signal"].indicators),
            "risk_json": json.dumps(t["decision"].to_dict()),
        }
        self.db.insert_trade(self.run_id, trade)
        self.portfolio.record_trade(ts, net)
        logger.info("TRADE CLOSED %s %s qty=%d in=%.2f out=%.2f %s net=%.2f", t["side"],
                    self.symbol, qty, entry_price, exit_price, t["exit_reason"], net)
        self.open_trade = None

    def _event(self, ts, level: str, message: str) -> None:
        logger.log(getattr(logging, level), message)
        self.db.log_event(self.run_id, ts, level, message)


if __name__ == "__main__":
    from app.config.cost_config import CostConfig
    from app.data.regime_simulator import generate_regime_sample
    from app.execution.paper_broker import PaperBroker
    from app.indicators.indicator_engine import calculate_all
    from app.monitoring.logging_setup import setup_logging

    setup_logging("WARNING")
    print("NOTE: synthetic data. This tests the machinery, not profitability.")
    data = calculate_all(generate_regime_sample())
    db = Database("data/paper_trading.db")
    broker = PaperBroker(100_000, CostConfig())
    trader = PaperTrader("RELIANCE", StrategyConfig(), RiskConfig(), broker, db)
    summary = trader.run(data)

    q = lambda sql: db.query(sql, (trader.run_id,))
    print("run_id:", trader.run_id)
    print("balance:", summary["balance"])
    print("trades:", q("SELECT COUNT(*) AS n, ROUND(SUM(net_pnl),2) AS net, "
                       "ROUND(SUM(costs),2) AS costs FROM trades WHERE run_id=?")[0])
    print("by exit reason:", q("SELECT exit_reason, COUNT(*) AS n, ROUND(SUM(net_pnl),2) AS net "
                               "FROM trades WHERE run_id=? GROUP BY exit_reason"))
    print("orders by status:", q("SELECT status, COUNT(*) AS n FROM orders WHERE run_id=? GROUP BY status"))
    print("system events:", q("SELECT level, message FROM system_events WHERE run_id=? LIMIT 5"))
    db.close()
import dataclasses
import logging
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from app.backtest.costs import apply_slippage, leg_cost
from app.config.cost_config import CostConfig
from app.execution.broker_interface import (
    BUY, CANCELLED, FILLED, LIMIT, MARKET, NEW, PARTIALLY_FILLED, REJECTED, SELL,
    Broker, Execution, Order, Position,
)

logger = logging.getLogger(__name__)


@dataclass
class _Holding:
    qty: int = 0              # signed
    avg_price: float = 0.0


class PaperBroker(Broker):
    def __init__(self, capital: float, cost_cfg: CostConfig,
                 max_volume_participation: Optional[float] = None):
        if capital <= 0:
            raise ValueError("capital must be positive")
        if max_volume_participation is not None and not (0 < max_volume_participation <= 1):
            raise ValueError("max_volume_participation must be in (0, 1]")
        self.initial_capital = capital
        self.cash = capital
        self.cost_cfg = cost_cfg
        self.participation = max_volume_participation

        self._orders = {}            # order_id -> Order (dicts keep insertion order = time priority)
        self._client_ids = {}        # client_order_id -> order_id
        self._holdings = {}          # symbol -> _Holding
        self._last_price = {}        # symbol -> last close seen
        self._counter = 0
        self.realized_pnl = 0.0      # gross, before charges
        self.total_charges = 0.0
        self.executions = []         # full trade history

    # ---------- Broker interface ----------
    def place_order(self, symbol, side, quantity, order_type=MARKET,
                    limit_price=None, client_order_id=None) -> Order:
        # Duplicate protection: the same client_order_id returns the SAME order
        if client_order_id is not None and client_order_id in self._client_ids:
            logger.warning("Duplicate client_order_id %s ignored", client_order_id)
            return dataclasses.replace(self._orders[self._client_ids[client_order_id]])

        self._counter += 1
        order = Order(order_id=f"PAPER-{self._counter:06d}", symbol=symbol, side=side,
                      order_type=order_type, quantity=quantity, limit_price=limit_price,
                      client_order_id=client_order_id)
        self._orders[order.order_id] = order
        if client_order_id is not None:
            self._client_ids[client_order_id] = order.order_id

        error = self._validate(order)
        if error:
            order.status, order.reject_reason = REJECTED, error
            logger.warning("Order %s REJECTED: %s", order.order_id, error)
        else:
            logger.info("Order %s accepted: %s %d %s %s", order.order_id, side, quantity,
                        symbol, order_type)
        return dataclasses.replace(order)       # a copy, so callers can't edit our state

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None or order.status not in (NEW, PARTIALLY_FILLED):
            return False
        order.status = CANCELLED
        logger.info("Order %s cancelled (filled %d of %d)", order_id,
                    order.filled_quantity, order.quantity)
        return True

    def get_order_status(self, order_id: str) -> Order:
        if order_id not in self._orders:
            raise KeyError(f"Unknown order id: {order_id}")
        return dataclasses.replace(self._orders[order_id])

    def get_positions(self) -> list:
        result = []
        for symbol, h in self._holdings.items():
            if h.qty == 0:
                continue
            last = self._last_price.get(symbol, h.avg_price)
            result.append(Position(symbol, h.qty, round(h.avg_price, 4), last,
                                   round(h.qty * (last - h.avg_price), 2)))
        return result

    def get_balance(self) -> dict:
        unrealized = sum(p.unrealized_pnl for p in self.get_positions())
        market_value = sum(p.quantity * p.market_price for p in self.get_positions())
        return {
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),         # gross
            "charges": round(self.total_charges, 2),
            "net_realized_pnl": round(self.realized_pnl - self.total_charges, 2),
            "unrealized_pnl": round(unrealized, 2),
            "equity": round(self.cash + market_value, 2),
        }

    # ---------- simulation: feed candles ----------
    def on_candle(self, symbol: str, candle: pd.Series) -> list:
        """Try to fill every open order for `symbol` against this candle.
        Returns the new Executions."""
        self._last_price[symbol] = float(candle["close"])
        budget = None if self.participation is None else \
            math.floor(float(candle["volume"]) * self.participation)

        new_fills = []
        for order in list(self._orders.values()):   # list() copy: we modify orders while looping
            if order.symbol != symbol or order.status not in (NEW, PARTIALLY_FILLED):
                continue
            price = self._fill_price(order, candle)
            if price is None:
                continue
            qty = order.quantity - order.filled_quantity
            if budget is not None:
                qty = min(qty, budget)
            if qty <= 0:
                continue
            fill = self._execute(order, qty, price, candle["timestamp"])
            if fill is not None:
                if budget is not None:
                    budget -= qty
                new_fills.append(fill)
        return new_fills

    # ---------- internals ----------
    def _validate(self, order: Order) -> Optional[str]:
        if order.side not in (BUY, SELL):
            return f"invalid side '{order.side}'"
        if order.order_type not in (MARKET, LIMIT):
            return f"invalid order type '{order.order_type}'"
        if not isinstance(order.quantity, int) or order.quantity <= 0:
            return "quantity must be a positive integer"
        if order.order_type == LIMIT and (order.limit_price is None or order.limit_price <= 0):
            return "limit order needs a positive limit_price"
        return None

    def _fill_price(self, order: Order, candle: pd.Series) -> Optional[float]:
        """Price this order would get on this candle, or None if it doesn't fill."""
        o, h, l = float(candle["open"]), float(candle["high"]), float(candle["low"])
        if order.order_type == MARKET:
            return apply_slippage(o, order.side, self.cost_cfg.slippage_pct)
        # LIMIT: limit price or better (a gap through the limit fills at the open)
        if order.side == BUY and l <= order.limit_price:
            return min(order.limit_price, o)
        if order.side == SELL and h >= order.limit_price:
            return max(order.limit_price, o)
        return None

    def _execute(self, order: Order, qty: int, price: float, ts) -> Optional[Execution]:
        charges = leg_cost(order.side, price, qty, self.cost_cfg)
        value = price * qty
        cash_delta = (-value if order.side == BUY else value) - charges

        if self.cash + cash_delta < 0:                       # can't afford it
            order.status = REJECTED if order.filled_quantity == 0 else CANCELLED
            order.reject_reason = "insufficient cash"
            logger.warning("Order %s: insufficient cash (need %.2f, have %.2f)",
                           order.order_id, -cash_delta, self.cash)
            return None

        realized = self._apply_to_position(order.symbol, order.side, qty, price)
        self.cash += cash_delta
        self.realized_pnl += realized
        self.total_charges += charges

        total = order.filled_quantity + qty
        order.avg_fill_price = round(
            (order.avg_fill_price * order.filled_quantity + price * qty) / total, 4)
        order.filled_quantity = total
        order.charges = round(order.charges + charges, 2)
        order.status = FILLED if total == order.quantity else PARTIALLY_FILLED

        fill = Execution(order.order_id, order.symbol, order.side, qty, price,
                         charges, ts, round(realized, 2))
        self.executions.append(fill)
        logger.info("FILL %s %s %d @ %.2f charges=%.2f (%s)", order.side, order.symbol,
                    qty, price, charges, order.status)
        return fill

    def _apply_to_position(self, symbol: str, side: str, qty: int, price: float) -> float:
        """Update the holding. Returns gross P&L realized by this fill."""
        h = self._holdings.setdefault(symbol, _Holding())
        signed = qty if side == BUY else -qty

        if h.qty == 0 or (h.qty > 0) == (signed > 0):        # opening or adding
            h.avg_price = (abs(h.qty) * h.avg_price + qty * price) / (abs(h.qty) + qty)
            h.qty += signed
            return 0.0

        direction = 1 if h.qty > 0 else -1                   # reducing, closing or flipping
        closing = min(qty, abs(h.qty))
        realized = closing * (price - h.avg_price) * direction
        h.qty -= direction * closing
        remaining = qty - closing
        if remaining > 0:                                    # flipped to the other side
            h.qty = remaining if side == BUY else -remaining
            h.avg_price = price
        elif h.qty == 0:
            h.avg_price = 0.0
        return realized


if __name__ == "__main__":
    from app.data.regime_simulator import generate_regime_sample
    from app.monitoring.logging_setup import setup_logging

    setup_logging("INFO")
    broker = PaperBroker(capital=100_000, cost_cfg=CostConfig())
    candles = generate_regime_sample().iloc[:6]

    order = broker.place_order("RELIANCE", "BUY", 10)
    print("placed:", order.order_id, order.status)
    for i, (_, row) in enumerate(candles.iterrows()):
        broker.on_candle("RELIANCE", row)
        if i == 2:
            broker.place_order("RELIANCE", "SELL", 10)
        print(i, row["timestamp"].time(),
              "positions:", [(p.quantity, p.avg_price) for p in broker.get_positions()])
    print(broker.get_balance())
    print(len(broker.executions), "executions")
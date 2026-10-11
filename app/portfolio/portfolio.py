from datetime import date

from app.risk.exposure import OpenPosition
from app.risk.risk_engine import PortfolioState


class Portfolio:
    """Remembers closed trades and builds the PortfolioState the risk engine reads."""

    def __init__(self):
        self._closed = []            # (exit_date, net_pnl), in chronological order

    @property
    def trade_count(self) -> int:
        return len(self._closed)

    def record_trade(self, exit_ts, net_pnl: float) -> None:
        self._closed.append((exit_ts.date(), float(net_pnl)))

    def realized_today(self, day: date) -> float:
        return round(sum(p for d, p in self._closed if d == day), 2)

    def consecutive_losses(self, day: date) -> int:
        """Trailing losing streak among today's trades (resets on a win and each new day)."""
        streak = 0
        for d, pnl in reversed(self._closed):
            if d != day or pnl >= 0:
                break
            streak += 1
        return streak

    def risk_state(self, broker, day: date) -> PortfolioState:
        positions = broker.get_positions()
        unrealized = sum(p.unrealized_pnl for p in positions)
        open_positions = [
            OpenPosition(p.symbol, abs(p.quantity), p.avg_price,
                         "LONG" if p.quantity > 0 else "SHORT")
            for p in positions
        ]        
        return PortfolioState(open_positions=open_positions,
                              daily_pnl=round(self.realized_today(day) + unrealized, 2),
                              consecutive_losses=self.consecutive_losses(day),
                              available_cash=broker.get_balance()["cash"])                              
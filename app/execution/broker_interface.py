from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

# Order sides, types and statuses (plain strings, easy to log and store in a DB)
BUY, SELL = "BUY", "SELL"
MARKET, LIMIT = "MARKET", "LIMIT"
NEW, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED = (
    "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED")


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    limit_price: Optional[float] = None
    client_order_id: Optional[str] = None   # caller's own id, used for duplicate protection
    status: str = NEW
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    charges: float = 0.0
    reject_reason: str = ""


@dataclass
class Execution:
    """One fill (an order can have several fills if partially filled)."""
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    charges: float
    timestamp: object
    realized_pnl: float      # gross P&L realized by THIS fill (0 if it only opened/added)


@dataclass
class Position:
    symbol: str
    quantity: int            # signed: + long, - short
    avg_price: float
    market_price: float
    unrealized_pnl: float


class Broker(ABC):
    """The contract every broker (paper or live) must follow.
    ABC + @abstractmethod ~ a Java interface: you cannot instantiate it, and a
    subclass that forgets a method cannot be instantiated either."""

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: int,
                    order_type: str = MARKET, limit_price: Optional[float] = None,
                    client_order_id: Optional[str] = None) -> Order: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order: ...

    @abstractmethod
    def get_positions(self) -> list: ...

    @abstractmethod
    def get_balance(self) -> dict: ...
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd


@dataclass
class GatewayOrder:
    order_id: str
    gateway: str  # "UPSTOX" or "KOTAK"
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET" or "LIMIT"
    quantity: int
    price: float = 0.0
    status: str = "PENDING"  # "PENDING", "FILLED", "REJECTED", "CANCELLED"
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    fill_price: float = 0.0
    fill_time: str = ""
    charges: float = 0.0
    rejection_reason: str = ""
    reason: str = ""
    client_order_id: Optional[str] = None


@dataclass
class GatewayPosition:
    symbol: str
    gateway: str
    quantity: int  # Positive for LONG, negative for SHORT
    avg_price: float
    ltp: float = 0.0
    pnl: float = 0.0
    side: str = "LONG"


@dataclass
class GatewayBalance:
    gateway: str
    available_margin: float
    used_margin: float
    total_capital: float
    realized_pnl: float = 0.0
    currency: str = "INR"


class BaseBrokerGateway(ABC):
    """Unified Broker Gateway contract for Upstox and Kotak Neo."""

    @property
    @abstractmethod
    def gateway_name(self) -> str:
        """Name of the broker gateway (e.g. 'UPSTOX' or 'KOTAK')."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker credentials are configured and active."""
        pass

    @abstractmethod
    def get_connection_info(self) -> Dict[str, Any]:
        """Return connectivity details, status, and environment."""
        pass

    @abstractmethod
    def get_balance(self) -> GatewayBalance:
        """Fetch account margin / funds balance."""
        pass

    @abstractmethod
    def get_positions(self) -> List[GatewayPosition]:
        """Fetch open net positions from broker."""
        pass

    @abstractmethod
    def get_orders(self) -> List[GatewayOrder]:
        """Fetch today's order book from broker."""
        pass

    @abstractmethod
    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        isin: Optional[str] = None,
        instrument_key: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> GatewayOrder:
        """Place an instantaneous Market order as soon as candle closes."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open pending order."""
        pass

    @abstractmethod
    def get_ltp(self, symbol: str, instrument_key: Optional[str] = None) -> float:
        """Get latest traded market price."""
        pass

    def fetch_historical_candles(
        self,
        symbol: str,
        instrument_key: str,
        timeframe_minutes: int,
        start_date: Any,
        end_date: Any,
    ) -> pd.DataFrame:
        """Fetch OHLCV historical candle data for analysis or replay."""
        pass

    def get_live_market_depth(self, symbol: str, instrument_key: Optional[str] = None) -> Dict[str, Any]:
        """Fetch Level-2 order book depth (5 bids, 5 asks, spread)."""
        ref_ltp = self.get_ltp(symbol, instrument_key) or 100.0
        tick = 0.05
        return {
            "status": "SIMULATED_DEPTH",
            "symbol": symbol,
            "token": instrument_key or "N/A",
            "ltp": ref_ltp,
            "change": 0.0,
            "per_change": 0.0,
            "volume": 10000,
            "total_buy": 1365,
            "total_sell": 1625,
            "spread": 0.10,
            "bids": [{"orders": 2, "quantity": 130, "price": round(ref_ltp - tick * (i + 1), 2)} for i in range(5)],
            "asks": [{"orders": 3, "quantity": 195, "price": round(ref_ltp + tick * (i + 1), 2)} for i in range(5)],
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }


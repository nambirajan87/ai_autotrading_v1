from abc import ABC, abstractmethod
from typing import Callable, Optional
import pandas as pd


class BaseMarketFeed(ABC):
    """Abstract interface for streaming completed candles to trading engines."""

    @abstractmethod
    def set_callback(self, callback: Callable[[pd.Series], None]) -> None:
        """Register the consumer callback invoked for every completed candle."""
        pass

    @abstractmethod
    def step(self) -> Optional[pd.Series]:
        """Advance exactly one completed candle and emit it."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the feed back to the initial state."""
        pass

    @abstractmethod
    def is_finished(self) -> bool:
        """Returns True if all historical candles have been replayed."""
        pass


class HistoricalReplayFeed(BaseMarketFeed):
    """
    Feeds historical candles sequentially, one bar at a time.
    Enables realistic candle-by-candle simulation for paper trading.
    """

    def __init__(self, df: pd.DataFrame, symbol: str = "EQUITY"):
        if df.empty:
            raise ValueError("HistoricalReplayFeed received an empty DataFrame")

        # Ensure sorted chronologically
        self.df = df.copy()
        if "timestamp" in self.df.columns:
            self.df["timestamp"] = pd.to_datetime(self.df["timestamp"])
            self.df = self.df.sort_values("timestamp").reset_index(drop=True)
        else:
            self.df = self.df.reset_index(drop=True)

        self.symbol = symbol
        self._cursor = 0
        self._callback: Optional[Callable[[pd.Series], None]] = None

    def set_callback(self, callback: Callable[[pd.Series], None]) -> None:
        self._callback = callback

    @property
    def total_candles(self) -> int:
        return len(self.df)

    @property
    def current_index(self) -> int:
        return self._cursor

    @property
    def progress_pct(self) -> float:
        if self.total_candles == 0:
            return 100.0
        return round((self._cursor / self.total_candles) * 100.0, 1)

    def is_finished(self) -> bool:
        return self._cursor >= len(self.df)

    def current_candle(self) -> Optional[pd.Series]:
        if 0 < self._cursor <= len(self.df):
            return self.df.iloc[self._cursor - 1]
        return None

    def step(self) -> Optional[pd.Series]:
        """Advance by 1 candle and invoke callback if set."""
        if self.is_finished():
            return None

        candle = self.df.iloc[self._cursor]
        self._cursor += 1

        if self._callback is not None:
            self._callback(candle)

        return candle

    def reset(self) -> None:
        self._cursor = 0

    def seek(self, index: int) -> None:
        self._cursor = max(0, min(index, len(self.df)))


class LiveBrokerageFeed(BaseMarketFeed):
    """
    Bridge interface for real-time live brokerage feeds (e.g. Upstox WebSocket / polling).
    Emits completed live candles using the same contract as HistoricalReplayFeed.
    """

    def __init__(self, symbol: str, timeframe: int = 5):
        self.symbol = symbol
        self.timeframe = timeframe
        self._callback: Optional[Callable[[pd.Series], None]] = None
        self._running = False

    def set_callback(self, callback: Callable[[pd.Series], None]) -> None:
        self._callback = callback

    def step(self) -> Optional[pd.Series]:
        # Live feed operates push-based on real-time candle close events
        return None

    def reset(self) -> None:
        self._running = False

    def is_finished(self) -> bool:
        return False

from dataclasses import dataclass
from datetime import time
from typing import Optional

from app.config.settings import Settings


# @dataclass(frozen=True)
# class RiskConfig:
#     capital: float = 100_000.0
#     risk_per_trade_pct: float = 0.01      # 0.01 = 1% of capital
#     max_daily_loss: float = 2_000.0       # in rupees
#     max_open_positions: int = 3
#     max_exposure_pct: float = 1.0         # total position value / capital; 1.0 = no leverage
#     max_quantity: int = 5_000
#     max_consecutive_losses: int = 3
#     lot_size: int = 1                     # equities = 1; derivatives later
#     tick_size: float = 0.05               # verify per instrument
#     atr_stop_multiplier: float = 1.5
#     reward_risk_ratio: float = 2.0
#     entry_start: time = time(9, 20)
#     entry_end: time = time(15, 0)         # no new entries after this
#     square_off_time: time = time(15, 15)  # intraday positions must be closed by here

@dataclass(frozen=True)
class RiskConfig:
    # ---- Account ----
    capital: float = 100_000.0
    # Starting capital in rupees.
    # Enforced: > 0.   Example: 50_000 | 100_000 | 500_000

    # ---- Per-trade risk ----
    risk_per_trade_pct: float = 0.01
    # Fraction of capital risked per trade (0.01 = 1%).
    # Enforced: > 0 and <= 0.05 (typo guard: 1 instead of 0.01 is rejected).
    # Suggested: 0.0025 to 0.02 (0.25% to 2%). Beginners: 0.5% to 1%.

    atr_stop_multiplier: float = 1.5
    # Stop distance = this x ATR.
    # Enforced: nothing (see note below). Must be > 0 to work.
    # Suggested: 1.0 to 3.0. Smaller = tighter stop, more stop-outs.

    reward_risk_ratio: float = 2.0
    # Target distance = this x stop distance.
    # Enforced: nothing (see note below). Must be > 0 to work.
    # Suggested: 1.0 to 3.0. Higher = fewer targets hit.

    # ---- Fixed percentage stop and target ----
    fixed_target_pct: Optional[float] = None
    # Profit target as fixed percentage of entry price (e.g. 3.0 for 3%).
    fixed_stoploss_pct: Optional[float] = None
    # Stop loss as fixed percentage of entry price (e.g. 2.0 for 2%).

    # ---- Trailing Stop-Loss (Model B) ----
    trailing_sl_enabled: bool = False
    # If True, moves stop loss to Breakeven at 50% target, and trails peak by fixed_stoploss_pct.
    trail_beyond_target: bool = False
    # If True, trade does not exit at fixed target, but continues trailing to capture runaway upside.

    # ---- Direction and short lot sizing ----
    trade_side: str = "BOTH"
    # "LONG", "SHORT", or "BOTH".
    short_lot_size: Optional[int] = None
    # Number of lots for SHORT trades (e.g. 1 lot). If None, uses standard risk sizing.

    # ---- Trade snooze / cooldown period ----
    snooze_minutes: int = 0
    # Minutes to prevent new trade entries after previous trade exit (cooldown period).

    # ---- Daily and streak limits ----
    max_daily_loss: float = 2_000.0
    # Rupees. Trading stops for the day once daily P&L <= -this.
    # Enforced: > 0.
    # Suggested: 1% to 3% of capital (1_000 to 3_000 on 100k).

    max_consecutive_losses: int = 3
    # Stop entering after this many losses in a row (resets daily in the backtest).
    # Enforced: nothing. Must be >= 1 (0 blocks every trade).
    # Suggested: 2 to 5.

    # ---- Position limits ----
    max_open_positions: int = 3
    # Max simultaneous open positions.
    # Enforced: >= 1.   Suggested: 1 to 5. (The backtester currently runs 1 symbol.)

    max_exposure_pct: float = 1.0
    # Total position value / capital. 1.0 = no leverage.
    # Enforced: > 0.
    # Suggested: 0.1 to 1.0. Above 1.0 means intraday margin: verify your
    # broker's current margin rules first, and note that losses scale with it.

    max_quantity: int = 5_000
    # Hard cap on shares per order (fat-finger guard).
    # Enforced: >= 1.
    # Suggested: roughly capital / typical price, with some headroom.

    cash_buffer_pct: float = 0.01
    # Keep this fraction of available cash unused when sizing, to absorb a gap-up
    # at the next open, slippage and charges.
    # Enforced: 0 <= value < 1.   Suggested: 0.005 to 0.02.

    # ---- Instrument ----
    lot_size: int = 1
    # Quantity must be a multiple of this.
    # Enforced: >= 1.   Equities = 1. Derivatives = exchange lot size (verify, it changes).

    tick_size: float = 0.05
    # Smallest price step; stop and target are rounded to it.
    # Enforced: nothing. Must be > 0 (0 causes a divide-by-zero).
    # Typical: 0.05 for most NSE equities. Verify per instrument.

    # ---- Trading hours (IST) ----
    entry_start: time = time(9, 20)
    # No entries before this.
    # Enforced: entry_start < entry_end < square_off_time.
    # Suggested: 09:20 to 09:30 (NSE opens 09:15; the first minutes are volatile).

    entry_end: time = time(15, 0)
    # No NEW entries after this.
    # Suggested: 14:30 to 15:00. Later entries have little time before square-off,
    # and your report showed late entries tend to end as SQUARE_OFF.

    square_off_time: time = time(15, 15)
    # Open intraday positions are force-closed from this time.
    # Suggested: 15:10 to 15:20. NSE closes 15:30, and broker auto square-off
    # times vary, so check your broker.

    def __post_init__(self):
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        # 5% cap is a typo guard (e.g. someone typing 1 instead of 0.01)
        if not (0 < self.risk_per_trade_pct <= 0.05):
            raise ValueError("risk_per_trade_pct must be in (0, 0.05]")
        if self.max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be positive")
        if self.max_open_positions < 1 or self.max_quantity < 1 or self.lot_size < 1:
            raise ValueError("max_open_positions, max_quantity, lot_size must be >= 1")
        if self.max_exposure_pct <= 0:
            raise ValueError("max_exposure_pct must be positive")
        if not (self.entry_start < self.entry_end < self.square_off_time):
            raise ValueError("need entry_start < entry_end < square_off_time")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.atr_stop_multiplier <= 0 or self.reward_risk_ratio <= 0:
            raise ValueError("atr_stop_multiplier and reward_risk_ratio must be positive")
        if self.fixed_target_pct is not None and not (1 <= self.fixed_target_pct <= 50):
            raise ValueError("fixed_target_pct must be between 1 and 50")
        if self.fixed_stoploss_pct is not None and not (1 <= self.fixed_stoploss_pct <= 10):
            raise ValueError("fixed_stoploss_pct must be between 1 and 10")
        if self.trade_side not in ("LONG", "SHORT", "BOTH"):
            raise ValueError("trade_side must be one of 'LONG', 'SHORT', 'BOTH'")
        if self.short_lot_size is not None and self.short_lot_size < 1:
            raise ValueError("short_lot_size must be >= 1")
        if self.snooze_minutes < 0:
            raise ValueError("snooze_minutes must be >= 0")
        if self.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be >= 1")
        if not (0 <= self.cash_buffer_pct < 1):
            raise ValueError("cash_buffer_pct must be in [0, 1)")

    @staticmethod
    def from_settings(settings: Settings, capital: float = 100_000.0) -> "RiskConfig":
        """Connects Phase 1's .env values to the risk engine."""
        return RiskConfig(
            capital=capital,
            risk_per_trade_pct=settings.max_risk_per_trade,
            max_daily_loss=settings.max_daily_loss,
            max_open_positions=settings.max_positions,
        )
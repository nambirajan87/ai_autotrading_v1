import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from app.config.risk_config import RiskConfig
from app.risk.exposure import remaining_exposure
from app.risk.position_sizing import calculate_quantity
from app.risk.stop_loss import calculate_fixed_pct_levels, calculate_levels, is_stop_valid
from app.strategy.signal_engine import Signal

logger = logging.getLogger(__name__)


@dataclass
class PortfolioState:
    """What the risk engine needs to know. The portfolio module fills this in (Phase 7)."""
    open_positions: list = field(default_factory=list)
    daily_pnl: float = 0.0              # realized only for now; negative = loss
    consecutive_losses: int = 0
    available_cash: Optional[float] = None   # None = don't limit by cash (backtester)
    last_exit_time: Optional[object] = None  # timestamp of previous trade exit for snooze cooldown


@dataclass
class RiskDecision:
    approved: bool
    symbol: str
    side: str
    quantity: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    risk_amount: float = 0.0            # quantity x |entry - stop|
    rejection_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate_order(d: RiskDecision, cfg: RiskConfig) -> Optional[str]:
    """Last gate. Independent of the sizing code. Returns an error text, or None if OK."""
    if d.quantity <= 0:
        return "quantity must be positive"
    if d.quantity % cfg.lot_size != 0:
        return "quantity is not a multiple of lot size"
    if d.quantity > cfg.max_quantity:
        return "quantity above max_quantity"
    if d.entry_price <= 0:
        return "entry price must be positive"
    if not is_stop_valid(d.side, d.entry_price, d.stop_loss):
        return "stop loss on the wrong side of entry"
    if (d.side == "LONG" and d.target <= d.entry_price) or \
       (d.side == "SHORT" and d.target >= d.entry_price):
        return "target on the wrong side of entry"
    is_fixed_derivative = (getattr(cfg, "fo_mode", "") == "OIPulse Mode" or cfg.lot_size > 1) and d.quantity <= cfg.lot_size
    is_fixed_short = (d.side == "SHORT" and getattr(cfg, "short_lot_size", None) is not None
                      and d.quantity <= cfg.lot_size * getattr(cfg, "short_lot_size", 1))
    if not is_fixed_short and not is_fixed_derivative and d.risk_amount > cfg.capital * cfg.risk_per_trade_pct + 1e-6:
        return "risk amount above per-trade limit"
    return None


class RiskEngine:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.kill_switch = False
        self.kill_reason = ""
        self.trading_enabled = True
        self._seen_keys = set()          # approved (symbol, side, timestamp), for duplicate protection

    # ---- controls ----
    def activate_kill_switch(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def release_kill_switch(self) -> None:
        """Deliberately manual. Nothing calls this automatically."""
        self.kill_switch = False
        self.kill_reason = ""
        logger.warning("Kill switch released manually")

    def reset_for_new_day(self) -> None:
        self.trading_enabled = True
        self._seen_keys.clear()          # kill switch is intentionally NOT reset here

    def should_square_off(self, ts) -> bool:
        return ts.time() >= self.cfg.square_off_time

    # ---- main gate ----
    def evaluate(self, signal: Signal, state: PortfolioState) -> RiskDecision:
        cfg = self.cfg
        decision = RiskDecision(approved=False, symbol=signal.symbol, side=signal.signal)

        if self.kill_switch:
            return self._reject(decision, f"kill switch active ({self.kill_reason})")

        if state.daily_pnl <= -cfg.max_daily_loss:
            if self.trading_enabled:
                logger.critical("Daily loss limit reached (pnl=%.2f)", state.daily_pnl)
            self.trading_enabled = False
        if not self.trading_enabled:
            return self._reject(decision, f"trading disabled for the day "
                                          f"(daily P&L {state.daily_pnl:.2f}, limit -{cfg.max_daily_loss:.2f})")

        if signal.signal not in ("LONG", "SHORT"):
            return self._reject(decision, "no trade signal (HOLD)")

        ts = signal.timestamp
        if not (cfg.entry_start <= ts.time() <= cfg.entry_end):
            return self._reject(decision, f"outside entry hours ({ts.time()})")

        if state.consecutive_losses >= cfg.max_consecutive_losses:
            return self._reject(decision, f"{state.consecutive_losses} consecutive losses")

        if len(state.open_positions) >= cfg.max_open_positions:
            return self._reject(decision, "max open positions reached")

        if any(p.symbol == signal.symbol for p in state.open_positions):
            return self._reject(decision, f"position already open in {signal.symbol}")

        key = (signal.symbol, signal.signal, str(ts))
        if key in self._seen_keys:
            return self._reject(decision, "duplicate order for this signal")

        # Snooze / cooldown check: prevent new entries for snooze_minutes after previous exit
        snooze_min = getattr(cfg, "snooze_minutes", 0) or 0
        if snooze_min > 0 and state.last_exit_time is not None:
            try:
                last_dt = pd.to_datetime(state.last_exit_time)
                curr_dt = pd.to_datetime(signal.timestamp)
                if last_dt.date() == curr_dt.date():
                    elapsed_min = (curr_dt - last_dt).total_seconds() / 60.0
                    if 0 <= elapsed_min < snooze_min:
                        return self._reject(decision, f"snooze active ({snooze_min}m cooldown)")
            except Exception as e:
                logger.warning("Error evaluating snooze cooldown: %s", e)

        entry = signal.indicators.get("close")
        atr = signal.indicators.get("atr")
        if entry is None or entry <= 0:
            return self._reject(decision, "missing or invalid close")

        if getattr(cfg, "fixed_target_pct", None) is not None and getattr(cfg, "fixed_stoploss_pct", None) is not None:
            stop, target = calculate_fixed_pct_levels(
                signal.signal, entry, float(cfg.fixed_target_pct), float(cfg.fixed_stoploss_pct), cfg.tick_size
            )
        else:
            if atr is None or atr <= 0:
                return self._reject(decision, "missing or invalid close/ATR")
            stop, target = calculate_levels(signal.signal, entry, atr, cfg.atr_stop_multiplier,
                                            cfg.reward_risk_ratio, cfg.tick_size)
        if not is_stop_valid(signal.signal, entry, stop):
            return self._reject(decision, "invalid stop loss")

        # Check trade side direction filter (LONG, SHORT, BOTH)
        trade_side = getattr(cfg, "trade_side", "BOTH") or "BOTH"
        if trade_side == "LONG" and signal.signal == "SHORT":
            return self._reject(decision, "SHORT ignored (LONG only selected)")
        if trade_side == "SHORT" and signal.signal == "LONG":
            return self._reject(decision, "LONG ignored (SHORT only selected)")

        room = remaining_exposure(cfg.capital, cfg.max_exposure_pct, state.open_positions)
        if state.available_cash is not None:
            room = min(room, state.available_cash * (1 - cfg.cash_buffer_pct))

        if signal.signal == "SHORT" and getattr(cfg, "short_lot_size", None) is not None:
            # For SHORT trades when short_lot_size is configured, strictly short_lot_size lots are taken
            short_lots = cfg.short_lot_size
            qty = min(short_lots * cfg.lot_size, cfg.max_quantity)
        else:
            qty = calculate_quantity(cfg.capital, cfg.risk_per_trade_pct, entry, stop,
                                     cfg.lot_size, max_exposure_value=room,
                                     max_quantity=cfg.max_quantity)
        if qty == 0 and (getattr(cfg, "fo_mode", "") == "OIPulse Mode" or cfg.lot_size > 1):
            # In F&O / OIPulse Mode or derivative contracts with lot_size > 1, allocate standard 1 lot
            qty = min(cfg.lot_size, cfg.max_quantity)
        if qty == 0:
            return self._reject(decision, "quantity is 0 (stop too wide for risk budget, "
                                          "or no exposure room left)")

        decision.quantity = qty
        decision.entry_price = entry
        decision.stop_loss = stop
        decision.target = target
        decision.risk_amount = round(qty * abs(entry - stop), 2)

        error = validate_order(decision, cfg)
        if error:
            return self._reject(decision, f"order validation failed: {error}")

        self._seen_keys.add(key)
        decision.approved = True
        logger.info("APPROVED %s %s qty=%d entry=%.2f stop=%.2f target=%.2f risk=%.2f",
                    signal.signal, signal.symbol, qty, entry, stop, target, decision.risk_amount)
        return decision

    def _reject(self, decision: RiskDecision, reason: str) -> RiskDecision:
        decision.approved = False
        decision.rejection_reason = reason
        logger.info("REJECTED %s %s: %s", decision.side, decision.symbol, reason)
        return decision


if __name__ == "__main__":
    import json

    from app.config.strategy_config import StrategyConfig
    from app.data.regime_simulator import generate_regime_sample
    from app.indicators.indicator_engine import calculate_all
    from app.monitoring.logging_setup import setup_logging
    from app.strategy.signal_engine import generate_signal

    setup_logging("INFO")
    data = calculate_all(generate_regime_sample())
    engine = RiskEngine(RiskConfig())

    sig = None
    for _, row in data.iterrows():
        candidate = generate_signal(row, "RELIANCE", StrategyConfig())
        if candidate.signal != "HOLD":
            sig = candidate
            break

    print("Signal:", sig.signal, "score", sig.score, "at", sig.timestamp)
    print(json.dumps(engine.evaluate(sig, PortfolioState()).to_dict(), indent=2))
    print("Same signal again ->", engine.evaluate(sig, PortfolioState()).rejection_reason)
    print("After -2500 daily loss ->", engine.evaluate(sig, PortfolioState(daily_pnl=-2500)).rejection_reason)
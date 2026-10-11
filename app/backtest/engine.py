import logging
from collections import Counter
from dataclasses import asdict, dataclass, fields

import pandas as pd

from app.backtest.costs import apply_slippage, leg_cost
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig
from app.indicators.regime import detect_regime_row
from app.risk.risk_engine import PortfolioState, RiskEngine
from app.risk.stop_loss import calculate_fixed_pct_levels, round_to_tick
from app.strategy.signal_engine import generate_signal

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    allow_short: bool = True   # delivery-based shorting isn't possible in Indian equities; intraday is
    trade_side: str = "BOTH"   # "LONG", "SHORT", or "BOTH"
    short_lot_size: int = 1    # fixed lot count for SHORT trades (1 lot)
    snooze_minutes: int = 0    # cooldown minutes after trade exit to prevent new entries
    trailing_sl_enabled: bool = False  # Enable Model B Trailing Stop-Loss
    trail_beyond_target: bool = False  # If True, trade does not exit at target, keeps trailing
    execution_mode: str = "SIGNAL_BAR_CLOSE"  # "SIGNAL_BAR_CLOSE" (immediate fill at candle close) or "NEXT_BAR_OPEN"


@dataclass
class OpenTrade:
    side: str
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    signal_time: object
    entry_time: object
    score: int
    regime: str
    entry_slippage_cost: float
    peak_price: float = 0.0
    trough_price: float = 0.0
    trailing_active: bool = False


@dataclass
class Trade:
    symbol: str
    side: str
    strategy_version: str
    regime: str
    score: int
    signal_time: object
    entry_time: object
    exit_time: object
    quantity: int
    entry_price: float
    exit_price: float
    stop_loss: float
    target: float
    exit_reason: str
    gross_pnl: float       # price P&L (slippage is already inside the fill prices)
    costs: float           # brokerage + taxes + charges, both legs
    slippage_cost: float   # informational: how much of the loss was slippage
    net_pnl: float         # gross_pnl - costs
    peak_gain_pct: float = 0.0


class Backtester:
    def __init__(self, symbol: str, strategy_cfg: StrategyConfig, risk_cfg: RiskConfig,
                 cost_cfg: CostConfig, bt_cfg: BacktestConfig):
        self.symbol = symbol
        self.strategy_cfg = strategy_cfg
        self.cost_cfg = cost_cfg
        self.bt_cfg = bt_cfg
        self.risk = RiskEngine(risk_cfg)
        if getattr(self.bt_cfg, "snooze_minutes", 0) and not getattr(self.risk.cfg, "snooze_minutes", 0):
            self.risk.cfg.snooze_minutes = self.bt_cfg.snooze_minutes

        self.trades = []
        self.position = None          # OpenTrade or None
        self.pending = None           # (RiskDecision, Signal, regime) waiting for next open
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.current_day = None
        self.last_exit_time = None
        self.signal_counts = Counter()
        self.rejections = Counter()

    # ---------- main loop ----------
    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        prev = None
        for _, row in df.iterrows():
            if row["timestamp"].date() != self.current_day:
                self._start_new_day(row["timestamp"], prev)
            self._process_candle(row)
            prev = row

        if self.position is not None and prev is not None:
            self._exit(prev["close"], prev["timestamp"], "END_OF_DATA", slippage=True)

        columns = [f.name for f in fields(Trade)]
        return pd.DataFrame([asdict(t) for t in self.trades], columns=columns)

    def _start_new_day(self, ts, prev) -> None:
        if self.position is not None and prev is not None:   # safety net; square-off should prevent this
            self._exit(prev["close"], prev["timestamp"], "FORCED_DAY_END", slippage=True)
        self.pending = None
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_exit_time = None
        self.risk.reset_for_new_day()
        self.current_day = ts.date()

    def _process_candle(self, row: pd.Series) -> None:
        ts = row["timestamp"]

        # 1. exits for a position carried over from earlier candles
        if self.position is not None:
            if self.risk.should_square_off(ts):
                self._exit(row["open"], ts, "SQUARE_OFF", slippage=True)
            else:
                self._check_stop_target(row)

        # 2. fill the order decided at the PREVIOUS candle's close
        if self.pending is not None:
            if self.risk.should_square_off(ts):
                self.pending = None                     # too late in the day to enter
            else:
                self._fill_pending(row)
                self._check_stop_target(row)            # the entry candle can hit stop/target too

        # 3. look for a new entry using THIS candle's close
        if self.position is None and self.pending is None:
            self._look_for_entry(row)

    # ---------- entry ----------
    def _look_for_entry(self, row: pd.Series) -> None:
        signal = generate_signal(row, self.symbol, self.strategy_cfg)
        self.signal_counts[signal.signal] += 1
        if signal.signal == "HOLD":
            return

        trade_side = getattr(self.bt_cfg, "trade_side", "BOTH") or getattr(self.risk.cfg, "trade_side", "BOTH") or "BOTH"
        if trade_side == "LONG" and signal.signal == "SHORT":
            self.rejections["SHORT ignored (LONG only selected)"] += 1
            return
        if trade_side == "SHORT" and signal.signal == "LONG":
            self.rejections["LONG ignored (SHORT only selected)"] += 1
            return

        if signal.signal == "SHORT" and not self.bt_cfg.allow_short:
            self.rejections["shorting disabled"] += 1
            return

        state = PortfolioState(open_positions=[], daily_pnl=self.daily_pnl,
                               consecutive_losses=self.consecutive_losses,
                               last_exit_time=self.last_exit_time)
        decision = self.risk.evaluate(signal, state)
        if decision.approved:
            regime = row.get("regime", "") or detect_regime_row(row)
            if getattr(self.bt_cfg, "execution_mode", "SIGNAL_BAR_CLOSE") == "NEXT_BAR_OPEN":
                self.pending = (decision, signal, regime)
            else:
                self._enter_immediate(row, decision, signal, regime)
        else:
            self.rejections[decision.rejection_reason.split(" (")[0]] += 1

    def _enter_immediate(self, row: pd.Series, decision, signal, regime: str) -> None:
        leg = "BUY" if decision.side == "LONG" else "SELL"
        reference = row["close"]
        fill = apply_slippage(reference, leg, self.cost_cfg.slippage_pct,
                              cfg=self.cost_cfg, quantity=decision.quantity)

        stop_loss = decision.stop_loss
        target = decision.target
        if getattr(self.risk.cfg, "fixed_target_pct", None) is not None and getattr(self.risk.cfg, "fixed_stoploss_pct", None) is not None:
            stop_loss, target = calculate_fixed_pct_levels(
                decision.side, fill,
                float(self.risk.cfg.fixed_target_pct),
                float(self.risk.cfg.fixed_stoploss_pct),
                float(self.risk.cfg.tick_size)
            )

        self.position = OpenTrade(
            side=decision.side, quantity=decision.quantity, entry_price=fill,
            stop_loss=stop_loss, target=target,
            signal_time=signal.timestamp, entry_time=row["timestamp"],
            score=signal.score, regime=regime,
            entry_slippage_cost=round(abs(fill - reference) * decision.quantity, 2),
            peak_price=fill,
            trough_price=fill,
            trailing_active=False,
        )

    def _fill_pending(self, row: pd.Series) -> None:
        decision, signal, regime = self.pending
        self.pending = None
        leg = "BUY" if decision.side == "LONG" else "SELL"
        reference = row["open"]
        fill = apply_slippage(reference, leg, self.cost_cfg.slippage_pct,
                              cfg=self.cost_cfg, quantity=decision.quantity)

        stop_loss = decision.stop_loss
        target = decision.target
        if getattr(self.risk.cfg, "fixed_target_pct", None) is not None and getattr(self.risk.cfg, "fixed_stoploss_pct", None) is not None:
            stop_loss, target = calculate_fixed_pct_levels(
                decision.side, fill,
                float(self.risk.cfg.fixed_target_pct),
                float(self.risk.cfg.fixed_stoploss_pct),
                float(self.risk.cfg.tick_size)
            )

        self.position = OpenTrade(
            side=decision.side, quantity=decision.quantity, entry_price=fill,
            stop_loss=stop_loss, target=target,
            signal_time=signal.timestamp, entry_time=row["timestamp"],
            score=signal.score, regime=regime,
            entry_slippage_cost=round(abs(fill - reference) * decision.quantity, 2),
            peak_price=fill,
            trough_price=fill,
            trailing_active=False,
        )

    # ---------- exits ----------
    def _check_stop_target(self, row: pd.Series) -> None:
        p = self.position
        if p is None:
            return
        ts, o = row["timestamp"], row["open"]

        trailing_enabled = bool(
            getattr(self.bt_cfg, "trailing_sl_enabled", False)
            or getattr(self.risk.cfg, "trailing_sl_enabled", False)
        )
        trail_beyond = bool(
            getattr(self.bt_cfg, "trail_beyond_target", False)
            or getattr(self.risk.cfg, "trail_beyond_target", False)
        )
        fixed_target_pct = getattr(self.risk.cfg, "fixed_target_pct", None)
        fixed_stoploss_pct = getattr(self.risk.cfg, "fixed_stoploss_pct", None)
        tick_size = getattr(self.risk.cfg, "tick_size", 0.05)

        # Stop is checked BEFORE target: if both are touched in one candle we
        # can't know the order, so we assume the worse outcome.
        if p.side == "LONG":
            p.peak_price = max(getattr(p, "peak_price", p.entry_price), row["high"])
            if row["low"] <= p.stop_loss:
                if o < p.stop_loss:
                    reason = "STOP_GAP"
                elif p.trailing_active or p.stop_loss >= p.entry_price:
                    reason = "TRAILING_STOP"
                else:
                    reason = "STOP"
                self._exit(min(p.stop_loss, o), ts, reason, slippage=True)
                return
            elif not trail_beyond and row["high"] >= p.target:
                self._exit(max(p.target, o), ts, "TARGET", slippage=False)
                return

            # Update Trailing Stop for subsequent candles if enabled
            if trailing_enabled and fixed_target_pct and fixed_stoploss_pct:
                p.peak_price = max(getattr(p, "peak_price", p.entry_price), row["high"])
                # Model B: Milestone 1 - 50% of Target % triggers Breakeven (entry price)
                be_trigger = p.entry_price * (1.0 + 0.5 * (float(fixed_target_pct) / 100.0))
                if p.peak_price >= be_trigger:
                    cand_sl = p.entry_price
                    # Milestone 2: As peak climbs higher, trail by fixed_stoploss_pct
                    trailed_sl = p.peak_price * (1.0 - (float(fixed_stoploss_pct) / 100.0))
                    if trailed_sl > cand_sl:
                        cand_sl = trailed_sl
                    cand_sl = round_to_tick(cand_sl, tick_size)
                    if cand_sl > p.stop_loss:
                        p.stop_loss = cand_sl
                        p.trailing_active = True
        else:
            p.trough_price = min(getattr(p, "trough_price", p.entry_price), row["low"])
            if row["high"] >= p.stop_loss:
                if o > p.stop_loss:
                    reason = "STOP_GAP"
                elif p.trailing_active or p.stop_loss <= p.entry_price:
                    reason = "TRAILING_STOP"
                else:
                    reason = "STOP"
                self._exit(max(p.stop_loss, o), ts, reason, slippage=True)
                return
            elif not trail_beyond and row["low"] <= p.target:
                self._exit(min(p.target, o), ts, "TARGET", slippage=False)
                return

            # Update Trailing Stop for subsequent candles if enabled
            if trailing_enabled and fixed_target_pct and fixed_stoploss_pct:
                p.trough_price = min(getattr(p, "trough_price", p.entry_price), row["low"])
                # Model B: Milestone 1 - 50% of Target % triggers Breakeven (entry price)
                be_trigger = p.entry_price * (1.0 - 0.5 * (float(fixed_target_pct) / 100.0))
                if p.trough_price <= be_trigger:
                    cand_sl = p.entry_price
                    # Milestone 2: As trough drops lower, trail by fixed_stoploss_pct
                    trailed_sl = p.trough_price * (1.0 + (float(fixed_stoploss_pct) / 100.0))
                    if trailed_sl < cand_sl:
                        cand_sl = trailed_sl
                    cand_sl = round_to_tick(cand_sl, tick_size)
                    if cand_sl < p.stop_loss:
                        p.stop_loss = cand_sl
                        p.trailing_active = True

    def _exit(self, reference: float, ts, reason: str, slippage: bool) -> None:
        p = self.position
        exit_leg = "SELL" if p.side == "LONG" else "BUY"
        entry_leg = "BUY" if p.side == "LONG" else "SELL"
        slip = self.cost_cfg.slippage_pct if slippage else 0.0
        slip_cfg = self.cost_cfg if slippage else None

        fill = apply_slippage(reference, exit_leg, slip, cfg=slip_cfg, quantity=p.quantity)
        direction = 1 if p.side == "LONG" else -1
        gross = round(direction * (fill - p.entry_price) * p.quantity, 2)
        costs = round(leg_cost(entry_leg, p.entry_price, p.quantity, self.cost_cfg)
                      + leg_cost(exit_leg, fill, p.quantity, self.cost_cfg), 2)
        net = round(gross - costs, 2)
        slip_cost = round(p.entry_slippage_cost + abs(fill - reference) * p.quantity, 2)

        if p.side == "LONG":
            peak = max(getattr(p, "peak_price", p.entry_price), p.entry_price)
            peak_pct = round(max(0.0, (peak - p.entry_price) / p.entry_price * 100.0), 2) if p.entry_price > 0 else 0.0
        else:
            trough = min(getattr(p, "trough_price", p.entry_price), p.entry_price)
            peak_pct = round(max(0.0, (p.entry_price - trough) / p.entry_price * 100.0), 2) if p.entry_price > 0 else 0.0

        self.trades.append(Trade(
            symbol=self.symbol, side=p.side, strategy_version=self.strategy_cfg.version,
            regime=p.regime, score=p.score, signal_time=p.signal_time,
            entry_time=p.entry_time, exit_time=ts, quantity=p.quantity,
            entry_price=p.entry_price, exit_price=fill, stop_loss=p.stop_loss,
            target=p.target, exit_reason=reason, gross_pnl=gross, costs=costs,
            slippage_cost=slip_cost, net_pnl=net, peak_gain_pct=peak_pct,
        ))
        self.daily_pnl += net
        self.consecutive_losses = self.consecutive_losses + 1 if net < 0 else 0
        self.last_exit_time = ts
        self.position = None
        logger.info("CLOSED %s %s qty=%d in=%.2f out=%.2f %s net=%.2f",
                    p.side, self.symbol, p.quantity, p.entry_price, fill, reason, net)


def run_backtest(df: pd.DataFrame, symbol: str, strategy_cfg: StrategyConfig,
                 risk_cfg: RiskConfig, cost_cfg: CostConfig, bt_cfg: BacktestConfig = None):
    """Returns (trades DataFrame, the Backtester, which holds signal/rejection counters)."""
    bt = Backtester(symbol, strategy_cfg, risk_cfg, cost_cfg, bt_cfg or BacktestConfig())
    return bt.run(df), bt
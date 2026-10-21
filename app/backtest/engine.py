import logging
from collections import Counter
from dataclasses import asdict, dataclass, fields
from typing import Optional

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
    fo_mode: str = "Normal Mode"  # "Normal Mode" or "OIPulse Mode"


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
    contract_symbol: str = ""
    opt_exp_dt: Optional[object] = None
    opt_strike: Optional[float] = None
    opt_type: Optional[str] = None
    index_ltp: Optional[float] = None


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
    opt_type: Optional[str] = None
    index_ltp: Optional[float] = None


class Backtester:
    def __init__(self, symbol: str, strategy_cfg: StrategyConfig, risk_cfg: RiskConfig,
                 cost_cfg: CostConfig, bt_cfg: BacktestConfig):
        self.symbol = symbol
        self.strategy_cfg = strategy_cfg
        self.cost_cfg = cost_cfg
        self.bt_cfg = bt_cfg
        # Ensure snooze_minutes and other strategy-level risk settings are reliably populated
        snooze = (
            getattr(risk_cfg, "snooze_minutes", 0)
            or getattr(bt_cfg, "snooze_minutes", 0)
            or getattr(strategy_cfg, "snooze_minutes", 0)
            or 0
        )
        replacements = {}
        if snooze and getattr(risk_cfg, "snooze_minutes", 0) != snooze:
            replacements["snooze_minutes"] = snooze
        if getattr(strategy_cfg, "fixed_target_pct", None) is not None and getattr(risk_cfg, "fixed_target_pct", None) is None:
            replacements["fixed_target_pct"] = getattr(strategy_cfg, "fixed_target_pct")
        if getattr(strategy_cfg, "fixed_stoploss_pct", None) is not None and getattr(risk_cfg, "fixed_stoploss_pct", None) is None:
            replacements["fixed_stoploss_pct"] = getattr(strategy_cfg, "fixed_stoploss_pct")
        if getattr(strategy_cfg, "trailing_sl_enabled", False) and not getattr(risk_cfg, "trailing_sl_enabled", False):
            replacements["trailing_sl_enabled"] = True
        if getattr(strategy_cfg, "trail_beyond_target", False) and not getattr(risk_cfg, "trail_beyond_target", False):
            replacements["trail_beyond_target"] = True
        if getattr(strategy_cfg, "trade_side", None) and getattr(risk_cfg, "trade_side", "BOTH") == "BOTH":
            replacements["trade_side"] = getattr(strategy_cfg, "trade_side")
        if replacements:
            import dataclasses
            risk_cfg = dataclasses.replace(risk_cfg, **replacements)
        self.risk = RiskEngine(risk_cfg)

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
            p = self.position
            if getattr(p, "opt_strike", None) is not None and p.opt_exp_dt is not None:
                from app.strategy.oipulse_resolver import calculate_option_price
                exit_ref = calculate_option_price(prev["close"], p.opt_strike, p.opt_type, prev["timestamp"], p.opt_exp_dt)
            else:
                exit_ref = prev["close"]
            self._exit(exit_ref, prev["timestamp"], "END_OF_DATA", slippage=True)

        columns = [f.name for f in fields(Trade)]
        return pd.DataFrame([asdict(t) for t in self.trades], columns=columns)

    def _start_new_day(self, ts, prev) -> None:
        if self.position is not None and prev is not None:   # safety net; square-off should prevent this
            p = self.position
            if getattr(p, "opt_strike", None) is not None and p.opt_exp_dt is not None:
                from app.strategy.oipulse_resolver import calculate_option_price
                exit_ref = calculate_option_price(prev["close"], p.opt_strike, p.opt_type, prev["timestamp"], p.opt_exp_dt)
            else:
                exit_ref = prev["close"]
            self._exit(exit_ref, prev["timestamp"], "FORCED_DAY_END", slippage=True)
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
                p = self.position
                if getattr(p, "opt_strike", None) is not None and p.opt_exp_dt is not None:
                    from app.strategy.oipulse_resolver import calculate_option_price
                    exit_ref = calculate_option_price(row["open"], p.opt_strike, p.opt_type, ts, p.opt_exp_dt)
                else:
                    exit_ref = row["open"]
                self._exit(exit_ref, ts, "SQUARE_OFF", slippage=True)
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
        fo_mode = getattr(self.bt_cfg, "fo_mode", "Normal Mode")
        dt_val = row["timestamp"]
        if hasattr(dt_val, "to_pydatetime"):
            dt_val = dt_val.to_pydatetime()
        elif isinstance(dt_val, str):
            from datetime import datetime as _dt
            try:
                dt_val = _dt.fromisoformat(dt_val.replace("Z", "+00:00"))
            except Exception:
                pass

        if fo_mode == "OIPulse Mode":
            # 1. Evaluate Strength from row or DB
            from app.strategy.oipulse_resolver import (
                resolve_oipulse_option,
                parse_strength_value,
                calculate_option_price,
            )
            strength = row.get("strength")
            ltp = row.get("ltp", row.get("close"))

            if strength is None:
                from app.strategy.oipulse_resolver import get_oipulse_record_from_db
                db_ltp, db_strength = get_oipulse_record_from_db(dt_val)
                strength = db_strength
                if ltp is None:
                    ltp = db_ltp

            if strength is None or (-50 <= strength <= 50):
                self.signal_counts["HOLD"] += 1
                self.rejections["OIPulse: Strength within neutral band (-50 to +50)"] += 1
                return

            oi_res = resolve_oipulse_option(
                self.symbol, dt_val, live_ltp=ltp, live_strength=strength
            )
            if not oi_res.get("has_signal") or not oi_res.get("is_valid_symbol"):
                self.signal_counts["HOLD"] += 1
                self.rejections[oi_res.get("validation_reason", "OIPulse: Invalid contract symbol or no signal")] += 1
                return

            resolved_contract_symbol = oi_res["resolved_symbol"]
            opt_type = oi_res["option_type"]  # "CE" or "PE"
            strike = oi_res["strike"]
            exp_date = oi_res["expiry_date"]

            # Calculate realistic option premium price
            opt_price = calculate_option_price(ltp, strike, opt_type, dt_val, exp_date)

            # 2. Watchlist of strategy: apply technical indicators & scoring
            from app.strategy.custom_strategy import score_custom_row
            score = row.get("score")
            if score is None:
                score = sum(score_custom_row(row, self.strategy_cfg).values())

            long_th = getattr(self.strategy_cfg, "long_threshold", 1)
            short_th = getattr(self.strategy_cfg, "short_threshold", -1)

            if opt_type == "CE":
                if score < long_th:
                    self.signal_counts["HOLD"] += 1
                    self.rejections[f"Indicator score ({score}) < LONG threshold ({long_th})"] += 1
                    return
                signal_side = "LONG"
            else:  # "PE"
                if score > short_th:
                    self.signal_counts["HOLD"] += 1
                    self.rejections[f"Indicator score ({score}) > SHORT threshold ({short_th})"] += 1
                    return
                # Option Buying: buying a PE contract is entered as a LONG position in that option
                signal_side = "LONG"

            self.signal_counts[signal_side] += 1

            trade_side = getattr(self.bt_cfg, "trade_side", "BOTH") or getattr(self.risk.cfg, "trade_side", "BOTH") or "BOTH"
            if trade_side == "SHORT" and signal_side == "LONG":
                self.rejections["LONG ignored (SHORT only selected)"] += 1
                return

            from app.strategy.signal_engine import Signal
            ind_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            ind_dict["option_price"] = opt_price
            ind_dict["underlying_ltp"] = ltp

            signal = Signal(
                symbol=resolved_contract_symbol,
                timestamp=dt_val,
                signal=signal_side,
                score=score,
                max_score=getattr(self.strategy_cfg, "max_score", 1),
                confidence=round(abs(score) / max(getattr(self.strategy_cfg, "max_score", 1), 1), 2),
                strategy_version=getattr(self.strategy_cfg, "name", getattr(self.strategy_cfg, "version", "custom")),
                indicators=ind_dict,
            )
            opt_meta = (strike, opt_type, exp_date, opt_price)
        else:
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

            resolved_contract_symbol = self.symbol
            opt_meta = (None, None, None, None)

        state = PortfolioState(open_positions=[], daily_pnl=self.daily_pnl,
                               consecutive_losses=self.consecutive_losses,
                               last_exit_time=self.last_exit_time)
        decision = self.risk.evaluate(signal, state)
        if decision.approved:
            regime = row.get("regime", "") or detect_regime_row(row)
            if getattr(self.bt_cfg, "execution_mode", "SIGNAL_BAR_CLOSE") == "NEXT_BAR_OPEN":
                self.pending = (decision, signal, regime, resolved_contract_symbol, *opt_meta)
            else:
                self._enter_immediate(
                    row, decision, signal, regime,
                    contract_symbol=resolved_contract_symbol,
                    opt_strike=opt_meta[0], opt_type=opt_meta[1],
                    opt_exp_dt=opt_meta[2], opt_price=opt_meta[3]
                )
        else:
            self.rejections[decision.rejection_reason.split(" (")[0]] += 1

    def _enter_immediate(
        self, row: pd.Series, decision, signal, regime: str, contract_symbol: str = "",
        opt_strike: Optional[float] = None, opt_type: Optional[str] = None,
        opt_exp_dt: Optional[object] = None, opt_price: Optional[float] = None
    ) -> None:
        leg = "BUY" if decision.side == "LONG" else "SELL"
        reference = opt_price if opt_price is not None else row["close"]
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
            contract_symbol=contract_symbol or self.symbol,
            opt_exp_dt=opt_exp_dt,
            opt_strike=opt_strike,
            opt_type=opt_type,
            index_ltp=float(row.get("ltp", row.get("close"))) if pd.notna(row.get("ltp", row.get("close"))) else None,
        )

    def _fill_pending(self, row: pd.Series) -> None:
        opt_strike, opt_type, opt_exp_dt = None, None, None
        if len(self.pending) >= 7:
            decision, signal, regime, contract_sym, opt_strike, opt_type, opt_exp_dt, _ = self.pending
        elif len(self.pending) == 4:
            decision, signal, regime, contract_sym = self.pending
        else:
            decision, signal, regime = self.pending
            contract_sym = self.symbol
        self.pending = None
        leg = "BUY" if decision.side == "LONG" else "SELL"
        if opt_strike is not None and opt_exp_dt is not None:
            from app.strategy.oipulse_resolver import calculate_option_price
            reference = calculate_option_price(row["open"], opt_strike, opt_type, row["timestamp"], opt_exp_dt)
        else:
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
            contract_symbol=contract_sym or self.symbol,
            opt_exp_dt=opt_exp_dt,
            opt_strike=opt_strike,
            opt_type=opt_type,
        )

    # ---------- exits ----------
    def _check_stop_target(self, row: pd.Series) -> None:
        p = self.position
        if p is None:
            return
        ts, o = row["timestamp"], row["open"]

        if getattr(p, "opt_strike", None) is not None and p.opt_exp_dt is not None:
            from app.strategy.oipulse_resolver import calculate_option_price
            opt_c = calculate_option_price(row["close"], p.opt_strike, p.opt_type, ts, p.opt_exp_dt)
            opt_o = calculate_option_price(row["open"], p.opt_strike, p.opt_type, ts, p.opt_exp_dt)
            opt_h = calculate_option_price(row["low" if p.opt_type == "PE" else "high"], p.opt_strike, p.opt_type, ts, p.opt_exp_dt)
            opt_l = calculate_option_price(row["high" if p.opt_type == "PE" else "low"], p.opt_strike, p.opt_type, ts, p.opt_exp_dt)
            candle_h = max(opt_h, opt_c, opt_o)
            candle_l = min(opt_l, opt_c, opt_o)
            candle_o = opt_o
        else:
            candle_h = row["high"]
            candle_l = row["low"]
            candle_o = o

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
            p.peak_price = max(getattr(p, "peak_price", p.entry_price), candle_h)
            if candle_l <= p.stop_loss:
                if candle_o < p.stop_loss:
                    reason = "STOP_GAP"
                elif p.trailing_active or p.stop_loss >= p.entry_price:
                    reason = "TRAILING_STOP"
                else:
                    reason = "STOP"
                self._exit(min(p.stop_loss, candle_o), ts, reason, slippage=True)
                return
            elif not trail_beyond and candle_h >= p.target:
                self._exit(max(p.target, candle_o), ts, "TARGET", slippage=False)
                return

            # Update Trailing Stop for subsequent candles if enabled
            if trailing_enabled and fixed_target_pct and fixed_stoploss_pct:
                p.peak_price = max(getattr(p, "peak_price", p.entry_price), candle_h)
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
            p.trough_price = min(getattr(p, "trough_price", p.entry_price), candle_l)
            if candle_h >= p.stop_loss:
                if candle_o > p.stop_loss:
                    reason = "STOP_GAP"
                elif p.trailing_active or p.stop_loss <= p.entry_price:
                    reason = "TRAILING_STOP"
                else:
                    reason = "STOP"
                self._exit(max(p.stop_loss, candle_o), ts, reason, slippage=True)
                return
            elif not trail_beyond and candle_l <= p.target:
                self._exit(min(p.target, candle_o), ts, "TARGET", slippage=False)
                return

            # Update Trailing Stop for subsequent candles if enabled
            if trailing_enabled and fixed_target_pct and fixed_stoploss_pct:
                p.trough_price = min(getattr(p, "trough_price", p.entry_price), candle_l)
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

        t_opt_type = getattr(p, "opt_type", None)
        sym_str = getattr(p, "contract_symbol", "") or self.symbol
        if not t_opt_type:
            import re
            m_ot = re.search(r"(?:\d+[\s\-_]?|\b)(CE|PE)$", sym_str.upper())
            if m_ot:
                t_opt_type = m_ot.group(1)

        self.trades.append(Trade(
            symbol=getattr(p, "contract_symbol", "") or self.symbol, side=p.side, strategy_version=self.strategy_cfg.version,
            regime=p.regime, score=p.score, signal_time=p.signal_time,
            entry_time=p.entry_time, exit_time=ts, quantity=p.quantity,
            entry_price=p.entry_price, exit_price=fill, stop_loss=p.stop_loss,
            target=p.target, exit_reason=reason, gross_pnl=gross, costs=costs,
            slippage_cost=slip_cost, net_pnl=net, peak_gain_pct=peak_pct,
            opt_type=t_opt_type,
            index_ltp=getattr(p, "index_ltp", None),
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd

from app.strategy.custom_strategy import (
    CustomStrategyConfig,
    calculate_custom_indicators,
    generate_custom_signal,
    score_custom_row,
)
from app.strategy.strategy_store import get_strategy


@dataclass
class StreamingSignal:
    timestamp: object
    symbol: str
    decision: str            # "LONG", "SHORT", "HOLD"
    score: int
    components: Dict[str, int]
    indicators: Dict[str, float]
    close: float
    reasons: List[str] = field(default_factory=list)


class StreamingStrategyEngine:
    """
    Consumes completed candles sequentially, maintains a rolling window,
    calculates 12 technical indicators dynamically, and evaluates strategy rules.
    """

    def __init__(
        self,
        symbol: str,
        custom_cfg: CustomStrategyConfig,
        trade_side: str = "BOTH",
        snooze_minutes: int = 0,
        max_buffer_size: int = 250,
    ):
        self.symbol = symbol
        self.custom_cfg = custom_cfg
        self.trade_side = trade_side.upper()
        self.snooze_minutes = snooze_minutes
        self.max_buffer_size = max_buffer_size

        self._history_candles: List[pd.Series] = []
        self._last_exit_time: Optional[datetime] = None
        self._indicators_df: Optional[pd.DataFrame] = None
        self._latest_signal: Optional[StreamingSignal] = None

    @classmethod
    def from_saved_strategy(
        cls,
        symbol: str,
        strategy_name: str,
        max_buffer_size: int = 250,
    ) -> "StreamingStrategyEngine":
        """Instantiate directly from a saved strategy in data/saved_strategies.json."""
        strat_dict = get_strategy(strategy_name)
        if not strat_dict:
            raise ValueError(f"Strategy '{strategy_name}' not found in saved strategies.")

        ind = strat_dict.get("indicators", {})
        risk = strat_dict.get("risk", {})

        custom_cfg = CustomStrategyConfig(
            name=strategy_name,
            ema_enabled=ind.get("ema_enabled", True),
            ema_fast=ind.get("ema_fast", 20),
            ema_slow=ind.get("ema_slow", 50),
            ema_weight=ind.get("ema_weight", 2),
            rsi_enabled=ind.get("rsi_enabled", True),
            rsi_period=ind.get("rsi_period", 14),
            rsi_bull_low=ind.get("rsi_bull_low", 55.0),
            rsi_bull_high=ind.get("rsi_bull_high", 70.0),
            rsi_bear_low=ind.get("rsi_bear_low", 30.0),
            rsi_bear_high=ind.get("rsi_bear_high", 45.0),
            rsi_weight=ind.get("rsi_weight", 1),
            macd_enabled=ind.get("macd_enabled", True),
            macd_fast=ind.get("macd_fast", 12),
            macd_slow=ind.get("macd_slow", 26),
            macd_signal=ind.get("macd_signal", 9),
            macd_weight=ind.get("macd_weight", 2),
            vwap_enabled=ind.get("vwap_enabled", True),
            vwap_weight=ind.get("vwap_weight", 1),
            atr_enabled=ind.get("atr_enabled", True),
            atr_period=ind.get("atr_period", 14),
            atr_stop_multiplier=risk.get("atr_stop_multiplier", 1.5),
            atr_reward_risk_ratio=risk.get("reward_risk_ratio", 2.0),
            adx_enabled=ind.get("adx_enabled", True),
            adx_period=ind.get("adx_period", 14),
            adx_min=ind.get("adx_min", 20.0),
            adx_weight=ind.get("adx_weight", 1),
            volume_enabled=ind.get("volume_enabled", True),
            volume_period=ind.get("volume_period", 20),
            volume_ratio_min=ind.get("volume_ratio_min", 1.2),
            volume_consecutive=int(ind.get("volume_consecutive", 3)),
            volume_weight=ind.get("volume_weight", 1),
            bollinger_enabled=ind.get("bollinger_enabled", True),
            bollinger_period=ind.get("bollinger_period", 20),
            bollinger_std_dev=ind.get("bollinger_std_dev", 2.0),
            bollinger_weight=ind.get("bollinger_weight", 1),
            stoch_enabled=ind.get("stoch_enabled", True),
            stoch_k=ind.get("stoch_k", 14),
            stoch_d=ind.get("stoch_d", 3),
            stoch_low=ind.get("stoch_low", 20.0),
            stoch_high=ind.get("stoch_high", 80.0),
            stoch_weight=ind.get("stoch_weight", 1),
            obv_enabled=ind.get("obv_enabled", True),
            obv_ma_period=ind.get("obv_ma_period", 20),
            obv_weight=ind.get("obv_weight", 1),
            higher_high_enabled=bool(ind.get("higher_high_enabled", False)),
            higher_high_period=int(ind.get("higher_high_period", 5)),
            higher_high_weight=int(ind.get("higher_high_weight", 1)),
            lower_low_enabled=bool(ind.get("lower_low_enabled", False)),
            lower_low_period=int(ind.get("lower_low_period", 5)),
            lower_low_weight=int(ind.get("lower_low_weight", 1)),
            hma_enabled=bool(ind.get("hma_enabled", False)),
            hma_period=int(ind.get("hma_period", 100)),
            hma_weight=int(ind.get("hma_weight", 1)),
            supertrend_enabled=bool(ind.get("supertrend_enabled", False)),
            supertrend_period=int(ind.get("supertrend_period", 10)),
            supertrend_mult_simple=float(ind.get("supertrend_mult_simple", 2.0)),
            supertrend_mult_complex=float(ind.get("supertrend_mult_complex", 4.0)),
            supertrend_weight=int(ind.get("supertrend_weight", 1)),
            long_threshold=ind.get("long_threshold", 5),
            short_threshold=ind.get("short_threshold", -5),
            fixed_target_pct=float(ind.get("fixed_target_pct", 3)),
            fixed_stoploss_pct=float(ind.get("fixed_stoploss_pct", 2)),
            trailing_sl_enabled=bool(ind.get("trailing_sl_enabled", False)),
            trail_beyond_target=bool(ind.get("trail_beyond_target", False)),
        )

        trade_side = ind.get("trade_side", risk.get("trade_side", "BOTH"))
        snooze_min = int(ind.get("snooze_minutes", risk.get("snooze_minutes", 0)))

        return cls(
            symbol=symbol,
            custom_cfg=custom_cfg,
            trade_side=trade_side,
            snooze_minutes=snooze_min,
            max_buffer_size=max_buffer_size,
        )

    def record_exit(self, exit_time: datetime) -> None:
        """Mark when a trade closed to enforce snooze periods."""
        self._last_exit_time = exit_time

    def reset(self) -> None:
        self._history_candles.clear()
        self._last_exit_time = None
        self._indicators_df = None
        self._latest_signal = None

    @property
    def latest_signal(self) -> Optional[StreamingSignal]:
        return self._latest_signal

    @property
    def latest_indicators_df(self) -> Optional[pd.DataFrame]:
        return self._indicators_df

    def on_candle(self, candle: pd.Series) -> StreamingSignal:
        """Process one completed candle and evaluate strategy rules."""
        self._history_candles.append(candle)
        if len(self._history_candles) > self.max_buffer_size:
            self._history_candles.pop(0)

        raw_df = pd.DataFrame(self._history_candles).reset_index(drop=True)
        if "timestamp" in raw_df.columns:
            raw_df["timestamp"] = pd.to_datetime(raw_df["timestamp"])

        self._indicators_df = calculate_custom_indicators(raw_df, self.custom_cfg)
        latest_row = self._indicators_df.iloc[-1]

        # Generate base signal from custom strategy
        base_signal = generate_custom_signal(latest_row, self.symbol, self.custom_cfg)
        decision = base_signal.signal
        score = int(base_signal.score)
        reasons = list(base_signal.reasons)

        # 1. Trade Side Filter
        if self.trade_side == "LONG" and decision == "SHORT":
            decision = "HOLD"
            reasons.append("Filtered: trade_side set to LONG only")
        elif self.trade_side == "SHORT" and decision == "LONG":
            decision = "HOLD"
            reasons.append("Filtered: trade_side set to SHORT only")

        # 2. Snooze Period Check
        ts = candle["timestamp"]
        ts_dt = pd.to_datetime(ts)
        if self.snooze_minutes > 0 and self._last_exit_time is not None:
            if ts_dt < self._last_exit_time + timedelta(minutes=self.snooze_minutes):
                if decision in ("LONG", "SHORT"):
                    reasons.append(f"Snoozed: waiting {self.snooze_minutes}m after exit")
                    decision = "HOLD"

        # Build indicators telemetry dictionary
        indicators_snapshot = {}
        for col in [
            "ema20", "ema50", "rsi14", "macd", "macd_signal", "macd_hist",
            "vwap", "atr14", "adx", "plus_di", "minus_di", "bb_mid",
            "bb_upper", "bb_lower", "stoch_k", "stoch_d", "obv", "obv_ma",
            "volume_ratio"
        ]:
            if col in latest_row and pd.notna(latest_row[col]):
                indicators_snapshot[col] = float(latest_row[col])

        components = score_custom_row(latest_row, self.custom_cfg)

        self._latest_signal = StreamingSignal(
            timestamp=ts,
            symbol=self.symbol,
            decision=decision,
            score=score,
            components=components,
            indicators=indicators_snapshot,
            close=float(candle["close"]),
            reasons=reasons,
        )

        return self._latest_signal

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from app.indicators.adx import adx
from app.indicators.atr import atr
from app.indicators.bollinger import bollinger_bands
from app.indicators.ema import ema
from app.indicators.macd import macd
from app.indicators.obv import on_balance_volume
from app.indicators.regime import detect_regime
from app.indicators.rsi import rsi
from app.indicators.stochastic import stochastic
from app.indicators.volume import volume_features
from app.indicators.vwap import vwap
from app.strategy.signal_engine import Signal

logger = logging.getLogger(__name__)


@dataclass
class IndicatorSetting:
    enabled: bool = True
    weight: int = 1
    params: dict = field(default_factory=dict)


@dataclass
class CustomStrategyConfig:
    # 10 configurable indicators
    ema_enabled: bool = True
    ema_fast: int = 20
    ema_slow: int = 50
    ema_weight: int = 2

    rsi_enabled: bool = True
    rsi_period: int = 14
    rsi_bull_low: float = 55.0
    rsi_bull_high: float = 70.0
    rsi_bear_low: float = 30.0
    rsi_bear_high: float = 45.0
    rsi_weight: int = 1

    macd_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_weight: int = 2

    vwap_enabled: bool = True
    vwap_weight: int = 1

    atr_enabled: bool = True
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5
    atr_reward_risk_ratio: float = 2.0
    # ATR is used for stop and target sizing, not scoring (weight 0)

    adx_enabled: bool = True
    adx_period: int = 14
    adx_min: float = 20.0
    adx_weight: int = 1

    volume_enabled: bool = True
    volume_period: int = 20
    volume_ratio_min: float = 1.2
    volume_consecutive: int = 3
    volume_weight: int = 1

    bollinger_enabled: bool = True
    bollinger_period: int = 20
    bollinger_std_dev: float = 2.0
    bollinger_weight: int = 1

    stoch_enabled: bool = True
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_low: float = 20.0
    stoch_high: float = 80.0
    stoch_weight: int = 1

    obv_enabled: bool = True
    obv_ma_period: int = 20
    obv_weight: int = 1

    # 11. Higher High (indicator 11: current candle high > previous swing high)
    higher_high_enabled: bool = False
    higher_high_period: int = 5
    higher_high_weight: int = 1

    # 12. Lower Low (indicator 12: current candle low < previous swing low)
    lower_low_enabled: bool = False
    lower_low_period: int = 5
    lower_low_weight: int = 1

    # 13. HMA (indicator 13: Hull Moving Average 100)
    hma_enabled: bool = False
    hma_period: int = 100
    hma_weight: int = 1

    # 14. Supertrend (indicator 14: (10,2) simple and (10,4) complex trend applied by default)
    supertrend_enabled: bool = False
    supertrend_period: int = 10
    supertrend_mult_simple: float = 2.0
    supertrend_mult_complex: float = 4.0
    supertrend_weight: int = 1

    # Score thresholds
    long_threshold: int = 5
    short_threshold: int = -5

    # Fixed Target and Stoploss percentages
    fixed_target_pct: float = 3.0
    fixed_stoploss_pct: float = 2.0
    trailing_sl_enabled: bool = False
    trail_beyond_target: bool = False

    name: str = ""

    # Marker for compatibility
    is_custom: bool = True

    @property
    def max_score(self) -> int:
        total = 0
        if self.ema_enabled:
            total += self.ema_weight
        if self.rsi_enabled:
            total += self.rsi_weight
        if self.macd_enabled:
            total += self.macd_weight
        if self.vwap_enabled:
            total += self.vwap_weight
        if self.adx_enabled:
            total += self.adx_weight
        if self.volume_enabled:
            total += self.volume_weight
        if self.bollinger_enabled:
            total += self.bollinger_weight
        if self.stoch_enabled:
            total += self.stoch_weight
        if self.obv_enabled:
            total += self.obv_weight
        if self.higher_high_enabled:
            total += self.higher_high_weight
        if self.lower_low_enabled:
            total += self.lower_low_weight
        if self.hma_enabled:
            total += self.hma_weight
        if self.supertrend_enabled:
            total += self.supertrend_weight
        return max(total, 1)

    @property
    def version(self) -> str:
        if self.name and self.name.strip():
            return self.name.strip().replace(" ", "_")
        d = asdict(self)
        raw = json.dumps(d, sort_keys=True, default=str)
        short_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return f"custom_{short_hash}"

    def __post_init__(self):
        # Validate periods & weights
        if self.ema_fast < 1 or self.ema_slow < 1:
            raise ValueError("EMA fast and slow periods must be >= 1")
        if self.rsi_period < 1:
            raise ValueError("RSI period must be >= 1")
        if self.macd_fast < 1 or self.macd_slow < 1 or self.macd_signal < 1:
            raise ValueError("MACD periods must be >= 1")
        if self.atr_period < 1 or self.atr_stop_multiplier <= 0 or self.atr_reward_risk_ratio <= 0:
            raise ValueError("ATR period and multipliers must be positive")
        if self.adx_period < 1 or self.adx_min < 0:
            raise ValueError("ADX period and min must be positive")
        if self.bollinger_period < 1 or self.bollinger_std_dev <= 0:
            raise ValueError("Bollinger period and std_dev must be positive")
        if self.stoch_k < 1 or self.stoch_d < 1:
            raise ValueError("Stochastic periods must be >= 1")
        if self.obv_ma_period < 1:
            raise ValueError("OBV MA period must be >= 1")
        if self.higher_high_period < 1:
            raise ValueError("Higher High swing period must be >= 1")
        if self.lower_low_period < 1:
            raise ValueError("Lower Low swing period must be >= 1")
        if self.hma_period < 1:
            raise ValueError("HMA period must be >= 1")
        if self.supertrend_period < 1 or self.supertrend_mult_simple <= 0 or self.supertrend_mult_complex <= 0:
            raise ValueError("Supertrend period and multipliers must be positive")
        if self.volume_period < 1 or self.volume_ratio_min <= 0:
            raise ValueError("Volume period must be >= 1 and volume_ratio_min must be positive")
        if not (1 <= self.volume_consecutive <= 5):
            raise ValueError("Volume consecutive period must be between 1 and 5")
        if not (1 <= self.fixed_target_pct <= 50):
            raise ValueError("fixed_target_pct must be between 1 and 50")
        if not (1 <= self.fixed_stoploss_pct <= 10):
            raise ValueError("fixed_stoploss_pct must be between 1 and 10")

        # Thresholds validation
        m = self.max_score
        if not (0 < self.long_threshold <= m):
            raise ValueError(f"long_threshold ({self.long_threshold}) must be between 1 and max_score ({m})")
        if not (-m <= self.short_threshold < 0):
            raise ValueError(f"short_threshold ({self.short_threshold}) must be between -max_score ({-m}) and -1")


def calculate_custom_indicators(df: pd.DataFrame, cfg: CustomStrategyConfig) -> pd.DataFrame:
    """Computes all enabled indicators for the given CustomStrategyConfig."""
    out = df.copy().reset_index(drop=True)

    if cfg.ema_enabled:
        out["ema20"] = ema(out["close"], cfg.ema_fast)
        out["ema50"] = ema(out["close"], cfg.ema_slow)
    if cfg.rsi_enabled:
        out["rsi14"] = rsi(out["close"], cfg.rsi_period)
    if cfg.macd_enabled:
        out = out.join(macd(out["close"], fast=cfg.macd_fast, slow=cfg.macd_slow, signal=cfg.macd_signal))
    if cfg.vwap_enabled:
        out["vwap"] = vwap(out)
    if cfg.atr_enabled:
        out["atr14"] = atr(out, cfg.atr_period)
    else:
        # Fallback ATR for dynamic stop loss & position sizing in RiskEngine
        period = getattr(cfg, "atr_period", 14)
        out["atr14"] = atr(out, period if period > 1 else 14)
    if cfg.adx_enabled:
        out = out.join(adx(out, cfg.adx_period))
    if cfg.volume_enabled:
        vol_cols = [c for c in ["volume_ma", "volume_ratio"] if c in out.columns]
        if vol_cols:
            out = out.drop(columns=vol_cols)
        out = out.join(volume_features(out, cfg.volume_period))
        consec_k = int(getattr(cfg, "volume_consecutive", 3))

        # Reset consecutive period per trading day to prevent overnight bleed
        day_series = None
        if "timestamp" in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out["timestamp"]):
                day_series = out["timestamp"].dt.date
            else:
                try:
                    day_series = pd.to_datetime(out["timestamp"]).dt.date
                except Exception:
                    day_series = None
        elif isinstance(out.index, pd.DatetimeIndex):
            day_series = out.index.date

        # Intraday price direction: 9:15 candle has no previous candle check within the day
        if day_series is not None:
            prev_close = out.groupby(day_series, sort=False)["close"].shift(1)
        else:
            prev_close = out["close"].shift(1)

        close_up = (out["close"] > prev_close).astype(int)
        close_down = (out["close"] < prev_close).astype(int)
        vol_ok = (out["volume_ratio"] >= cfg.volume_ratio_min).astype(int)

        # Each candle must satisfy BOTH price close direction AND volume ratio >= min
        up_and_vol = ((close_up == 1) & (vol_ok == 1)).astype(int)
        down_and_vol = ((close_down == 1) & (vol_ok == 1)).astype(int)

        if day_series is not None:
            rolled_up = up_and_vol.groupby(day_series, sort=False).rolling(window=consec_k, min_periods=consec_k).sum()
            rolled_down = down_and_vol.groupby(day_series, sort=False).rolling(window=consec_k, min_periods=consec_k).sum()
            if isinstance(rolled_up.index, pd.MultiIndex):
                is_bullish = (rolled_up.droplevel(0).values == consec_k)
                is_bearish = (rolled_down.droplevel(0).values == consec_k)
            else:
                is_bullish = (rolled_up.values == consec_k)
                is_bearish = (rolled_down.values == consec_k)
        else:
            is_bullish = (up_and_vol.rolling(window=consec_k, min_periods=consec_k).sum().values == consec_k)
            is_bearish = (down_and_vol.rolling(window=consec_k, min_periods=consec_k).sum().values == consec_k)

        out["volume_trend_bullish"] = is_bullish
        out["volume_trend_bearish"] = is_bearish
        out["volume_consec_met"] = (is_bullish | is_bearish)
    if cfg.bollinger_enabled:
        out = out.join(bollinger_bands(out["close"], period=cfg.bollinger_period, std_dev=cfg.bollinger_std_dev))
    if cfg.stoch_enabled:
        out = out.join(stochastic(out, k_period=cfg.stoch_k, d_period=cfg.stoch_d))
    if cfg.obv_enabled:
        out = out.join(on_balance_volume(out, ma_period=cfg.obv_ma_period))
    if cfg.higher_high_enabled:
        from app.indicators.swing import swing_high_low
        hh_df = swing_high_low(out, period=cfg.higher_high_period)
        out["swing_high"] = hh_df["swing_high"]
        out["higher_high"] = hh_df["higher_high"]
    if cfg.lower_low_enabled:
        from app.indicators.swing import swing_high_low
        ll_df = swing_high_low(out, period=cfg.lower_low_period)
        out["swing_low"] = ll_df["swing_low"]
        out["lower_low"] = ll_df["lower_low"]

    if cfg.hma_enabled:
        from app.indicators.hma import hma
        out["hma"] = hma(out["close"], period=cfg.hma_period)

    if cfg.supertrend_enabled:
        from app.indicators.supertrend import supertrend
        st_sim = supertrend(out, period=cfg.supertrend_period, multiplier=cfg.supertrend_mult_simple)
        st_cpx = supertrend(out, period=cfg.supertrend_period, multiplier=cfg.supertrend_mult_complex)
        out["supertrend_simple"] = st_sim["supertrend"]
        out["supertrend_simple_trend"] = st_sim["trend"]
        out["supertrend_complex"] = st_cpx["supertrend"]
        out["supertrend_complex_trend"] = st_cpx["trend"]

    if "regime" not in out.columns:
        out["regime"] = detect_regime(out)

    if "score" not in out.columns:
        try:
            out["score"] = [sum(score_custom_row(r, cfg).values()) for _, r in out.iterrows()]
        except Exception:
            pass

    return out


def score_custom_row(row: pd.Series, cfg: CustomStrategyConfig) -> Dict[str, int]:
    """Scores a single candle according to the enabled indicator rules."""
    components = {}

    # 1. EMA
    if cfg.ema_enabled:
        if row.get("ema20") is not None and row.get("ema50") is not None:
            if row["ema20"] > row["ema50"]:
                components["ema"] = cfg.ema_weight
            elif row["ema20"] < row["ema50"]:
                components["ema"] = -cfg.ema_weight
            else:
                components["ema"] = 0

    # 2. RSI
    if cfg.rsi_enabled:
        rsi_val = row.get("rsi14")
        if rsi_val is not None:
            if cfg.rsi_bull_low <= rsi_val <= cfg.rsi_bull_high:
                components["rsi"] = cfg.rsi_weight
            elif cfg.rsi_bear_low <= rsi_val <= cfg.rsi_bear_high:
                components["rsi"] = -cfg.rsi_weight
            else:
                components["rsi"] = 0

    # 3. MACD
    if cfg.macd_enabled:
        hist = row.get("macd_hist")
        if hist is not None:
            if hist > 0:
                components["macd"] = cfg.macd_weight
            elif hist < 0:
                components["macd"] = -cfg.macd_weight
            else:
                components["macd"] = 0

    # 4. VWAP
    if cfg.vwap_enabled:
        c, v = row.get("close"), row.get("vwap")
        if c is not None and v is not None:
            if c > v:
                components["vwap"] = cfg.vwap_weight
            elif c < v:
                components["vwap"] = -cfg.vwap_weight
            else:
                components["vwap"] = 0

    # 5. ADX
    if cfg.adx_enabled:
        adx_val = row.get("adx")
        p_di = row.get("plus_di")
        m_di = row.get("minus_di")
        if adx_val is not None and p_di is not None and m_di is not None:
            if adx_val >= cfg.adx_min:
                if p_di > m_di:
                    components["adx"] = cfg.adx_weight
                elif m_di > p_di:
                    components["adx"] = -cfg.adx_weight
                else:
                    components["adx"] = 0
            else:
                components["adx"] = 0

    # 6. Bollinger Bands
    if cfg.bollinger_enabled:
        c = row.get("close")
        mid = row.get("bb_mid")
        upper = row.get("bb_upper")
        lower = row.get("bb_lower")
        if c is not None and mid is not None and upper is not None and lower is not None:
            if mid < c <= upper:
                components["bollinger"] = cfg.bollinger_weight
            elif lower <= c < mid:
                components["bollinger"] = -cfg.bollinger_weight
            else:
                components["bollinger"] = 0

    # 7. Stochastic
    if cfg.stoch_enabled:
        k = row.get("stoch_k")
        d = row.get("stoch_d")
        if k is not None and d is not None:
            if cfg.stoch_low <= k <= cfg.stoch_high:
                if k > d:
                    components["stochastic"] = cfg.stoch_weight
                elif k < d:
                    components["stochastic"] = -cfg.stoch_weight
                else:
                    components["stochastic"] = 0
            else:
                components["stochastic"] = 0

    # 8. OBV
    if cfg.obv_enabled:
        o = row.get("obv")
        o_ma = row.get("obv_ma")
        if o is not None and o_ma is not None:
            if o > o_ma:
                components["obv"] = cfg.obv_weight
            elif o < o_ma:
                components["obv"] = -cfg.obv_weight
            else:
                components["obv"] = 0

    # 9. Volume confirmation (consecutive candle close trend + volume ratio)
    if cfg.volume_enabled:
        bullish = row.get("volume_trend_bullish")
        bearish = row.get("volume_trend_bearish")

        if bullish is not None or bearish is not None:
            if pd.notna(bullish) and bool(bullish):
                components["volume"] = cfg.volume_weight
            elif pd.notna(bearish) and bool(bearish):
                components["volume"] = -cfg.volume_weight
            else:
                components["volume"] = 0
        else:
            # Fallback when volume_trend columns are not in row (e.g. single-row mock tests)
            is_vol_valid = (pd.notna(row.get("volume_ratio")) and row.get("volume_ratio") >= cfg.volume_ratio_min)
            subtotal = sum(components.values())
            if subtotal > 0:
                direction = 1
            elif subtotal < 0:
                direction = -1
            else:
                c = row.get("close")
                o = row.get("open")
                if c is not None and o is not None and c > o:
                    direction = 1
                elif c is not None and o is not None and c < o:
                    direction = -1
                else:
                    direction = 0

            if is_vol_valid:
                components["volume"] = direction * cfg.volume_weight
            else:
                components["volume"] = 0

    # 10. Higher High (indicator 11: current candle high > previous swing high)
    if cfg.higher_high_enabled:
        hh = row.get("higher_high")
        if pd.notna(hh) and bool(hh):
            components["higher_high"] = cfg.higher_high_weight
        else:
            components["higher_high"] = 0

    # 11. Lower Low (indicator 12: current candle low < previous swing low)
    if cfg.lower_low_enabled:
        ll = row.get("lower_low")
        if pd.notna(ll) and bool(ll):
            components["lower_low"] = -cfg.lower_low_weight
        else:
            components["lower_low"] = 0

    # 12. HMA (Hull Moving Average 100)
    if cfg.hma_enabled:
        c = row.get("close")
        h = row.get("hma")
        if c is not None and h is not None and pd.notna(h):
            if c > h:
                components["hma"] = cfg.hma_weight
            elif c < h:
                components["hma"] = -cfg.hma_weight
            else:
                components["hma"] = 0

    # 13. Supertrend ((10,2) simple and (10,4) complex trend applied by default in AND mode)
    if cfg.supertrend_enabled:
        c = row.get("close")
        st_s = row.get("supertrend_simple")
        st_c = row.get("supertrend_complex")
        if c is not None and st_s is not None and st_c is not None and pd.notna(st_s) and pd.notna(st_c):
            # check whether "Signal bar close" is greater than current Supertrend (10,2)
            # and check whether "Signal bar close" is greater than current Supertrend (10,4)
            if c > st_s and c > st_c:
                components["supertrend"] = cfg.supertrend_weight
            elif c < st_s and c < st_c:
                components["supertrend"] = -cfg.supertrend_weight
            else:
                components["supertrend"] = 0

    return components


def generate_custom_signal(row: pd.Series, symbol: str, cfg: CustomStrategyConfig) -> Signal:
    """Generates a Signal using custom indicator rules and weights."""
    components = score_custom_row(row, cfg)
    score = sum(components.values())
    max_score = cfg.max_score

    if score >= cfg.long_threshold:
        decision = "LONG"
    elif score <= cfg.short_threshold:
        decision = "SHORT"
    else:
        decision = "HOLD"

    reasons = [f"{name}: {pts:+d}" for name, pts in components.items() if pts != 0]

    # Mandatory Volume Confirmation Trend Gate:
    # When Volume Indicator is enabled and volume trend columns are computed:
    # - LONG trades REQUIRE volume_trend_bullish == True (all consecutive candles close > prev close and volume >= min MA ratio)
    # - SHORT trades REQUIRE volume_trend_bearish == True (all consecutive candles close < prev close and volume >= min MA ratio)
    if cfg.volume_enabled and (row.get("volume_trend_bullish") is not None or row.get("volume_trend_bearish") is not None):
        is_bullish = bool(pd.notna(row.get("volume_trend_bullish")) and row.get("volume_trend_bullish"))
        is_bearish = bool(pd.notna(row.get("volume_trend_bearish")) and row.get("volume_trend_bearish"))
        if decision == "LONG" and not is_bullish:
            decision = "HOLD"
            reasons.append(f"Volume: consecutive {getattr(cfg, 'volume_consecutive', 3)}-period bullish trend not met")
        elif decision == "SHORT" and not is_bearish:
            decision = "HOLD"
            reasons.append(f"Volume: consecutive {getattr(cfg, 'volume_consecutive', 3)}-period bearish trend not met")

    # Mandatory Higher High Gate (indicator 11): Locks to LONG only and requires High > Previous Swing High
    if cfg.higher_high_enabled:
        is_hh = bool(pd.notna(row.get("higher_high")) and row.get("higher_high"))
        if decision == "LONG" and not is_hh:
            decision = "HOLD"
            reasons.append("Higher High: current candle high <= previous swing high")
        elif decision == "SHORT" and not cfg.lower_low_enabled:
            decision = "HOLD"  # Higher High active: Take only LONG trades

    # Mandatory Lower Low Gate (indicator 12): Locks to SHORT only and requires Low < Previous Swing Low
    if cfg.lower_low_enabled:
        is_ll = bool(pd.notna(row.get("lower_low")) and row.get("lower_low"))
        if decision == "SHORT" and not is_ll:
            decision = "HOLD"
            reasons.append("Lower Low: current candle low >= previous swing low")
        elif decision == "LONG" and not cfg.higher_high_enabled:
            decision = "HOLD"  # Lower Low active: Take only SHORT trades

    atr_val = row.get("atr14")
    if pd.isna(atr_val) or atr_val is None:
        atr_val = row.get("atr", 10.0)
    if pd.isna(atr_val) or atr_val is None:
        atr_val = 10.0

    indicators = {
        "close": row.get("close"),
        "atr": float(atr_val),
        "score": score,
    }

    confidence = round(abs(score) / max_score, 2) if max_score > 0 else 0.0

    return Signal(
        symbol=symbol,
        timestamp=row.get("timestamp"),
        signal=decision,
        score=score,
        max_score=max_score,
        confidence=confidence,
        strategy_version=cfg.version,
        indicators=indicators,
        components=components,
        reasons=reasons,
    )

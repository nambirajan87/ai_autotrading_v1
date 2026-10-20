import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STRATEGIES_FILE = PROJECT_ROOT / "data" / "saved_strategies.json"

DEFAULT_TEMPLATES = {
    "EMA_RSI_MACD_Balanced": {
        "name": "EMA_RSI_MACD_Balanced",
        "description": "Standard balanced trend & momentum consensus using EMA, RSI, MACD, and VWAP.",
        "created_at": "2026-10-01 09:00:00",
        "updated_at": "2026-10-01 09:00:00",
        "indicators": {
            "ema_enabled": True, "ema_fast": 20, "ema_slow": 50, "ema_weight": 2,
            "rsi_enabled": True, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
            "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 1,
            "macd_enabled": True, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
            "vwap_enabled": True, "vwap_weight": 1,
            "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
            "adx_enabled": False, "adx_period": 14, "adx_min": 20.0, "adx_weight": 1,
            "volume_enabled": True, "volume_period": 20, "volume_ratio_min": 1.2, "volume_consecutive": 3, "volume_weight": 1,
            "bollinger_enabled": False, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 1,
            "stoch_enabled": False, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 1,
            "obv_enabled": False, "obv_ma_period": 20, "obv_weight": 1,
            "higher_high_enabled": False, "higher_high_period": 5, "higher_high_weight": 1,
            "lower_low_enabled": False, "lower_low_period": 5, "lower_low_weight": 1,
            "hma_enabled": False, "hma_period": 100, "hma_weight": 1,
            "supertrend_enabled": False, "supertrend_period": 10, "supertrend_mult_simple": 2.0, "supertrend_mult_complex": 4.0, "supertrend_weight": 1,
            "long_threshold": 4, "short_threshold": -4,
            "fixed_target_pct": 3.0, "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": False, "trail_beyond_target": False,
            "trade_side": "BOTH", "short_lot_size": 1,
            "snooze_period_str": "5 min", "snooze_minutes": 5,
        },
        "risk": {
            "capital": 100000.0,
            "risk_per_trade_pct": 0.01,
            "max_daily_loss": 2000.0,
            "max_open_positions": 3,
            "max_exposure_pct": 1.0,
            "max_quantity": 5000,
            "max_consecutive_losses": 3,
            "lot_size": 1,
            "tick_size": 0.05,
            "trade_side": "BOTH",
            "short_lot_size": 1,
            "snooze_period_str": "5 min",
            "snooze_minutes": 5,
            "atr_stop_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "fixed_target_pct": 3.0,
            "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": False,
            "trail_beyond_target": False,
            "entry_start": "09:20",
            "entry_end": "15:00",
            "square_off_time": "15:15",
        },
        "cost": {
            "cost_mode": "FLAT_PER_ORDER",
            "flat_cost_per_order": 20.0,
            "slippage_mode": "FLAT_PER_ORDER",
            "flat_slippage_per_order": 20.0,
            "slippage_pct": 0.0005,
        },
        "instrument": {
            "market_segment": "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]",
            "symbol": "NIFTY",
            "isin": "INF204KB14I2",
            "equity_name": "NIFTY",
            "instrument_key": "NSE_FO|NIFTY",
            "underlying": "NIFTY",
            "expiry": "",
            "option_type": "PE",
            "strike": "",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "timeframe": 5,
            "allow_short": True,
            "lot_size": 65,
            "tick_size": 0.05,
        },
    },
    "ADX": {
        "name": "ADX",
        "description": "ADX Directional Trend Strength Consensus with EMA and Volume Confirmation.",
        "created_at": "2026-10-01 09:00:00",
        "updated_at": "2026-10-01 09:00:00",
        "indicators": {
            "ema_enabled": True, "ema_fast": 9, "ema_slow": 21, "ema_weight": 2,
            "rsi_enabled": True, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
            "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 1,
            "macd_enabled": False, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
            "vwap_enabled": True, "vwap_weight": 1,
            "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
            "adx_enabled": True, "adx_period": 14, "adx_min": 20.0, "adx_weight": 3,
            "volume_enabled": True, "volume_period": 20, "volume_ratio_min": 1.2, "volume_consecutive": 3, "volume_weight": 1,
            "bollinger_enabled": False, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 1,
            "stoch_enabled": False, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 1,
            "obv_enabled": False, "obv_ma_period": 20, "obv_weight": 1,
            "higher_high_enabled": False, "higher_high_period": 5, "higher_high_weight": 1,
            "lower_low_enabled": False, "lower_low_period": 5, "lower_low_weight": 1,
            "hma_enabled": False, "hma_period": 100, "hma_weight": 1,
            "supertrend_enabled": False, "supertrend_period": 10, "supertrend_mult_simple": 2.0, "supertrend_mult_complex": 4.0, "supertrend_weight": 1,
            "long_threshold": 4, "short_threshold": -4,
            "fixed_target_pct": 3.0, "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": False, "trail_beyond_target": False,
            "trade_side": "BOTH", "short_lot_size": 1,
            "snooze_period_str": "5 min", "snooze_minutes": 5,
        },
        "risk": {
            "capital": 100000.0,
            "risk_per_trade_pct": 0.01,
            "max_daily_loss": 2000.0,
            "max_open_positions": 3,
            "max_exposure_pct": 1.0,
            "max_quantity": 5000,
            "max_consecutive_losses": 3,
            "lot_size": 1,
            "tick_size": 0.05,
            "trade_side": "BOTH",
            "short_lot_size": 1,
            "snooze_period_str": "5 min",
            "snooze_minutes": 5,
            "atr_stop_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "fixed_target_pct": 3.0,
            "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": False,
            "trail_beyond_target": False,
            "entry_start": "09:20",
            "entry_end": "15:00",
            "square_off_time": "15:15",
        },
        "cost": {
            "cost_mode": "FLAT_PER_ORDER",
            "flat_cost_per_order": 20.0,
            "slippage_mode": "FLAT_PER_ORDER",
            "flat_slippage_per_order": 20.0,
            "slippage_pct": 0.0005,
        },
        "instrument": {
            "market_segment": "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]",
            "symbol": "NIFTY26OCT22500PE",
            "isin": "INF000513319",
            "equity_name": "NIFTY 27-OCT-2026 22500 PE",
            "instrument_key": "NSE_FO|51331",
            "underlying": "NIFTY",
            "expiry": "27-OCT-2026",
            "option_type": "PE",
            "strike": "22500",
            "start_date": "2026-10-14",
            "end_date": "2026-10-14",
            "timeframe": 5,
            "allow_short": True,
            "lot_size": 65,
            "tick_size": 0.05,
        },
    },
    "EMA_Crossover_Trend": {
        "name": "EMA_Crossover_Trend",
        "description": "Fast/Slow EMA trend following with ADX trend strength and Volume confirmation.",
        "created_at": "2026-10-01 09:00:00",
        "updated_at": "2026-10-01 09:00:00",
        "indicators": {
            "ema_enabled": True, "ema_fast": 9, "ema_slow": 21, "ema_weight": 3,
            "rsi_enabled": False, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
            "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 1,
            "macd_enabled": False, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
            "vwap_enabled": False, "vwap_weight": 1,
            "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
            "adx_enabled": True, "adx_period": 14, "adx_min": 22.0, "adx_weight": 2,
            "volume_enabled": True, "volume_period": 20, "volume_ratio_min": 1.3, "volume_consecutive": 3, "volume_weight": 2,
            "bollinger_enabled": False, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 1,
            "stoch_enabled": False, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 1,
            "obv_enabled": False, "obv_ma_period": 20, "obv_weight": 1,
            "higher_high_enabled": False, "higher_high_period": 5, "higher_high_weight": 1,
            "lower_low_enabled": False, "lower_low_period": 5, "lower_low_weight": 1,
            "hma_enabled": False, "hma_period": 100, "hma_weight": 1,
            "supertrend_enabled": False, "supertrend_period": 10, "supertrend_mult_simple": 2.0, "supertrend_mult_complex": 4.0, "supertrend_weight": 1,
            "long_threshold": 4, "short_threshold": -4,
            "fixed_target_pct": 3.0, "fixed_stoploss_pct": 1.5,
            "trailing_sl_enabled": True, "trail_beyond_target": True,
            "trade_side": "BOTH", "short_lot_size": 1,
            "snooze_period_str": "5 min", "snooze_minutes": 5,
        },
        "risk": {
            "capital": 100000.0,
            "risk_per_trade_pct": 0.01,
            "max_daily_loss": 2000.0,
            "max_open_positions": 3,
            "max_exposure_pct": 1.0,
            "max_quantity": 5000,
            "max_consecutive_losses": 3,
            "lot_size": 1,
            "tick_size": 0.05,
            "trade_side": "BOTH",
            "short_lot_size": 1,
            "snooze_period_str": "5 min",
            "snooze_minutes": 5,
            "atr_stop_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "fixed_target_pct": 3.0,
            "fixed_stoploss_pct": 1.5,
            "trailing_sl_enabled": True,
            "trail_beyond_target": True,
            "entry_start": "09:20",
            "entry_end": "15:00",
            "square_off_time": "15:15",
        },
        "cost": {
            "cost_mode": "FLAT_PER_ORDER",
            "flat_cost_per_order": 20.0,
            "slippage_mode": "FLAT_PER_ORDER",
            "flat_slippage_per_order": 20.0,
            "slippage_pct": 0.0005,
        },
        "instrument": {
            "market_segment": "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]",
            "symbol": "NIFTY",
            "isin": "INF204KB14I2",
            "equity_name": "NIFTY",
            "instrument_key": "NSE_FO|NIFTY",
            "underlying": "NIFTY",
            "expiry": "",
            "option_type": "PE",
            "strike": "",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "timeframe": 5,
            "allow_short": True,
            "lot_size": 65,
            "tick_size": 0.05,
        },
    },
    "Mean_Reversion_BB_RSI": {
        "name": "Mean_Reversion_BB_RSI",
        "description": "Counter-trend / mean-reversion setup using Bollinger Bands, RSI extremes, and Stochastic oscillator.",
        "created_at": "2026-10-01 09:00:00",
        "updated_at": "2026-10-01 09:00:00",
        "indicators": {
            "ema_enabled": False, "ema_fast": 20, "ema_slow": 50, "ema_weight": 2,
            "rsi_enabled": True, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
            "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 2,
            "macd_enabled": False, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
            "vwap_enabled": False, "vwap_weight": 1,
            "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
            "adx_enabled": False, "adx_period": 14, "adx_min": 20.0, "adx_weight": 1,
            "volume_enabled": False, "volume_period": 20, "volume_ratio_min": 1.2, "volume_consecutive": 3, "volume_weight": 1,
            "bollinger_enabled": True, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 2,
            "stoch_enabled": True, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 2,
            "obv_enabled": False, "obv_ma_period": 20, "obv_weight": 1,
            "higher_high_enabled": False, "higher_high_period": 5, "higher_high_weight": 1,
            "lower_low_enabled": False, "lower_low_period": 5, "lower_low_weight": 1,
            "hma_enabled": False, "hma_period": 100, "hma_weight": 1,
            "supertrend_enabled": False, "supertrend_period": 10, "supertrend_mult_simple": 2.0, "supertrend_mult_complex": 4.0, "supertrend_weight": 1,
            "long_threshold": 4, "short_threshold": -4,
            "fixed_target_pct": 2.0, "fixed_stoploss_pct": 1.5,
            "trailing_sl_enabled": False, "trail_beyond_target": False,
            "trade_side": "BOTH", "short_lot_size": 1,
            "snooze_period_str": "5 min", "snooze_minutes": 5,
        },
        "risk": {
            "capital": 100000.0,
            "risk_per_trade_pct": 0.01,
            "max_daily_loss": 2000.0,
            "max_open_positions": 3,
            "max_exposure_pct": 1.0,
            "max_quantity": 5000,
            "max_consecutive_losses": 3,
            "lot_size": 1,
            "tick_size": 0.05,
            "trade_side": "BOTH",
            "short_lot_size": 1,
            "snooze_period_str": "5 min",
            "snooze_minutes": 5,
            "atr_stop_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "fixed_target_pct": 2.0,
            "fixed_stoploss_pct": 1.5,
            "trailing_sl_enabled": False,
            "trail_beyond_target": False,
            "entry_start": "09:20",
            "entry_end": "15:00",
            "square_off_time": "15:15",
        },
        "cost": {
            "cost_mode": "FLAT_PER_ORDER",
            "flat_cost_per_order": 20.0,
            "slippage_mode": "FLAT_PER_ORDER",
            "flat_slippage_per_order": 20.0,
            "slippage_pct": 0.0005,
        },
        "instrument": {
            "market_segment": "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]",
            "symbol": "NIFTY",
            "isin": "INF204KB14I2",
            "equity_name": "NIFTY",
            "instrument_key": "NSE_FO|NIFTY",
            "underlying": "NIFTY",
            "expiry": "",
            "option_type": "PE",
            "strike": "",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "timeframe": 5,
            "allow_short": True,
            "lot_size": 65,
            "tick_size": 0.05,
        },
    },
    "Breakout_Momentum_VWAP": {
        "name": "Breakout_Momentum_VWAP",
        "description": "High-volume intraday breakout strategy anchored by VWAP, Volume surge, and OBV accumulators.",
        "created_at": "2026-10-01 09:00:00",
        "updated_at": "2026-10-01 09:00:00",
        "indicators": {
            "ema_enabled": False, "ema_fast": 20, "ema_slow": 50, "ema_weight": 2,
            "rsi_enabled": False, "rsi_period": 14, "rsi_bull_low": 55.0, "rsi_bull_high": 70.0,
            "rsi_bear_low": 30.0, "rsi_bear_high": 45.0, "rsi_weight": 1,
            "macd_enabled": False, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_weight": 2,
            "vwap_enabled": True, "vwap_weight": 2,
            "atr_enabled": True, "atr_period": 14, "atr_stop_multiplier": 1.5, "atr_reward_risk_ratio": 2.0,
            "adx_enabled": False, "adx_period": 14, "adx_min": 20.0, "adx_weight": 1,
            "volume_enabled": True, "volume_period": 20, "volume_ratio_min": 1.5, "volume_consecutive": 3, "volume_weight": 2,
            "bollinger_enabled": False, "bollinger_period": 20, "bollinger_std_dev": 2.0, "bollinger_weight": 1,
            "stoch_enabled": False, "stoch_k": 14, "stoch_d": 3, "stoch_low": 20.0, "stoch_high": 80.0, "stoch_weight": 1,
            "obv_enabled": True, "obv_ma_period": 20, "obv_weight": 2,
            "higher_high_enabled": True, "higher_high_period": 5, "higher_high_weight": 1,
            "lower_low_enabled": True, "lower_low_period": 5, "lower_low_weight": 1,
            "long_threshold": 4, "short_threshold": -4,
            "fixed_target_pct": 3.5, "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": True, "trail_beyond_target": False,
            "trade_side": "BOTH", "short_lot_size": 1,
            "snooze_period_str": "5 min", "snooze_minutes": 5,
        },
        "risk": {
            "capital": 100000.0,
            "risk_per_trade_pct": 0.01,
            "max_daily_loss": 2000.0,
            "max_open_positions": 3,
            "max_exposure_pct": 1.0,
            "max_quantity": 5000,
            "max_consecutive_losses": 3,
            "lot_size": 1,
            "tick_size": 0.05,
            "trade_side": "BOTH",
            "short_lot_size": 1,
            "snooze_period_str": "5 min",
            "snooze_minutes": 5,
            "atr_stop_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "fixed_target_pct": 3.5,
            "fixed_stoploss_pct": 2.0,
            "trailing_sl_enabled": True,
            "trail_beyond_target": False,
            "entry_start": "09:20",
            "entry_end": "15:00",
            "square_off_time": "15:15",
        },
        "cost": {
            "cost_mode": "FLAT_PER_ORDER",
            "flat_cost_per_order": 20.0,
            "slippage_mode": "FLAT_PER_ORDER",
            "flat_slippage_per_order": 20.0,
            "slippage_pct": 0.0005,
        },
        "instrument": {
            "market_segment": "🎯 NSE F&O (Options & Futures) [Kotak Live Priority]",
            "symbol": "NIFTY",
            "isin": "INF204KB14I2",
            "equity_name": "NIFTY",
            "instrument_key": "NSE_FO|NIFTY",
            "underlying": "NIFTY",
            "expiry": "",
            "option_type": "PE",
            "strike": "",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "timeframe": 5,
            "allow_short": True,
            "lot_size": 65,
            "tick_size": 0.05,
        },
    },
}


def auto_suggest_strategy_name(ind: dict) -> str:
    """
    Generates a meaningful, descriptive strategy name based on the enabled indicators.
    Examples:
      - EMA + RSI + MACD -> 'EMA_RSI_MACD_Consensus'
      - Bollinger + RSI + Stoch -> 'BB_RSI_STOCH_Strategy'
      - EMA + VWAP -> 'EMA_VWAP_Trend'
    """
    tokens = []
    if ind.get("ema_enabled"):
        fast = ind.get("ema_fast", 20)
        slow = ind.get("ema_slow", 50)
        tokens.append(f"EMA{fast}_{slow}")
    if ind.get("rsi_enabled"):
        tokens.append("RSI")
    if ind.get("macd_enabled"):
        tokens.append("MACD")
    if ind.get("vwap_enabled"):
        tokens.append("VWAP")
    if ind.get("bollinger_enabled"):
        tokens.append("BB")
    if ind.get("adx_enabled"):
        tokens.append("ADX")
    if ind.get("volume_enabled"):
        tokens.append("VOL")
    if ind.get("stoch_enabled"):
        tokens.append("STOCH")
    if ind.get("obv_enabled"):
        tokens.append("OBV")
    if ind.get("higher_high_enabled"):
        tokens.append("HH")
    if ind.get("lower_low_enabled"):
        tokens.append("LL")
    if ind.get("hma_enabled"):
        tokens.append("HMA")
    if ind.get("supertrend_enabled"):
        tokens.append("STREND")

    if not tokens:
        return "Custom_Strategy_v1"

    if len(tokens) <= 3:
        return "_".join(tokens) + "_Consensus"
    elif len(tokens) <= 4:
        return "_".join(tokens) + "_Strategy"
    else:
        # For many indicators, summarize cleanly
        top_tokens = "_".join(tokens[:3])
        return f"{top_tokens}_Plus_{len(tokens)-3}Ind"


def load_all_strategies() -> Dict[str, dict]:
    """Loads all saved strategies from data/saved_strategies.json, seeding defaults if needed."""
    STRATEGIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STRATEGIES_FILE.exists():
        save_all_strategies(DEFAULT_TEMPLATES)
        return DEFAULT_TEMPLATES.copy()

    try:
        with open(STRATEGIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or not data:
                save_all_strategies(DEFAULT_TEMPLATES)
                return DEFAULT_TEMPLATES.copy()
            return data
    except Exception as e:
        logger.error(f"Error loading saved strategies from {STRATEGIES_FILE}: {e}")
        return DEFAULT_TEMPLATES.copy()


def save_all_strategies(strategies: Dict[str, dict]) -> bool:
    """Serializes the dictionary of strategies to data/saved_strategies.json."""
    try:
        STRATEGIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STRATEGIES_FILE, "w", encoding="utf-8") as f:
            json.dump(strategies, f, indent=2, default=str)
        return True
    except Exception as e:
        logger.error(f"Error saving strategies to {STRATEGIES_FILE}: {e}")
        return False


def get_strategy(name: str) -> Optional[dict]:
    """Retrieves a single strategy by name."""
    all_strats = load_all_strategies()
    return all_strats.get(name)


def save_strategy(
    name: str,
    indicators: dict,
    risk: Optional[dict] = None,
    cost: Optional[dict] = None,
    description: str = "",
    instrument: Optional[dict] = None,
) -> bool:
    """
    Saves or updates a strategy with the given name and configurations.
    """
    clean_name = name.strip()
    if not clean_name:
        clean_name = auto_suggest_strategy_name(indicators)

    all_strats = load_all_strategies()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing = all_strats.get(clean_name, {})
    created_at = existing.get("created_at", now_str)

    # Sanitize and serialize risk
    safe_risk = {}
    if risk:
        for k, v in risk.items():
            if hasattr(v, "strftime"):
                safe_risk[k] = v.strftime("%H:%M")
            else:
                safe_risk[k] = v

    record = {
        "name": clean_name,
        "description": description or existing.get("description", f"User strategy: {clean_name}"),
        "created_at": created_at,
        "updated_at": now_str,
        "indicators": indicators,
        "risk": safe_risk if risk else existing.get("risk", {}),
        "cost": cost if cost else existing.get("cost", {}),
        "instrument": instrument if instrument is not None else existing.get("instrument", {}),
    }

    all_strats[clean_name] = record
    return save_all_strategies(all_strats)


def delete_strategy(name: str) -> bool:
    """Deletes a strategy by name. Ensures at least 1 strategy remains."""
    all_strats = load_all_strategies()
    if name in all_strats:
        if len(all_strats) <= 1:
            logger.warning("Cannot delete the only remaining strategy.")
            return False
        del all_strats[name]
        return save_all_strategies(all_strats)
    return False


def list_strategy_names() -> List[str]:
    """Returns sorted list of available strategy names."""
    all_strats = load_all_strategies()
    return sorted(list(all_strats.keys()))

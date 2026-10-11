import pandas as pd

from app.config.strategy_config import StrategyConfig

# Each rule looks at ONE candle (a pandas Series) and returns points.
# Positive = bullish, negative = bearish, 0 = no opinion.


def ema_rule(row: pd.Series) -> int:
    if row["ema20"] > row["ema50"]:
        return 2
    if row["ema20"] < row["ema50"]:
        return -2
    return 0


def vwap_rule(row: pd.Series) -> int:
    if row["close"] > row["vwap"]:
        return 1
    if row["close"] < row["vwap"]:
        return -1
    return 0


def rsi_rule(row: pd.Series, cfg: StrategyConfig) -> int:
    rsi = row["rsi14"]
    if cfg.rsi_bull_low <= rsi <= cfg.rsi_bull_high:
        return 1
    if cfg.rsi_bear_low <= rsi <= cfg.rsi_bear_high:
        return -1
    return 0  # neutral zone or stretched (>70 / <30)


def macd_rule(row: pd.Series) -> int:
    if row["macd_hist"] > 0:
        return 2
    if row["macd_hist"] < 0:
        return -2
    return 0


def adx_rule(row: pd.Series, cfg: StrategyConfig) -> int:
    if row["adx"] < cfg.adx_min:
        return 0  # weak trend: no opinion
    if row["plus_di"] > row["minus_di"]:
        return 1
    if row["minus_di"] > row["plus_di"]:
        return -1
    return 0


def volume_rule(row: pd.Series, cfg: StrategyConfig, direction: int) -> int:
    """Volume has no direction of its own. It confirms the side the other
    rules lean toward (direction is +1, -1 or 0)."""
    if row["volume_ratio"] >= cfg.volume_ratio_min:
        return direction
    return 0
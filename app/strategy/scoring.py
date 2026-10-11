import pandas as pd

from app.config.strategy_config import StrategyConfig
from app.strategy.strategy_rules import (
    adx_rule, ema_rule, macd_rule, rsi_rule, volume_rule, vwap_rule,
)


def score_row(row: pd.Series, cfg: StrategyConfig) -> dict:
    """Returns the per-rule points, e.g. {"ema_trend": 2, "vwap": 1, ...}."""
    parts = {
        "ema_trend": ema_rule(row),
        "vwap": vwap_rule(row),
        "rsi": rsi_rule(row, cfg),
        "macd": macd_rule(row),
        "adx": adx_rule(row, cfg),
    }

    subtotal = sum(parts.values())
    if subtotal > 0:
        direction = 1
    elif subtotal < 0:
        direction = -1
    else:
        c, o = row.get("close"), row.get("open")
        if c is not None and o is not None and c > o:
            direction = 1
        elif c is not None and o is not None and c < o:
            direction = -1
        else:
            direction = 0

    parts["volume"] = volume_rule(row, cfg, direction)
    return parts
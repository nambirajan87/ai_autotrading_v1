import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from app.config.strategy_config import StrategyConfig
from app.strategy.scoring import score_row

logger = logging.getLogger(__name__)

# output name -> DataFrame column name
INDICATOR_COLUMNS = {
    "close": "close", "ema20": "ema20", "ema50": "ema50", "rsi": "rsi14",
    "macd": "macd", "macd_signal": "macd_signal", "macd_hist": "macd_hist",
    "vwap": "vwap", "atr": "atr14", "adx": "adx",
    "plus_di": "plus_di", "minus_di": "minus_di", "volume_ratio": "volume_ratio",
}


@dataclass
class Signal:
    symbol: str
    timestamp: object
    signal: str                   # "LONG" | "SHORT" | "HOLD"
    score: int
    max_score: int
    confidence: float             # abs(score)/max_score. Score strength, NOT a win probability
    strategy_version: str
    indicators: dict
    components: dict = field(default_factory=dict)   # points per rule
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = str(self.timestamp)
        return d


def _clean(value) -> Optional[float]:
    return None if pd.isna(value) else round(float(value), 4)


def generate_signal(row: pd.Series, symbol: str, cfg: StrategyConfig) -> Signal:
    if getattr(cfg, "is_custom", False):
        from app.strategy.custom_strategy import generate_custom_signal
        return generate_custom_signal(row, symbol, cfg)

    indicators = {name: _clean(row.get(col)) for name, col in INDICATOR_COLUMNS.items()}
    missing = [name for name, value in indicators.items() if value is None]

    if missing:
        return Signal(symbol, row.get("timestamp"), "HOLD", 0, cfg.max_score, 0.0,
                      cfg.version, indicators,
                      reasons=[f"insufficient data: {missing}"])

    components = score_row(row, cfg)
    score = sum(components.values())

    if score >= cfg.long_threshold:
        decision = "LONG"
    elif score <= cfg.short_threshold:
        decision = "SHORT"
    else:
        decision = "HOLD"

    reasons = [f"{name}: {pts:+d}" for name, pts in components.items() if pts != 0]
    return Signal(symbol, row.get("timestamp"), decision, score, cfg.max_score,
                  round(abs(score) / cfg.max_score, 2), cfg.version,
                  indicators, components, reasons)


def generate_signals(df: pd.DataFrame, symbol: str, cfg: StrategyConfig) -> pd.DataFrame:
    """Signal for every candle. Each row is judged using only that row's
    indicator values, which (Phase 3 test) depend only on past candles."""
    records = []
    for _, row in df.iterrows():
        s = generate_signal(row, symbol, cfg)
        records.append({"timestamp": s.timestamp, "signal": s.signal, "score": s.score})
    return pd.DataFrame(records, index=df.index)


if __name__ == "__main__":
    from app.data.regime_simulator import generate_regime_sample
    from app.indicators.indicator_engine import calculate_all
    from app.monitoring.logging_setup import setup_logging

    setup_logging("INFO")
    cfg = StrategyConfig()
    data = calculate_all(generate_regime_sample())
    data["signal"] = generate_signals(data, "RELIANCE", cfg)["signal"].values

    print(pd.crosstab(data["regime"], data["signal"]))
    last = generate_signal(data.iloc[-1], "RELIANCE", cfg)
    print(json.dumps(last.to_dict(), indent=2))
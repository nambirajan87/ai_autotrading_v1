from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "strategy_v1"
    max_score: int = 8

    long_threshold: int = 5       # score >= this -> LONG
    short_threshold: int = -5     # score <= this -> SHORT

    rsi_bull_low: float = 55.0
    rsi_bull_high: float = 70.0
    rsi_bear_high: float = 45.0
    rsi_bear_low: float = 30.0
    adx_min: float = 20.0
    volume_ratio_min: float = 1.2

    def __post_init__(self):  # runs right after the generated constructor
        if not (0 < self.long_threshold <= self.max_score):
            raise ValueError("long_threshold must be between 1 and max_score")
        if not (-self.max_score <= self.short_threshold < 0):
            raise ValueError("short_threshold must be between -max_score and -1")
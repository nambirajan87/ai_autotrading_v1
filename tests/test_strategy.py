import json

import pandas as pd
import pytest

from app.config.strategy_config import StrategyConfig
from app.data.market_data import normalize_ohlcv, validate_ohlcv
from app.data.regime_simulator import generate_regime_sample
from app.data.sample_data import generate_sample
from app.indicators.indicator_engine import calculate_all
from app.strategy.signal_engine import generate_signal, generate_signals
from app.strategy.strategy_rules import rsi_rule

CFG = StrategyConfig()


def bullish_row() -> pd.Series:
    return pd.Series({
        "timestamp": pd.Timestamp("2026-08-03 10:00"), "close": 2510.0,
        "ema20": 2505.0, "ema50": 2495.0, "rsi14": 60.0,
        "macd": 3.0, "macd_signal": 2.0, "macd_hist": 1.0,
        "vwap": 2500.0, "atr14": 5.0, "plus_di": 30.0, "minus_di": 15.0,
        "adx": 25.0, "volume_ratio": 1.5,
    })


def bearish_row() -> pd.Series:
    row = bullish_row()
    row["close"], row["vwap"] = 2500.0, 2510.0
    row["ema20"], row["ema50"] = 2495.0, 2505.0
    row["rsi14"] = 40.0
    row["macd"], row["macd_signal"], row["macd_hist"] = -3.0, -2.0, -1.0
    row["plus_di"], row["minus_di"] = 15.0, 30.0
    return row


def test_strong_bullish_is_long():
    s = generate_signal(bullish_row(), "RELIANCE", CFG)
    assert s.signal == "LONG"
    assert s.score == 8


def test_strong_bearish_is_short():
    s = generate_signal(bearish_row(), "RELIANCE", CFG)
    assert s.signal == "SHORT"
    assert s.score == -8


def test_mixed_indicators_hold():
    row = bullish_row()
    row["macd_hist"] = -1.0   # MACD disagrees
    row["rsi14"] = 50.0       # neutral
    row["adx"] = 15.0         # weak trend
    s = generate_signal(row, "RELIANCE", CFG)
    assert s.signal == "HOLD"
    assert s.score == 2       # ema +2, vwap +1, macd -2, volume +1


def test_nan_indicator_gives_hold():
    row = bullish_row()
    row["ema50"] = float("nan")
    s = generate_signal(row, "RELIANCE", CFG)
    assert s.signal == "HOLD"
    assert "insufficient data" in s.reasons[0]


def test_missing_column_gives_hold():
    row = bullish_row().drop("adx")
    assert generate_signal(row, "RELIANCE", CFG).signal == "HOLD"


def test_overbought_rsi_gives_no_points():
    row = bullish_row()
    row["rsi14"] = 75.0
    assert rsi_rule(row, CFG) == 0
    row["rsi14"] = 25.0
    assert rsi_rule(row, CFG) == 0


def test_threshold_is_configurable():
    row = bullish_row()
    row["volume_ratio"] = 1.0                  # score becomes 7
    assert generate_signal(row, "X", CFG).signal == "LONG"
    strict = StrategyConfig(long_threshold=8)
    assert generate_signal(row, "X", strict).signal == "HOLD"


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        StrategyConfig(long_threshold=-1)
    with pytest.raises(ValueError):
        StrategyConfig(short_threshold=3)


def test_warmup_rows_are_hold():
    df = calculate_all(generate_sample(days=3))
    sigs = generate_signals(df, "RELIANCE", CFG)
    assert len(sigs) == len(df)
    assert (sigs["signal"].iloc[:49] == "HOLD").all()


def test_signal_is_json_serializable_and_versioned():
    d = generate_signal(bullish_row(), "RELIANCE", CFG).to_dict()
    json.dumps(d)  # must not raise
    assert d["strategy_version"] == "strategy_v1"
    assert d["signal"] == "LONG"


def test_regime_sample_is_valid_and_row_count():
    raw = generate_regime_sample()
    assert len(raw) == 20 * 75
    validate_ohlcv(normalize_ohlcv(raw.drop(columns="regime")))
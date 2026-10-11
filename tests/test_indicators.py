import numpy as np
import pandas as pd

from app.data.sample_data import generate_sample
from app.indicators.adx import adx
from app.indicators.atr import atr
from app.indicators.ema import ema
from app.indicators.indicator_engine import calculate_all
from app.indicators.macd import macd
from app.indicators.rsi import rsi
from app.indicators.volume import volume_features
from app.indicators.vwap import vwap


def trending_df(n=60):
    close = pd.Series(100.0 + np.arange(n))
    return pd.DataFrame({"high": close + 0.5, "low": close - 0.5, "close": close})


def test_ema_hand_calculated():
    # span=3 -> alpha=0.5: 1, 1.5, 2.25, 3.125, 4.0625
    result = ema(pd.Series([1, 2, 3, 4, 5], dtype=float), 3)
    assert result.iloc[:2].isna().all()          # warm-up
    assert result.iloc[2] == 2.25
    assert result.iloc[4] == 4.0625


def test_ema_constant_series():
    result = ema(pd.Series([50.0] * 30), 10)
    assert np.allclose(result.dropna(), 50.0)


def test_rsi_all_up_is_100():
    assert rsi(pd.Series(np.arange(1.0, 40.0)), 14).iloc[-1] == 100.0


def test_rsi_all_down_is_0():
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1.0)), 14).iloc[-1] == 0.0


def test_rsi_stays_between_0_and_100():
    values = rsi(generate_sample(days=5)["close"]).dropna()
    assert values.between(0, 100).all()


def test_macd_rising_prices_and_histogram_identity():
    result = macd(pd.Series(100 + 0.5 * np.arange(100)))
    assert result["macd"].iloc[-1] > 0           # fast EMA above slow EMA
    diff = result["macd"] - result["macd_signal"]
    assert np.allclose(result["macd_hist"].dropna(), diff.dropna())


def test_vwap_hand_calculated_and_daily_reset():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-08-03 09:15", "2026-08-03 09:20", "2026-08-04 09:15"]),
        "high":   [12.0, 21.0, 31.0],
        "low":    [8.0, 19.0, 29.0],
        "close":  [10.0, 20.0, 30.0],
        "volume": [100, 300, 50],
    })
    result = vwap(df)
    assert result.iloc[0] == 10.0
    assert result.iloc[1] == 17.5                # (10*100 + 20*300) / 400
    assert result.iloc[2] == 30.0                # new day -> reset


def test_atr_constant_range():
    df = pd.DataFrame({"high": [101.0] * 30, "low": [99.0] * 30, "close": [100.0] * 30})
    assert np.isclose(atr(df, 14).iloc[-1], 2.0)


def test_adx_strong_trend_is_high():
    result = adx(trending_df(), 14)
    assert result["adx"].iloc[-1] > 90
    assert result["plus_di"].iloc[-1] > result["minus_di"].iloc[-1]


def test_volume_ratio_constant_is_one():
    df = pd.DataFrame({"volume": [1000] * 30})
    assert np.allclose(volume_features(df, 20)["volume_ratio"].dropna(), 1.0)


def test_no_look_ahead_bias():
    """Indicators for the first 300 candles must be identical whether or not
    the later candles exist. If not, an indicator is 'seeing the future'."""
    df = generate_sample(days=5)
    full = calculate_all(df)
    partial = calculate_all(df.iloc[:300])
    pd.testing.assert_frame_equal(full.iloc[:300], partial)


def test_engine_adds_all_columns():
    result = calculate_all(generate_sample(days=3))
    for col in ["ema20", "ema50", "rsi14", "macd", "macd_signal", "macd_hist",
                "vwap", "atr14", "plus_di", "minus_di", "adx",
                "volume_ma", "volume_ratio", "regime"]:
        assert col in result.columns


def test_regime_detection():
    from app.indicators.regime import detect_regime, detect_regime_row

    df = pd.DataFrame({
        "adx": [15.0, 25.0, 25.0, np.nan],
        "ema20": [105.0, 105.0, 95.0, 105.0],
        "ema50": [100.0, 100.0, 100.0, 100.0],
    })
    assert detect_regime(df).tolist() == ["SIDEWAYS", "UP", "DOWN", "SIDEWAYS"]
    assert detect_regime_row(pd.Series({"adx": 22.0, "ema20": 105.0, "ema50": 100.0})) == "UP"
    assert detect_regime_row(pd.Series({"adx": 22.0, "ema20": 95.0, "ema50": 100.0})) == "DOWN"
    assert detect_regime_row(pd.Series({"adx": 18.0, "ema20": 105.0, "ema50": 100.0})) == "SIDEWAYS"
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


def test_hma_indicator():
    from app.indicators.hma import hma

    # Constant series should yield close to constant
    constant_s = pd.Series([100.0] * 120)
    res_const = hma(constant_s, period=100)
    assert np.allclose(res_const.iloc[-10:], 100.0)

    # Trending series: in an uptrend, HMA should follow the trend and be close to current prices
    trend_s = pd.Series(np.linspace(100.0, 200.0, 150))
    res_trend = hma(trend_s, period=50)
    assert not res_trend.isna().any()
    assert res_trend.iloc[-1] > res_trend.iloc[0]


def test_supertrend_indicator():
    from app.indicators.supertrend import supertrend

    # Strong uptrend: Supertrend should be bullish (trend == 1, close > supertrend)
    n = 50
    up_prices = np.linspace(100.0, 200.0, n)
    df_up = pd.DataFrame({
        "open": up_prices - 0.5,
        "high": up_prices + 1.0,
        "low": up_prices - 1.0,
        "close": up_prices,
    })
    st_res_up = supertrend(df_up, period=10, multiplier=2.0)
    assert (st_res_up["trend"].iloc[-10:] == 1).all()
    assert (df_up["close"].iloc[-10:] > st_res_up["supertrend"].iloc[-10:]).all()

    # Strong downtrend: Supertrend should be bearish (trend == -1, close < supertrend)
    down_prices = np.linspace(200.0, 100.0, n)
    df_down = pd.DataFrame({
        "open": down_prices + 0.5,
        "high": down_prices + 1.0,
        "low": down_prices - 1.0,
        "close": down_prices,
    })
    st_res_down = supertrend(df_down, period=10, multiplier=2.0)
    assert (st_res_down["trend"].iloc[-10:] == -1).all()
    assert (df_down["close"].iloc[-10:] < st_res_down["supertrend"].iloc[-10:]).all()


def test_custom_strategy_hma_and_supertrend_scoring():
    from app.strategy.custom_strategy import (
        CustomStrategyConfig,
        calculate_custom_indicators,
        score_custom_row,
    )

    n = 60
    base = np.linspace(100.0, 160.0, n)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-10-19 09:15", periods=n, freq="1min"),
        "open": base - 0.2,
        "high": base + 0.8,
        "low": base - 0.8,
        "close": base,
        "volume": [1000] * n,
    })

    # Enable HMA and Supertrend
    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False, vwap_enabled=False,
        atr_enabled=True, adx_enabled=False, volume_enabled=False, bollinger_enabled=False,
        stoch_enabled=False, obv_enabled=False, higher_high_enabled=False, lower_low_enabled=False,
        hma_enabled=True, hma_period=20, hma_weight=2,
        supertrend_enabled=True, supertrend_period=10, supertrend_mult_simple=2.0, supertrend_mult_complex=4.0, supertrend_weight=3,
        long_threshold=4, short_threshold=-4,
    )

    scored_df = calculate_custom_indicators(df, cfg)
    assert "hma" in scored_df.columns
    assert "supertrend_simple" in scored_df.columns
    assert "supertrend_complex" in scored_df.columns

    last_row = scored_df.iloc[-1]
    scores = score_custom_row(last_row, cfg)

    # In strong uptrend:
    # 1. close > hma -> +hma_weight (2)
    # 2. close > supertrend(10,2) AND close > supertrend(10,4) -> +supertrend_weight (3)
    assert scores.get("hma") == 2
    assert scores.get("supertrend") == 3
    assert sum(scores.values()) == 5

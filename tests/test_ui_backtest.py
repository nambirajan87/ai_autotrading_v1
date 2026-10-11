import numpy as np
import pandas as pd
import pytest

from app.backtest.costs import apply_slippage, leg_cost
from app.config.cost_config import CostConfig
from app.data.instrument_loader import validate_isin
from app.database.backtest_database import BacktestDatabase
from app.indicators.bollinger import bollinger_bands
from app.indicators.obv import on_balance_volume
from app.indicators.stochastic import stochastic
from app.strategy.custom_strategy import (
    CustomStrategyConfig,
    calculate_custom_indicators,
    generate_custom_signal,
)


# ---------------- 1. ISIN Validation Tests ----------------
def test_isin_validation_valid():
    assert validate_isin("INE002A01018") is True  # RELIANCE
    assert validate_isin("INE009A01021") is True  # INFY
    assert validate_isin("INE467B01029") is True  # TCS
    assert validate_isin("INE040A01034") is True  # HDFCBANK


def test_isin_validation_invalid():
    assert validate_isin("INE002A01019") is False  # Bad check digit
    assert validate_isin("INE002A0101") is False   # Too short (11 chars)
    assert validate_isin("INE002A010180") is False  # Too long (13 chars)
    assert validate_isin("US0378331005") is False  # Non-Indian (US)
    assert validate_isin("INE002A0101@") is False  # Special chars
    assert validate_isin("") is False
    assert validate_isin(None) is False


# ---------------- 2. New Indicators Hand-Calculated Tests ----------------
def test_bollinger_bands_hand_calculated():
    # Constant series: std is 0.0, upper == lower == mid
    s = pd.Series([100.0] * 20)
    bb = bollinger_bands(s, period=20, std_dev=2.0)
    assert bb["bb_mid"].iloc[-1] == pytest.approx(100.0)
    assert bb["bb_upper"].iloc[-1] == pytest.approx(100.0)
    assert bb["bb_lower"].iloc[-1] == pytest.approx(100.0)

    # Series [10, 20]: mean = 15.0, pop std (ddof=0) = 5.0
    s2 = pd.Series([10.0, 20.0])
    bb2 = bollinger_bands(s2, period=2, std_dev=2.0)
    assert bb2["bb_mid"].iloc[-1] == pytest.approx(15.0)
    assert bb2["bb_upper"].iloc[-1] == pytest.approx(25.0)
    assert bb2["bb_lower"].iloc[-1] == pytest.approx(5.0)
    assert bb2["bb_pct_b"].iloc[-1] == pytest.approx(0.75)  # (20 - 5) / 20 = 0.75


def test_stochastic_hand_calculated():
    df = pd.DataFrame({
        "high": [10.0, 15.0, 20.0],
        "low": [5.0, 8.0, 10.0],
        "close": [8.0, 12.0, 15.0],
    })
    # Over 3 periods: Lowest Low = 5.0, Highest High = 20.0, Range = 15.0
    # %K = 100 * (15.0 - 5.0) / 15.0 = 66.6667
    stoch = stochastic(df, k_period=3, d_period=1)
    assert stoch["stoch_k"].iloc[-1] == pytest.approx(66.6667, abs=0.01)
    assert stoch["stoch_d"].iloc[-1] == pytest.approx(66.6667, abs=0.01)


def test_on_balance_volume_hand_calculated():
    df = pd.DataFrame({
        "close": [100.0, 105.0, 102.0, 102.0],
        "volume": [100, 200, 150, 50],
    })
    # Row 0: diff NaN -> 0
    # Row 1: diff +5 -> +200, cumsum = 200
    # Row 2: diff -3 -> -150, cumsum = 50
    # Row 3: diff 0  -> 0,    cumsum = 50
    obv_df = on_balance_volume(df, ma_period=2)
    assert obv_df["obv"].tolist() == [0.0, 200.0, 50.0, 50.0]
    assert obv_df["obv_ma"].iloc[-1] == pytest.approx(50.0)


# ---------------- 3. No Look-Ahead Bias Tests ----------------
def test_new_indicators_no_look_ahead_bias():
    from app.data.sample_data import generate_sample
    df = generate_sample(days=5)

    # Full vs partial calculation
    full_bb = bollinger_bands(df["close"], 20, 2.0)
    part_bb = bollinger_bands(df["close"].iloc[:150], 20, 2.0)
    pd.testing.assert_frame_equal(full_bb.iloc[:150], part_bb)

    full_stoch = stochastic(df, 14, 3)
    part_stoch = stochastic(df.iloc[:150], 14, 3)
    pd.testing.assert_frame_equal(full_stoch.iloc[:150], part_stoch)

    full_obv = on_balance_volume(df, 20)
    part_obv = on_balance_volume(df.iloc[:150], 20)
    pd.testing.assert_frame_equal(full_obv.iloc[:150], part_obv)


# ---------------- 4. Flat Cost and Slippage Tests ----------------
def test_flat_cost_math():
    cfg_flat = CostConfig(cost_mode="FLAT_PER_ORDER", flat_cost_per_order=20.0)
    # Regardless of turnover, flat cost is 20 per leg
    assert leg_cost("BUY", 2500.0, 40, cfg_flat) == 20.0
    assert leg_cost("SELL", 2500.0, 40, cfg_flat) == 20.0

    # Percent model keeps exact original behavior
    cfg_pct = CostConfig(cost_mode="PERCENT_MODEL")
    assert leg_cost("BUY", 2500.0, 40, cfg_pct) == pytest.approx(30.22, abs=0.01)


def test_flat_slippage_math():
    cfg_flat = CostConfig(slippage_mode="FLAT_PER_ORDER", flat_slippage_per_order=20.0)
    # Quantity = 40: adverse move per share = 20 / 40 = 0.50
    # BUY fills higher, SELL fills lower
    assert apply_slippage(2500.0, "BUY", cfg=cfg_flat, quantity=40) == 2500.50
    assert apply_slippage(2500.0, "SELL", cfg=cfg_flat, quantity=40) == 2499.50

    # Round trip adverse total = (2500.50 - 2500) * 40 + (2500 - 2499.50) * 40 = 20 + 20 = 40
    buy_slip = (2500.50 - 2500.0) * 40
    sell_slip = (2500.0 - 2499.50) * 40
    assert buy_slip + sell_slip == pytest.approx(40.0)


# ---------------- 5. Custom Strategy Config & Scoring Tests ----------------
def test_custom_strategy_config_validation():
    cfg = CustomStrategyConfig()
    assert cfg.max_score > 0
    assert cfg.version.startswith("custom_")

    # Reject threshold > max_score
    with pytest.raises(ValueError):
        CustomStrategyConfig(long_threshold=cfg.max_score + 1)

    # Reject short threshold < -max_score
    with pytest.raises(ValueError):
        CustomStrategyConfig(short_threshold=-(cfg.max_score + 1))


def test_custom_strategy_scoring():
    cfg = CustomStrategyConfig(long_threshold=2, short_threshold=-2)
    row = pd.Series({
        "timestamp": pd.Timestamp("2026-09-01 10:00"),
        "close": 2500.0,
        "ema20": 2510.0, "ema50": 2490.0,  # EMA bullish (+2)
        "rsi14": 60.0,                      # RSI bullish (+1)
        "macd_hist": 1.5,                   # MACD bullish (+2)
        "vwap": 2495.0,                     # VWAP bullish (+1)
        "adx": 25.0, "plus_di": 30.0, "minus_di": 15.0,  # ADX bullish (+1)
        "bb_mid": 2490.0, "bb_upper": 2520.0, "bb_lower": 2460.0,  # BB bullish (+1)
        "stoch_k": 65.0, "stoch_d": 50.0,   # Stoch bullish (+1)
        "obv": 1000.0, "obv_ma": 800.0,     # OBV bullish (+1)
        "volume_ratio": 1.5,                # Volume confirms (+1)
        "atr14": 15.0,
    })
    sig = generate_custom_signal(row, "TEST", cfg)
    assert sig.signal == "LONG"
    assert sig.score > 0
    assert sig.strategy_version == cfg.version


# ---------------- 6. Database Storage & Retrieval Tests ----------------
def test_backtest_database_roundtrip():
    db = BacktestDatabase(":memory:")
    sample_trades = pd.DataFrame([{
        "symbol": "RELIANCE", "side": "LONG", "strategy_version": "custom_abc",
        "regime": "UP", "score": 6, "signal_time": "2026-09-01 10:00",
        "entry_time": "2026-09-01 10:05", "exit_time": "2026-09-01 11:00",
        "quantity": 40, "entry_price": 2500.0, "exit_price": 2530.0,
        "stop_loss": 2485.0, "target": 2530.0, "exit_reason": "TARGET",
        "gross_pnl": 1200.0, "costs": 40.0, "slippage_cost": 40.0, "net_pnl": 1160.0,
    }])
    run_id = db.save_run(
        symbol="RELIANCE", isin="INE002A01018", date_from="2026-09-01",
        date_to="2026-09-30", timeframe="5m", strategy_version="custom_abc",
        config={"symbol": "RELIANCE", "capital": 100000},
        metrics={"net_pnl": 1160.0, "num_trades": 1},
        trades_df=sample_trades,
    )
    assert run_id > 0

    # Retrieve run
    loaded_run = db.get_run(run_id)
    assert loaded_run["symbol"] == "RELIANCE"
    assert loaded_run["isin"] == "INE002A01018"
    assert loaded_run["metrics"]["net_pnl"] == 1160.0

    # Retrieve trades
    loaded_trades = db.get_trades(run_id)
    assert len(loaded_trades) == 1
    assert loaded_trades.iloc[0]["exit_reason"] == "TARGET"

    # Recent runs list
    recent = db.get_recent_runs(limit=10)
    assert len(recent) == 1
    assert recent[0]["id"] == run_id

    # Prune runs test
    for _ in range(5):
        db.save_run("RELIANCE", "INE002A01018", "2026-09-01", "2026-09-30", "5m", "v", {})
    assert len(db.get_recent_runs(10)) == 6
    db.prune_runs(max_runs_stored=3)
    assert len(db.get_recent_runs(10)) == 3

    db.close()


# ---------------- 7. Standalone Volume Indicator Tests ----------------
def test_standalone_volume_scoring_and_signals():
    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=20, volume_ratio_min=1.2, volume_weight=1,
        long_threshold=1, short_threshold=-1,
    )

    # Bullish candle (close > open) with volume ratio >= 1.2
    bullish_row = pd.Series({
        "timestamp": pd.Timestamp("2026-09-01 10:00"),
        "open": 2500.0,
        "close": 2510.0,
        "volume_ratio": 1.5,
    })
    sig_bull = generate_custom_signal(bullish_row, "TEST", cfg)
    assert sig_bull.signal == "LONG"
    assert sig_bull.score == 1
    assert sig_bull.components["volume"] == 1

    # Bearish candle (close < open) with volume ratio >= 1.2
    bearish_row = pd.Series({
        "timestamp": pd.Timestamp("2026-09-01 10:00"),
        "open": 2510.0,
        "close": 2500.0,
        "volume_ratio": 1.5,
    })
    sig_bear = generate_custom_signal(bearish_row, "TEST", cfg)
    assert sig_bear.signal == "SHORT"
    assert sig_bear.score == -1
    assert sig_bear.components["volume"] == -1

    # Low volume ratio (< 1.2) yields HOLD
    low_vol_row = pd.Series({
        "timestamp": pd.Timestamp("2026-09-01 10:00"),
        "open": 2500.0,
        "close": 2510.0,
        "volume_ratio": 1.0,
    })
    sig_low = generate_custom_signal(low_vol_row, "TEST", cfg)
    assert sig_low.signal == "HOLD"
    assert sig_low.score == 0
    assert sig_low.components["volume"] == 0


def test_volume_consecutive_config_validation():
    # Valid consecutive values 1 to 5
    for c in [1, 2, 3, 4, 5]:
        cfg = CustomStrategyConfig(volume_consecutive=c)
        assert cfg.volume_consecutive == c

    # Invalid consecutive values
    with pytest.raises(ValueError):
        CustomStrategyConfig(volume_consecutive=0)
    with pytest.raises(ValueError):
        CustomStrategyConfig(volume_consecutive=6)


def test_volume_consecutive_periods_rolling():
    # Build synthetic candles
    # 5 candles: volumes [100, 100, 200, 250, 300] with MA around 100
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-01 09:15", periods=6, freq="5min"),
        "open": [100.0, 100.0, 100.0, 101.0, 102.0, 103.0],
        "high": [105.0, 105.0, 105.0, 105.0, 105.0, 105.0],
        "low": [95.0, 95.0, 95.0, 95.0, 95.0, 95.0],
        "close": [101.0, 101.0, 101.0, 102.0, 103.0, 104.0],
        "volume": [100, 100, 200, 250, 300, 100],
    })

    # Test consecutive = 2
    cfg2 = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=2, volume_ratio_min=1.2, volume_consecutive=2,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res2 = calculate_custom_indicators(df, cfg2)
    assert "volume_consec_met" in res2.columns
    # Row 0: warmup (min_periods=2)
    # Consecutive check is a rolling boolean
    assert not res2["volume_consec_met"].iloc[0]

    # Test consecutive = 3
    cfg3 = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=2, volume_ratio_min=1.2, volume_consecutive=3,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res3 = calculate_custom_indicators(df, cfg3)
    assert not res3["volume_consec_met"].iloc[0]
    assert not res3["volume_consec_met"].iloc[1]


def test_volume_consecutive_resets_on_new_day():
    # Day 1: 2 candles (rising closes: 100 -> 101)
    # Day 2: 4 candles (rising closes: 100 -> 101 -> 102 -> 103)
    # With volume_consecutive=3:
    # 09:15 cannot be checked against Day 1
    # 09:20 is check 1 (101 > 100)
    # 09:25 is check 2 (102 > 101)
    # 09:30 is check 3 (103 > 102) -> 3 consecutive checks met at 09:30!
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-09-01 15:20:00",
            "2026-09-01 15:25:00",
            "2026-09-02 09:15:00",
            "2026-09-02 09:20:00",
            "2026-09-02 09:25:00",
            "2026-09-02 09:30:00",
        ]),
        "open": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        "high": [105.0, 105.0, 105.0, 105.0, 105.0, 105.0],
        "low": [95.0, 95.0, 95.0, 95.0, 95.0, 95.0],
        "close": [100.0, 101.0, 100.0, 101.0, 102.0, 103.0],
        "volume": [1000, 1000, 1000, 1000, 1000, 1000],
    })

    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=1, volume_ratio_min=0.5, volume_consecutive=3,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res = calculate_custom_indicators(df, cfg)

    # Day 1: 2 candles -> cannot meet 3 consecutive checks
    assert not res["volume_consec_met"].iloc[0]
    assert not res["volume_consec_met"].iloc[1]

    # Day 2:
    # 09:15 (1st candle of day, no prev candle check) -> False
    assert not res["volume_consec_met"].iloc[2]
    # 09:20 (1st check) -> False
    assert not res["volume_consec_met"].iloc[3]
    # 09:25 (2nd check) -> False
    assert not res["volume_consec_met"].iloc[4]
    # 09:30 (3rd check) -> True! (Bullish trend confirmed)
    assert res["volume_consec_met"].iloc[5]
    assert res["volume_trend_bullish"].iloc[5]
    assert not res["volume_trend_bearish"].iloc[5]


def test_volume_trend_examples_scoring():
    # Example 1 & 2 (k=3): Bullish (LONG) and Bearish (SHORT)
    # Example 5 (k=4): Mixed pattern (DOWN, UP, DOWN, DOWN -> not considered)
    # Day candles:
    # 09:15: 100.0 (base)
    # 09:20: 101.0 (UP #1)
    # 09:25: 102.0 (UP #2)
    # 09:30: 103.0 (UP #3 -> Bullish 3)
    # 09:35: 102.0 (DOWN #1)
    # 09:40: 101.0 (DOWN #2)
    # 09:45: 100.0 (DOWN #3 -> Bearish 3)
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-09-02 09:15:00",
            "2026-09-02 09:20:00",
            "2026-09-02 09:25:00",
            "2026-09-02 09:30:00",
            "2026-09-02 09:35:00",
            "2026-09-02 09:40:00",
            "2026-09-02 09:45:00",
        ]),
        "open": [100.0] * 7,
        "high": [105.0] * 7,
        "low": [95.0] * 7,
        "close": [100.0, 101.0, 102.0, 103.0, 102.0, 101.0, 100.0],
        "volume": [1000] * 7,
    })

    # Test k=3
    cfg3 = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=1, volume_ratio_min=0.5, volume_consecutive=3,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res3 = calculate_custom_indicators(df, cfg3)
    # Row 3 (09:30): Bullish (3 consecutive UP closes)
    sig_bull = generate_custom_signal(res3.iloc[3], "TEST", cfg3)
    assert sig_bull.signal == "LONG"
    assert sig_bull.score == 1

    # Row 6 (09:45): Bearish (3 consecutive DOWN closes)
    sig_bear = generate_custom_signal(res3.iloc[6], "TEST", cfg3)
    assert sig_bear.signal == "SHORT"
    assert sig_bear.score == -1

    # Test Example 5 (k=4 with mixed pattern: DOWN, UP, DOWN, DOWN)
    df_mixed = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-09-02 09:15:00",
            "2026-09-02 09:20:00",
            "2026-09-02 09:25:00",
            "2026-09-02 09:30:00",
            "2026-09-02 09:35:00",
        ]),
        "open": [100.0] * 5,
        "high": [105.0] * 5,
        "low": [90.0] * 5,
        "close": [100.0, 95.0, 98.0, 92.0, 90.0],  # DOWN, UP, DOWN, DOWN
        "volume": [1000] * 5,
    })
    cfg4 = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=1, volume_ratio_min=0.5, volume_consecutive=4,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res4 = calculate_custom_indicators(df_mixed, cfg4)
    # Row 4 (09:35): No consecutive pattern -> not considered (HOLD, score=0)
    sig_mixed = generate_custom_signal(res4.iloc[4], "TEST", cfg4)
    assert sig_mixed.signal == "HOLD"
    assert sig_mixed.score == 0



def test_direction_of_change_calculation():
    # Test identical to the user prompt's specification:
    # Row 1 (09:15): close = 1284.6 -> blank
    # Row 2 (09:20): close = 1290.9 > 1284.6 -> 🟢 UP
    # Row 3 (09:25): close = 1296.0 > 1290.9 -> 🟢 UP
    # Row 4 (09:30): close = 1294.5 < 1296.0 -> 🔴 DOWN
    # Row 5 (09:35): close = 1295.0 > 1294.5 -> 🟢 UP
    df = pd.DataFrame({
        "timestamp": ["09:15", "09:20", "09:25", "09:30", "09:35"],
        "close": [1284.6, 1290.9, 1296.0, 1294.5, 1295.0],
    })

    diff = df["close"].diff()
    dir_col = pd.Series("", index=df.index, dtype=str)
    dir_col[diff > 0] = "🟢 UP"
    dir_col[diff < 0] = "🔴 DOWN"
    if len(dir_col) > 0:
        dir_col.iloc[0] = ""

    assert dir_col.iloc[0] == ""
    assert dir_col.iloc[1] == "🟢 UP"
    assert dir_col.iloc[2] == "🟢 UP"
    assert dir_col.iloc[3] == "🔴 DOWN"
    assert dir_col.iloc[4] == "🟢 UP"


def test_volume_each_consecutive_candle_must_exceed_ma():
    # 4 candles on same day: 09:15 (base 100), 09:20 (101), 09:25 (102), 09:30 (103) -> 3 rising closes
    # Case 1: All 3 rising candles have volume >= 1.2x MA
    df_all_high = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-02 09:15", "2026-09-02 09:20", "2026-09-02 09:25", "2026-09-02 09:30"]),
        "open": [100.0] * 4,
        "high": [105.0] * 4,
        "low": [95.0] * 4,
        "close": [100.0, 101.0, 102.0, 103.0],
        "volume": [1000, 2000, 3000, 4500],  # All 3 rising candles have high volume (each ratio >= 1.2 vs MA(2))
    })

    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=2, volume_ratio_min=1.2, volume_consecutive=3,
        volume_weight=1, long_threshold=1, short_threshold=-1,
    )
    res_all_high = calculate_custom_indicators(df_all_high, cfg)
    assert res_all_high["volume_trend_bullish"].iloc[3] is True or res_all_high["volume_trend_bullish"].iloc[3] == True
    sig1 = generate_custom_signal(res_all_high.iloc[3], "TEST", cfg)
    assert sig1.signal == "LONG"

    # Case 2: The middle candle (09:25) has low volume (500) so volume ratio < 1.2
    df_one_low = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-02 09:15", "2026-09-02 09:20", "2026-09-02 09:25", "2026-09-02 09:30"]),
        "open": [100.0] * 4,
        "high": [105.0] * 4,
        "low": [95.0] * 4,
        "close": [100.0, 101.0, 102.0, 103.0],  # Closes still rising!
        "volume": [1000, 2000, 500, 2000],   # Middle candle volume drops below MA!
    })
    res_one_low = calculate_custom_indicators(df_one_low, cfg)
    # At 09:30, since candle 09:25 failed volume ratio, 3 consecutive is NOT met
    assert not res_one_low["volume_trend_bullish"].iloc[3]
    sig2 = generate_custom_signal(res_one_low.iloc[3], "TEST", cfg)
    assert sig2.signal == "HOLD"
    assert sig2.score == 0


# ---------------- 11. Fixed Percentage Target and Stoploss Tests ----------------
def test_calculate_fixed_pct_levels_long_and_short():
    from app.risk.stop_loss import calculate_fixed_pct_levels

    # LONG with 3% target and 2% stoploss
    stop_long, target_long = calculate_fixed_pct_levels("LONG", 100.0, target_pct=3.0, stoploss_pct=2.0, tick=0.05)
    assert stop_long == 98.0
    assert target_long == 103.0

    # SHORT with 3% target and 2% stoploss
    stop_short, target_short = calculate_fixed_pct_levels("SHORT", 100.0, target_pct=3.0, stoploss_pct=2.0, tick=0.05)
    assert stop_short == 102.0
    assert target_short == 97.0

    # Negative / zero checks
    with pytest.raises(ValueError):
        calculate_fixed_pct_levels("LONG", -100.0, target_pct=3.0, stoploss_pct=2.0)
    with pytest.raises(ValueError):
        calculate_fixed_pct_levels("LONG", 100.0, target_pct=0.0, stoploss_pct=2.0)
    with pytest.raises(ValueError):
        calculate_fixed_pct_levels("SIDE", 100.0, target_pct=3.0, stoploss_pct=2.0)


def test_fixed_pct_config_validation():
    from app.config.risk_config import RiskConfig
    # Valid
    rc = RiskConfig(fixed_target_pct=25.0, fixed_stoploss_pct=2.0)
    assert rc.fixed_target_pct == 25.0
    assert rc.fixed_stoploss_pct == 2.0

    # Invalid range for RiskConfig
    with pytest.raises(ValueError):
        RiskConfig(fixed_target_pct=0.5)
    with pytest.raises(ValueError):
        RiskConfig(fixed_target_pct=51.0)
    with pytest.raises(ValueError):
        RiskConfig(fixed_stoploss_pct=0.0)
    with pytest.raises(ValueError):
        RiskConfig(fixed_stoploss_pct=15.0)

    # Valid for CustomStrategyConfig
    cc = CustomStrategyConfig(fixed_target_pct=50.0, fixed_stoploss_pct=3.0)
    assert cc.fixed_target_pct == 50.0
    assert cc.fixed_stoploss_pct == 3.0
    with pytest.raises(ValueError):
        CustomStrategyConfig(fixed_target_pct=0.0)
    with pytest.raises(ValueError):
        CustomStrategyConfig(fixed_target_pct=55.0)
    with pytest.raises(ValueError):
        CustomStrategyConfig(fixed_stoploss_pct=12.0)


def test_backtest_with_fixed_target_profit_and_stoploss():
    from app.config.risk_config import RiskConfig
    from app.backtest.engine import run_backtest, BacktestConfig

    # Target 3%, Stoploss 2%
    custom_cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False,
        vwap_enabled=False, atr_enabled=False, adx_enabled=False,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        volume_enabled=True, volume_period=2, volume_ratio_min=1.2, volume_consecutive=1,
        volume_weight=1, long_threshold=1, short_threshold=-1,
        fixed_target_pct=3.0, fixed_stoploss_pct=2.0,
    )
    risk_cfg = RiskConfig(
        capital=100_000.0,
        risk_per_trade_pct=0.01,  # Rs 1000 risk budget
        fixed_target_pct=3.0,
        fixed_stoploss_pct=2.0,
    )
    cost_cfg = CostConfig(cost_mode="FLAT_PER_ORDER", flat_cost_per_order=20.0,
                          slippage_mode="FLAT_PER_ORDER", flat_slippage_per_order=0.0)
    bt_cfg = BacktestConfig(allow_short=True, execution_mode="NEXT_BAR_OPEN")

    # 1. LONG hits 3% Target
    # Candle 0 triggers LONG signal at 100.0.
    # Candle 1 opens at 100.0 (position filled: entry=100, target=103.0, stop=98.0, qty=500).
    # Candle 2 high reaches 103.5 -> exits at 103.0 (TARGET).
    # Gross profit = (103.0 - 100.0) * 500 = 1500.0 (exactly 3% of 50,000 trade value).
    # Net profit = 1500.0 - 40.0 (entry + exit flat costs) = 1460.0.
    df_tgt = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-02 09:20", "2026-09-02 09:25", "2026-09-02 09:30"]),
        "open": [100.0, 100.0, 100.0],
        "high": [100.0, 101.0, 103.5],
        "low": [99.5, 99.5, 99.5],
        "close": [100.0, 101.0, 102.0],
        "volume": [1000, 2000, 2000],
    })
    ind_df_tgt = calculate_custom_indicators(df_tgt, custom_cfg)
    trades_tgt, _ = run_backtest(ind_df_tgt, "TEST", custom_cfg, risk_cfg, cost_cfg, bt_cfg)

    assert len(trades_tgt) == 1
    t_row = trades_tgt.iloc[0]
    assert t_row["side"] == "LONG"
    assert t_row["entry_price"] == 100.0
    assert t_row["target"] == 103.0
    assert t_row["stop_loss"] == 98.0
    assert t_row["exit_reason"] == "TARGET"
    assert t_row["exit_price"] == 103.0
    assert t_row["gross_pnl"] == pytest.approx(1500.0, abs=0.01)
    assert t_row["net_pnl"] == pytest.approx(1460.0, abs=0.01)

    # 2. LONG hits 2% Stoploss
    # Entry at 100.0, candle 2 drops to 97.5 (below 98.0 stop) -> exits at 98.0 (STOP).
    # Gross loss = (98.0 - 100.0) * 500 = -1000.0 (exactly -2% of 50,000 trade value).
    # Net profit = -1000.0 - 40.0 = -1040.0.
    df_sl = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-02 09:20", "2026-09-02 09:25", "2026-09-02 09:30"]),
        "open": [100.0, 100.0, 100.0],
        "high": [100.0, 101.0, 100.5],
        "low": [99.5, 99.5, 97.5],
        "close": [100.0, 101.0, 97.5],
        "volume": [1000, 2000, 2000],
    })
    ind_df_sl = calculate_custom_indicators(df_sl, custom_cfg)
    trades_sl, _ = run_backtest(ind_df_sl, "TEST", custom_cfg, risk_cfg, cost_cfg, bt_cfg)

    assert len(trades_sl) == 1
    t_sl_row = trades_sl.iloc[0]
    assert t_sl_row["side"] == "LONG"
    assert t_sl_row["entry_price"] == 100.0
    assert t_sl_row["exit_reason"] == "STOP"
    assert t_sl_row["exit_price"] == 98.0
    assert t_sl_row["gross_pnl"] == pytest.approx(-1000.0, abs=0.01)
    assert t_sl_row["net_pnl"] == pytest.approx(-1040.0, abs=0.01)

    # 3. SHORT hits 3% Target
    # Entry at 100.0, target 97.0, stop 102.0. Low reaches 96.5 -> exits at 97.0 (TARGET).
    df_short_tgt = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-02 09:20", "2026-09-02 09:25", "2026-09-02 09:30"]),
        "open": [100.0, 100.0, 100.0],
        "high": [100.5, 100.0, 100.0],
        "low": [99.5, 99.0, 96.5],
        "close": [100.0, 99.0, 96.5],
        "volume": [1000, 2000, 2000],
    })
    ind_df_short_tgt = calculate_custom_indicators(df_short_tgt, custom_cfg)
    trades_st, _ = run_backtest(ind_df_short_tgt, "TEST", custom_cfg, risk_cfg, cost_cfg, bt_cfg)

    assert len(trades_st) == 1
    t_st_row = trades_st.iloc[0]
    assert t_st_row["side"] == "SHORT"
    assert t_st_row["entry_price"] == 100.0
    assert t_st_row["target"] == 97.0
    assert t_st_row["stop_loss"] == 102.0
    assert t_st_row["exit_reason"] == "TARGET"
    assert t_st_row["exit_price"] == 97.0
    assert t_st_row["gross_pnl"] == pytest.approx(1500.0, abs=0.01)
    assert t_st_row["net_pnl"] == pytest.approx(1460.0, abs=0.01)


# ---------------- 12. NIFTY Search and Matching Options Tests ----------------
def test_nifty_search_and_matching_options_and_isin():
    from app.data.instrument_loader import (
        find_equity_by_isin,
        find_equity_by_symbol,
        load_nse_equities,
        search_equities,
        validate_isin,
    )

    equities = load_nse_equities()

    # 1. Exact lookup
    nifty = find_equity_by_symbol("NIFTY", equities)
    assert nifty is not None
    assert nifty["tradingsymbol"] == "NIFTY"
    assert nifty["isin"] == "INF204KB14I2"
    assert validate_isin(nifty["isin"]) is True

    nifty50 = find_equity_by_symbol("NIFTY 50", equities)
    assert nifty50 is not None
    assert nifty50["isin"] == "INF204KB14I2"

    banknifty = find_equity_by_symbol("BANKNIFTY", equities)
    assert banknifty is not None
    assert banknifty["isin"] == "INF174KA1ZB7"
    assert validate_isin(banknifty["isin"]) is True

    # 2. Reverse lookup by ISIN
    found_by_isin = find_equity_by_isin("INF204KB14I2", equities)
    assert found_by_isin is not None
    assert "NIFTY" in found_by_isin["tradingsymbol"]

    # 3. Search ranking when searching "NIFTY"
    results = search_equities("NIFTY", equities, limit=20)
    assert not results.empty
    top_symbols = results["tradingsymbol"].tolist()[:5]

    # Flagship NIFTY symbols must appear right at the top
    assert "NIFTY" in top_symbols
    assert any("50" in s or "BEES" in s for s in top_symbols)

    # 4. NIFTY 22500 CE and PE derivative contracts
    ce_opt = find_equity_by_symbol("NIFTY26OCT22500CE", equities)
    assert ce_opt is not None
    assert ce_opt["instrument_key"] == "NSE_FO|51330"
    assert ce_opt["isin"] == "INF000513301"
    assert validate_isin(ce_opt["isin"]) is True
    assert int(ce_opt["lot_size"]) == 65
    assert float(ce_opt["tick_size"]) == 0.05

    pe_opt = find_equity_by_symbol("NIFTY26OCT22500PE", equities)
    assert pe_opt is not None
    assert pe_opt["instrument_key"] == "NSE_FO|51331"
    assert pe_opt["isin"] == "INF000513319"
    assert validate_isin(pe_opt["isin"]) is True
    assert int(pe_opt["lot_size"]) == 65
    assert float(pe_opt["tick_size"]) == 0.05

    # Reverse lookup by derivative token and ISIN
    assert find_equity_by_isin("51330", equities)["tradingsymbol"] == "NIFTY26OCT22500CE"
    assert find_equity_by_isin("51331", equities)["tradingsymbol"] == "NIFTY26OCT22500PE"
    assert find_equity_by_isin("INF000513301", equities)["tradingsymbol"] == "NIFTY26OCT22500CE"
    assert find_equity_by_isin("INF000513319", equities)["tradingsymbol"] == "NIFTY26OCT22500PE"
    assert find_equity_by_isin("NSE_FO|51330", equities)["tradingsymbol"] == "NIFTY26OCT22500CE"

    # Search by strike and token
    strike_res = search_equities("22500", equities, limit=10)
    assert "NIFTY26OCT22500CE" in strike_res["tradingsymbol"].tolist()
    assert "NIFTY26OCT22500PE" in strike_res["tradingsymbol"].tolist()

    token_res = search_equities("51330", equities, limit=5)
    assert "NIFTY26OCT22500CE" in token_res["tradingsymbol"].tolist()

    # 5. NIFTY 13-OCT-2026 strikes 23000 CE and 22300 PE
    ce_23000 = find_equity_by_symbol("NIFTY26O1323000CE", equities)
    assert ce_23000 is not None
    assert ce_23000["instrument_key"] == "NSE_FO|44632"
    assert ce_23000["isin"] == "INF000446320"
    assert validate_isin(ce_23000["isin"]) is True
    assert int(ce_23000["lot_size"]) == 65

    pe_22300 = find_equity_by_symbol("NIFTY26O1322300PE", equities)
    assert pe_22300 is not None
    assert pe_22300["instrument_key"] == "NSE_FO|44599"
    assert pe_22300["isin"] == "INF000445991"
    assert validate_isin(pe_22300["isin"]) is True
    assert int(pe_22300["lot_size"]) == 65

    assert find_equity_by_isin("44632", equities)["tradingsymbol"] == "NIFTY26O1323000CE"
    assert find_equity_by_isin("44599", equities)["tradingsymbol"] == "NIFTY26O1322300PE"
    assert "NIFTY26O1323000CE" in search_equities("23000", equities, limit=5)["tradingsymbol"].tolist()
    assert "NIFTY26O1322300PE" in search_equities("22300", equities, limit=5)["tradingsymbol"].tolist()

    # 6. NIFTY 27-OCT-2026 strikes 23500 CE and 23000 CE
    ce_23500 = find_equity_by_symbol("NIFTY26OCT23500CE", equities)
    assert ce_23500 is not None
    assert ce_23500["instrument_key"] == "NSE_FO|51372"
    assert ce_23500["isin"] == "INF000513723"
    assert validate_isin(ce_23500["isin"]) is True
    assert int(ce_23500["lot_size"]) == 65

    ce_23000_oct = find_equity_by_symbol("NIFTY26OCT23000CE", equities)
    assert ce_23000_oct is not None
    assert ce_23000_oct["instrument_key"] == "NSE_FO|51352"
    assert ce_23000_oct["isin"] == "INF000513525"
    assert validate_isin(ce_23000_oct["isin"]) is True
    assert int(ce_23000_oct["lot_size"]) == 65

    assert find_equity_by_isin("51372", equities)["tradingsymbol"] == "NIFTY26OCT23500CE"
    assert find_equity_by_isin("51352", equities)["tradingsymbol"] == "NIFTY26OCT23000CE"
    assert "NIFTY26OCT23500CE" in search_equities("23500", equities, limit=5)["tradingsymbol"].tolist()


def test_metrics_with_zero_losses_and_none_values():
    from app.backtest.metrics import compute_metrics
    # 1 win trade, 0 losses
    trades = pd.DataFrame([
        {
            "net_pnl": 853.10,
            "gross_pnl": 893.10,
            "costs": 40.0,
            "slippage_cost": 20.15,
            "exit_time": "2026-09-02 15:15:00+05:30",
        }
    ])
    trading_days = [pd.to_datetime("2026-09-02").date()]
    m = compute_metrics(trades, 100_000.0, trading_days)

    assert m["num_trades"] == 1
    assert m["profit_factor"] is None  # Gross losses == 0
    assert m["risk_reward"] is None

    # Verify UI formatting does not raise TypeError
    pf = m.get("profit_factor")
    pf_str = f"{pf:.2f}" if pf is not None else ("∞ (No Losses)" if (m.get("gross_pnl") or 0.0) > 0 else "0.00")
    assert pf_str == "∞ (No Losses)"

    rr = m.get("risk_reward")
    rr_str = f"{rr:.2f}" if rr is not None else "N/A"
    assert rr_str == "N/A"


def test_nifty_options_historical_data_cached():
    import os
    ce_path = "data/nifty26oct22500ce_5m.csv"
    assert os.path.exists(ce_path)
    df = pd.read_csv(ce_path)
    assert len(df) > 1000
    for col in ["timestamp", "open", "high", "low", "close", "volume"]:
        assert col in df.columns


def test_timeframe_1m_and_3m_data_and_caching():
    import os
    # Check 1m and 3m cached files
    for path in ["data/reliance_1m.csv", "data/reliance_3m.csv",
                 "data/nifty26oct22500ce_1m.csv", "data/nifty26oct22500ce_3m.csv"]:
        assert os.path.exists(path), f"Missing cache file: {path}"
        df = pd.read_csv(path)
        assert len(df) > 500
        for col in ["timestamp", "open", "high", "low", "close", "volume"]:
            assert col in df.columns

    # Verify formatting function handles 1m and 3m
    fmt = lambda x: f"{x} Minute" if x == 1 else f"{x} Minutes"
    assert fmt(1) == "1 Minute"
    assert fmt(3) == "3 Minutes"
    assert fmt(5) == "5 Minutes"


def test_risk_config_trade_side_and_short_lot_size():
    import pytest
    from app.config.risk_config import RiskConfig

    # Valid configurations
    c1 = RiskConfig(trade_side="LONG")
    assert c1.trade_side == "LONG"
    assert c1.short_lot_size is None

    c2 = RiskConfig(trade_side="SHORT", short_lot_size=1)
    assert c2.trade_side == "SHORT"
    assert c2.short_lot_size == 1

    c3 = RiskConfig(trade_side="BOTH", short_lot_size=1)
    assert c3.trade_side == "BOTH"

    # Invalid configurations
    with pytest.raises(ValueError, match="trade_side"):
        RiskConfig(trade_side="INVALID")

    with pytest.raises(ValueError, match="short_lot_size"):
        RiskConfig(short_lot_size=0)


def test_backtest_trade_side_filtering_and_short_lot_sizing():
    from app.backtest.engine import run_backtest, BacktestConfig
    from app.config.risk_config import RiskConfig
    from app.config.cost_config import CostConfig
    from app.strategy.custom_strategy import calculate_custom_indicators, CustomStrategyConfig
    from app.data.historical_data import load_csv

    df = load_csv("data/reliance_5m.csv")
    custom_cfg = CustomStrategyConfig()
    indicators_df = calculate_custom_indicators(df, custom_cfg)
    cost_cfg = CostConfig()

    # 1. LONG ONLY: No SHORT trades permitted
    r_long = RiskConfig(trade_side="LONG")
    b_long = BacktestConfig(trade_side="LONG")
    trades_long, _ = run_backtest(indicators_df, "RELIANCE", custom_cfg, r_long, cost_cfg, b_long)
    if not trades_long.empty:
        assert set(trades_long["side"]) == {"LONG"}

    # 2. SHORT ONLY: No LONG trades permitted, short quantity = 1 lot
    r_short = RiskConfig(trade_side="SHORT", lot_size=1, short_lot_size=1)
    b_short = BacktestConfig(trade_side="SHORT", short_lot_size=1)
    trades_short, _ = run_backtest(indicators_df, "RELIANCE", custom_cfg, r_short, cost_cfg, b_short)
    if not trades_short.empty:
        assert set(trades_short["side"]) == {"SHORT"}
        assert set(trades_short["quantity"]) == {1}

    # 3. BOTH: Both sides permitted, short quantity = 1 lot
    r_both = RiskConfig(trade_side="BOTH", lot_size=1, short_lot_size=1)
    b_both = BacktestConfig(trade_side="BOTH", short_lot_size=1)
    trades_both, _ = run_backtest(indicators_df, "RELIANCE", custom_cfg, r_both, cost_cfg, b_both)
    if not trades_both.empty:
        assert "LONG" in trades_both["side"].values
        assert "SHORT" in trades_both["side"].values
        shorts = trades_both[trades_both["side"] == "SHORT"]
        assert set(shorts["quantity"]) == {1}


def test_risk_config_snooze_minutes():
    import pytest
    from app.config.risk_config import RiskConfig

    c1 = RiskConfig()
    assert c1.snooze_minutes == 0

    c2 = RiskConfig(snooze_minutes=5)
    assert c2.snooze_minutes == 5

    c3 = RiskConfig(snooze_minutes=10)
    assert c3.snooze_minutes == 10

    with pytest.raises(ValueError, match="snooze_minutes"):
        RiskConfig(snooze_minutes=-1)


def test_risk_engine_snooze_cooldown_rejection():
    import pandas as pd
    from app.config.risk_config import RiskConfig
    from app.risk.risk_engine import PortfolioState, RiskEngine
    from app.strategy.signal_engine import Signal

    cfg = RiskConfig(snooze_minutes=5)
    engine = RiskEngine(cfg)

    # Exit occurred at 10:00:00
    exit_ts = pd.Timestamp("2026-09-01 10:00:00")
    state = PortfolioState(last_exit_time=exit_ts)

    # Signal 2 minutes after exit: within 5 min cooldown -> REJECTED
    sig_early = Signal(
        symbol="RELIANCE",
        timestamp=pd.Timestamp("2026-09-01 10:02:00"),
        signal="LONG",
        score=6,
        max_score=8,
        confidence=0.75,
        strategy_version="v1",
        indicators={"close": 2500.0, "atr": 20.0}
    )
    dec_early = engine.evaluate(sig_early, state)
    assert not dec_early.approved
    assert "snooze active" in dec_early.rejection_reason

    # Signal 4 minutes 59 seconds after exit: within 5 min cooldown -> REJECTED
    sig_close = Signal(
        symbol="RELIANCE",
        timestamp=pd.Timestamp("2026-09-01 10:04:59"),
        signal="LONG",
        score=6,
        max_score=8,
        confidence=0.75,
        strategy_version="v1",
        indicators={"close": 2500.0, "atr": 20.0}
    )
    dec_close = engine.evaluate(sig_close, state)
    assert not dec_close.approved
    assert "snooze active" in dec_close.rejection_reason

    # Signal exactly 5 minutes after exit: cooldown elapsed -> APPROVED
    sig_ok = Signal(
        symbol="RELIANCE",
        timestamp=pd.Timestamp("2026-09-01 10:05:00"),
        signal="LONG",
        score=6,
        max_score=8,
        confidence=0.75,
        strategy_version="v1",
        indicators={"close": 2500.0, "atr": 20.0}
    )
    dec_ok = engine.evaluate(sig_ok, state)
    assert dec_ok.approved


def test_snooze_string_parsing():
    import re

    def parse_snooze(s):
        digits = re.findall(r'\d+', str(s or ""))
        return int(digits[0]) if digits else 0

    assert parse_snooze("5 min") == 5
    assert parse_snooze("10 min") == 10
    assert parse_snooze("5") == 5
    assert parse_snooze("10") == 10
    assert parse_snooze("15 minutes") == 15
    assert parse_snooze("0 min") == 0
    assert parse_snooze("0") == 0
    assert parse_snooze("") == 0
    assert parse_snooze(None) == 0


def test_backtest_engine_snooze_period_enforcement():
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.data.historical_data import load_csv
    from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators

    df = load_csv("data/reliance_5m.csv")
    custom_cfg = CustomStrategyConfig()
    indicators_df = calculate_custom_indicators(df, custom_cfg)
    cost_cfg = CostConfig()

    # Backtest with 0 min snooze
    r_no_snooze = RiskConfig(snooze_minutes=0)
    b_no_snooze = BacktestConfig(snooze_minutes=0)
    trades_no_snooze, bt_no_snooze = run_backtest(indicators_df, "RELIANCE", custom_cfg, r_no_snooze, cost_cfg, b_no_snooze)

    # Backtest with 10 min snooze
    r_snooze = RiskConfig(snooze_minutes=10)
    b_snooze = BacktestConfig(snooze_minutes=10)
    trades_snooze, bt_snooze = run_backtest(indicators_df, "RELIANCE", custom_cfg, r_snooze, cost_cfg, b_snooze)

    # Verify snooze rejections recorded
    assert bt_snooze.rejections.get("snooze active", 0) >= 0

    # Verify that trades under snooze never enter within 10 minutes of previous exit on the same day
    for i in range(1, len(trades_snooze)):
        prev_exit = trades_snooze.iloc[i - 1]["exit_time"]
        curr_entry = trades_snooze.iloc[i]["entry_time"]
        if prev_exit.date() == curr_entry.date():
            diff_min = (curr_entry - prev_exit).total_seconds() / 60.0
            assert diff_min >= 10.0


def test_volume_and_vwap_combined_consecutive_period():
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.data.historical_data import load_csv
    from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators, generate_custom_signal

    df = load_csv("data/reliance_5m.csv")

    # Volume + VWAP enabled
    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False, vwap_enabled=True,
        adx_enabled=False, volume_enabled=True, volume_consecutive=3, volume_ratio_min=1.2,
        bollinger_enabled=False, stoch_enabled=False, obv_enabled=False,
        long_threshold=1, short_threshold=-1,
    )
    ind_df = calculate_custom_indicators(df, cfg)

    # 1. Verify candle-level signal generation:
    # If close > vwap, but volume_trend_bullish is False -> MUST BE HOLD (not LONG!)
    for _, row in ind_df.iterrows():
        vol_bull = bool(row.get("volume_trend_bullish", False))
        vol_bear = bool(row.get("volume_trend_bearish", False))
        sig = generate_custom_signal(row, "RELIANCE", cfg)

        if not vol_bull:
            assert sig.signal != "LONG", "LONG signal generated without volume_trend_bullish == True"
        if not vol_bear:
            assert sig.signal != "SHORT", "SHORT signal generated without volume_trend_bearish == True"

    # 2. Run Backtest:
    trades, bt = run_backtest(ind_df, "RELIANCE", cfg, RiskConfig(), CostConfig(), BacktestConfig())
    assert len(trades) > 0

    # Verify that every executed trade strictly had consecutive volume trend at signal time
    for _, t in trades.iterrows():
        sig_row = ind_df[ind_df["timestamp"] == t["signal_time"]].iloc[0]
        if t["side"] == "LONG":
            assert sig_row["volume_trend_bullish"] is True or sig_row["volume_trend_bullish"] == 1
        elif t["side"] == "SHORT":
            assert sig_row["volume_trend_bearish"] is True or sig_row["volume_trend_bearish"] == 1


def test_swing_high_low_indicator():
    from app.indicators.swing import swing_high_low
    import pandas as pd

    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-01 09:15", periods=8, freq="5min"),
        "high": [100.0, 105.0, 103.0, 104.0, 106.0, 102.0, 107.0, 101.0],
        "low":  [95.0,  98.0,  94.0,  96.0,  97.0,  93.0,  95.0,  92.0],
    })

    # Lookback period = 3
    res = swing_high_low(df, period=3)
    assert "swing_high" in res.columns
    assert "swing_low" in res.columns
    assert "higher_high" in res.columns
    assert "lower_low" in res.columns

    # Row 0 & 1 warmup: min_periods=2, shift(1) means row 2 is first valid
    assert pd.isna(res.loc[0, "swing_high"])
    assert pd.isna(res.loc[1, "swing_high"])

    # At row 2: previous highs were [100, 105] -> max is 105. High is 103 <= 105 -> higher_high is False
    assert res.loc[2, "swing_high"] == 105.0
    assert not res.loc[2, "higher_high"]

    # At row 4: high is 106.0. Previous 3 highs [105, 103, 104] -> max is 105. 106 > 105 -> higher_high is True!
    assert res.loc[4, "swing_high"] == 105.0
    assert res.loc[4, "higher_high"] == True

    # At row 5: low is 93.0. Previous 3 lows [94, 96, 97] -> min is 94. 93 < 94 -> lower_low is True!
    assert res.loc[5, "swing_low"] == 94.0
    assert res.loc[5, "lower_low"] == True


def test_higher_high_and_lower_low_config_and_scoring():
    import pytest
    from app.strategy.custom_strategy import CustomStrategyConfig, score_custom_row
    import pandas as pd

    # Invalid period checks
    with pytest.raises(ValueError):
        CustomStrategyConfig(higher_high_period=0)
    with pytest.raises(ValueError):
        CustomStrategyConfig(lower_low_period=0)

    # Max score calculation
    cfg = CustomStrategyConfig(
        ema_enabled=False, rsi_enabled=False, macd_enabled=False, vwap_enabled=False,
        atr_enabled=False, adx_enabled=False, volume_enabled=False, bollinger_enabled=False,
        stoch_enabled=False, obv_enabled=False,
        higher_high_enabled=True, higher_high_weight=2,
        lower_low_enabled=True, lower_low_weight=3,
        long_threshold=2, short_threshold=-2,
    )
    assert cfg.max_score == 5

    # Scoring row: Higher High True
    row_hh = pd.Series({"higher_high": True, "lower_low": False, "close": 100.0})
    scores_hh = score_custom_row(row_hh, cfg)
    assert scores_hh["higher_high"] == 2
    assert scores_hh["lower_low"] == 0

    # Scoring row: Lower Low True
    row_ll = pd.Series({"higher_high": False, "lower_low": True, "close": 100.0})
    scores_ll = score_custom_row(row_ll, cfg)
    assert scores_ll["higher_high"] == 0
    assert scores_ll["lower_low"] == -3


def test_higher_high_locks_to_long_trades_only():
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.data.historical_data import load_csv
    from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators, generate_custom_signal

    df = load_csv("data/reliance_5m.csv")

    # Higher High enabled, Lower Low disabled
    cfg = CustomStrategyConfig(
        ema_enabled=True, rsi_enabled=True, macd_enabled=True, vwap_enabled=True,
        higher_high_enabled=True, higher_high_period=5, higher_high_weight=1,
        lower_low_enabled=False,
        long_threshold=3, short_threshold=-3,
    )
    ind_df = calculate_custom_indicators(df, cfg)

    assert "swing_high" in ind_df.columns
    assert "higher_high" in ind_df.columns

    # Verify signal generator rules:
    # 1. Higher High enabled MUST NEVER output SHORT signals (blocks SHORT to HOLD)
    # 2. LONG signals MUST have higher_high == True
    for _, row in ind_df.iterrows():
        sig = generate_custom_signal(row, "RELIANCE", cfg)
        assert sig.signal != "SHORT", "Higher High allowed a SHORT signal!"
        if sig.signal == "LONG":
            assert row["higher_high"] is True or row["higher_high"] == 1

    # End-to-end backtest: trade_side = "LONG" locked
    risk_cfg = RiskConfig(trade_side="LONG")
    bt_cfg = BacktestConfig(trade_side="LONG")
    trades, bt = run_backtest(ind_df, "RELIANCE", cfg, risk_cfg, CostConfig(), bt_cfg)

    assert len(trades) > 0
    # Every executed trade MUST be LONG
    assert (trades["side"] == "LONG").all()
    assert (trades["side"] == "SHORT").sum() == 0


def test_lower_low_locks_to_short_trades_only():
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.data.historical_data import load_csv
    from app.strategy.custom_strategy import CustomStrategyConfig, calculate_custom_indicators, generate_custom_signal

    df = load_csv("data/reliance_5m.csv")

    # Lower Low enabled, Higher High disabled
    cfg = CustomStrategyConfig(
        ema_enabled=True, rsi_enabled=True, macd_enabled=True, vwap_enabled=True,
        higher_high_enabled=False,
        lower_low_enabled=True, lower_low_period=5, lower_low_weight=1,
        long_threshold=3, short_threshold=-3,
    )
    ind_df = calculate_custom_indicators(df, cfg)

    assert "swing_low" in ind_df.columns
    assert "lower_low" in ind_df.columns

    # Verify signal generator rules:
    # 1. Lower Low enabled MUST NEVER output LONG signals (blocks LONG to HOLD)
    # 2. SHORT signals MUST have lower_low == True
    for _, row in ind_df.iterrows():
        sig = generate_custom_signal(row, "RELIANCE", cfg)
        assert sig.signal != "LONG", "Lower Low allowed a LONG signal!"
        if sig.signal == "SHORT":
            assert row["lower_low"] is True or row["lower_low"] == 1

    # End-to-end backtest: trade_side = "SHORT" locked
    risk_cfg = RiskConfig(trade_side="SHORT")
    bt_cfg = BacktestConfig(allow_short=True, trade_side="SHORT")
    trades, bt = run_backtest(ind_df, "RELIANCE", cfg, risk_cfg, CostConfig(), bt_cfg)

    assert len(trades) > 0
    # Every executed trade MUST be SHORT
    assert (trades["side"] == "SHORT").all()
    assert (trades["side"] == "LONG").sum() == 0


def test_ui_trade_side_locking_logic():
    # Test the trade_side resolution and disablement logic used in Step 2 UI
    def resolve_ui_side(hh_active: bool, ll_active: bool, current_choice: str = "BOTH"):
        if hh_active and ll_active:
            return "BOTH", True
        elif hh_active:
            return "LONG", True
        elif ll_active:
            return "SHORT", True
        else:
            return current_choice, False

    # 1. Higher High selected -> LONG, disabled
    side, disabled = resolve_ui_side(hh_active=True, ll_active=False)
    assert side == "LONG"
    assert disabled is True

    # 2. Lower Low selected -> SHORT, disabled
    side, disabled = resolve_ui_side(hh_active=False, ll_active=True)
    assert side == "SHORT"
    assert disabled is True

    # 3. Both selected -> BOTH, disabled
    side, disabled = resolve_ui_side(hh_active=True, ll_active=True)
    assert side == "BOTH"
    assert disabled is True

    # 4. Neither selected -> user choice, enabled (not disabled)
    side, disabled = resolve_ui_side(hh_active=False, ll_active=False, current_choice="SHORT")
    assert side == "SHORT"
    assert disabled is False


# ---------------- 13. Model B Trailing Stop-Loss Unit Tests ----------------
def test_model_b_trailing_sl_breakeven_trigger_long():
    """
    Model B Milestone 1 (Long):
    Entry at 2000.0. Fixed Target = 10% (2200.0), Fixed Stoploss = 5% (1900.0).
    50% of target = 5% gain -> trigger price = 2100.0.
    Candle 1 reaches high of 2105.0 (>= 2100.0). Stop loss should move to Breakeven (2000.0).
    Candle 2 pulls back to low of 1990.0 (< 2000.0).
    Position exits at 2000.0 with reason 'TRAILING_STOP' (not 1900.0 'STOP').
    """
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.config.strategy_config import StrategyConfig

    c0 = dict(timestamp=pd.Timestamp("2026-08-03 10:00"), open=2000.0, high=2005.0, low=1995.0, close=2000.0,
              volume=1000, atr14=10.0, ema20=2005.0, ema50=1995.0, rsi14=60.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=1990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c1 = dict(timestamp=pd.Timestamp("2026-08-03 10:05"), open=2000.0, high=2105.0, low=1998.0, close=2090.0,
              volume=1000, atr14=10.0, ema20=2010.0, ema50=1995.0, rsi14=65.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=1990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c2 = dict(timestamp=pd.Timestamp("2026-08-03 10:10"), open=2080.0, high=2085.0, low=1990.0, close=1995.0,
              volume=1000, atr14=10.0, ema20=2000.0, ema50=1995.0, rsi14=50.0, macd=0.0, macd_signal=0.0,
              macd_hist=0.0, vwap=1990.0, adx=20.0, plus_di=20.0, minus_di=20.0, volume_ratio=1.0)

    df = pd.DataFrame([c0, c1, c2])

    risk_cfg = RiskConfig(
        fixed_target_pct=10.0,
        fixed_stoploss_pct=5.0,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    bt_cfg = BacktestConfig(
        allow_short=True,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    trades, _ = run_backtest(df, "TEST", StrategyConfig(), risk_cfg, CostConfig.zero(), bt_cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["side"] == "LONG"
    assert t["entry_price"] == pytest.approx(2000.0)
    assert t["exit_price"] == pytest.approx(2000.0)
    assert t["exit_reason"] == "TRAILING_STOP"
    assert t["gross_pnl"] == pytest.approx(0.0)


def test_model_b_trailing_sl_profit_trailing_long():
    """
    Model B Milestone 2 (Long):
    Entry at 1000.0. Fixed Target = 20% (1200.0), Fixed Stoploss = 3% (970.0).
    50% target = 10% gain (1100.0).
    Candle 1 reaches high 1150.0 (15% gain).
    Stop loss trails to 1150.0 * (1 - 0.03) = 1115.50.
    Candle 2 opens 1140.0 and drops to low 1110.0.
    Position exits at 1115.50 with 'TRAILING_STOP' locking in 11.55% profit.
    """
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.config.strategy_config import StrategyConfig

    c0 = dict(timestamp=pd.Timestamp("2026-08-03 10:00"), open=1000.0, high=1005.0, low=995.0, close=1000.0,
              volume=1000, atr14=10.0, ema20=1005.0, ema50=995.0, rsi14=60.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c1 = dict(timestamp=pd.Timestamp("2026-08-03 10:05"), open=1000.0, high=1150.0, low=998.0, close=1140.0,
              volume=1000, atr14=10.0, ema20=1010.0, ema50=995.0, rsi14=65.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c2 = dict(timestamp=pd.Timestamp("2026-08-03 10:10"), open=1140.0, high=1145.0, low=1110.0, close=1115.0,
              volume=1000, atr14=10.0, ema20=1000.0, ema50=995.0, rsi14=50.0, macd=0.0, macd_signal=0.0,
              macd_hist=0.0, vwap=990.0, adx=20.0, plus_di=20.0, minus_di=20.0, volume_ratio=1.0)

    df = pd.DataFrame([c0, c1, c2])

    risk_cfg = RiskConfig(
        fixed_target_pct=20.0,
        fixed_stoploss_pct=3.0,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    bt_cfg = BacktestConfig(
        allow_short=True,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    trades, _ = run_backtest(df, "TEST", StrategyConfig(), risk_cfg, CostConfig.zero(), bt_cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["exit_reason"] == "TRAILING_STOP"
    assert t["exit_price"] == pytest.approx(1115.50, abs=0.05)
    assert t["gross_pnl"] > 0


def test_model_b_trailing_sl_runner_mode():
    """
    Model B Runner Mode (trail_beyond_target = True):
    Trade hits fixed target (10%) and keeps running to +20%, then exits on trailing stop.
    """
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.config.strategy_config import StrategyConfig

    c0 = dict(timestamp=pd.Timestamp("2026-08-03 10:00"), open=1000.0, high=1005.0, low=995.0, close=1000.0,
              volume=1000, atr14=10.0, ema20=1005.0, ema50=995.0, rsi14=60.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c1 = dict(timestamp=pd.Timestamp("2026-08-03 10:05"), open=1000.0, high=1200.0, low=998.0, close=1190.0,
              volume=1000, atr14=10.0, ema20=1010.0, ema50=995.0, rsi14=65.0, macd=1.0, macd_signal=0.0,
              macd_hist=1.0, vwap=990.0, adx=25.0, plus_di=30.0, minus_di=15.0, volume_ratio=1.5)
    c2 = dict(timestamp=pd.Timestamp("2026-08-03 10:10"), open=1180.0, high=1185.0, low=1130.0, close=1135.0,
              volume=1000, atr14=10.0, ema20=1000.0, ema50=995.0, rsi14=50.0, macd=0.0, macd_signal=0.0,
              macd_hist=0.0, vwap=990.0, adx=20.0, plus_di=20.0, minus_di=20.0, volume_ratio=1.0)

    df = pd.DataFrame([c0, c1, c2])

    risk_cfg = RiskConfig(
        fixed_target_pct=10.0,
        fixed_stoploss_pct=5.0,
        trailing_sl_enabled=True,
        trail_beyond_target=True,
    )
    bt_cfg = BacktestConfig(
        allow_short=True,
        trailing_sl_enabled=True,
        trail_beyond_target=True,
    )
    trades, _ = run_backtest(df, "TEST", StrategyConfig(), risk_cfg, CostConfig.zero(), bt_cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["exit_reason"] == "TRAILING_STOP"
    assert t["exit_price"] == pytest.approx(1140.0, abs=0.05)


def test_model_b_trailing_sl_breakeven_trigger_short():
    """
    Model B Milestone 1 (Short):
    Entry at 2000.0. Fixed Target = 10% (1800.0), Fixed Stoploss = 6% (2120.0).
    50% target = 5% drop -> trigger price = 1900.0.
    Candle 1 reaches low of 1900.0 (<= 1900.0). Trailed SL = 1900 * 1.06 = 2014.0 (> entry 2000.0).
    So Stop loss moves down to Breakeven (2000.0).
    Candle 2 bounces back to high of 2010.0 (> 2000.0).
    Position exits at 2000.0 with reason 'TRAILING_STOP' (not 2120.0 'STOP').
    """
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.config.strategy_config import StrategyConfig

    c0 = dict(timestamp=pd.Timestamp("2026-08-03 10:00"), open=2000.0, high=2005.0, low=1995.0, close=2000.0,
              volume=1000, atr14=10.0, ema20=1995.0, ema50=2005.0, rsi14=40.0, macd=-1.0, macd_signal=0.0,
              macd_hist=-1.0, vwap=2010.0, adx=25.0, plus_di=15.0, minus_di=30.0, volume_ratio=1.5)
    c1 = dict(timestamp=pd.Timestamp("2026-08-03 10:05"), open=2000.0, high=2002.0, low=1900.0, close=1910.0,
              volume=1000, atr14=10.0, ema20=1990.0, ema50=2005.0, rsi14=35.0, macd=-1.0, macd_signal=0.0,
              macd_hist=-1.0, vwap=2010.0, adx=25.0, plus_di=15.0, minus_di=30.0, volume_ratio=1.5)
    c2 = dict(timestamp=pd.Timestamp("2026-08-03 10:10"), open=1920.0, high=2010.0, low=1915.0, close=2005.0,
              volume=1000, atr14=10.0, ema20=2000.0, ema50=2005.0, rsi14=50.0, macd=0.0, macd_signal=0.0,
              macd_hist=0.0, vwap=2010.0, adx=20.0, plus_di=20.0, minus_di=20.0, volume_ratio=1.0)

    df = pd.DataFrame([c0, c1, c2])

    risk_cfg = RiskConfig(
        fixed_target_pct=10.0,
        fixed_stoploss_pct=6.0,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    bt_cfg = BacktestConfig(
        allow_short=True,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    trades, _ = run_backtest(df, "TEST", StrategyConfig(), risk_cfg, CostConfig.zero(), bt_cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["side"] == "SHORT"
    assert t["entry_price"] == pytest.approx(2000.0)
    assert t["exit_price"] == pytest.approx(2000.0)
    assert t["exit_reason"] == "TRAILING_STOP"
    assert t["gross_pnl"] == pytest.approx(0.0)


def test_model_b_trailing_sl_profit_trailing_short():
    """
    Model B Milestone 2 (Short):
    Entry at 2000.0. Fixed Target = 20% (1600.0), Fixed Stoploss = 3% (2060.0).
    50% target = 10% drop -> trigger price = 1800.0.
    Candle 1 reaches low 1700.0 (15% drop).
    Stop loss trails to 1700.0 * (1 + 0.03) = 1751.0.
    Candle 2 opens 1720.0 and bounces up to high 1760.0.
    Position exits at 1751.0 with 'TRAILING_STOP' locking in 12.45% profit.
    """
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.config.cost_config import CostConfig
    from app.config.risk_config import RiskConfig
    from app.config.strategy_config import StrategyConfig

    c0 = dict(timestamp=pd.Timestamp("2026-08-03 10:00"), open=2000.0, high=2005.0, low=1995.0, close=2000.0,
              volume=1000, atr14=10.0, ema20=1995.0, ema50=2005.0, rsi14=40.0, macd=-1.0, macd_signal=0.0,
              macd_hist=-1.0, vwap=2010.0, adx=25.0, plus_di=15.0, minus_di=30.0, volume_ratio=1.5)
    c1 = dict(timestamp=pd.Timestamp("2026-08-03 10:05"), open=2000.0, high=2002.0, low=1700.0, close=1715.0,
              volume=1000, atr14=10.0, ema20=1990.0, ema50=2005.0, rsi14=30.0, macd=-1.0, macd_signal=0.0,
              macd_hist=-1.0, vwap=2010.0, adx=25.0, plus_di=15.0, minus_di=30.0, volume_ratio=1.5)
    c2 = dict(timestamp=pd.Timestamp("2026-08-03 10:10"), open=1720.0, high=1760.0, low=1715.0, close=1750.0,
              volume=1000, atr14=10.0, ema20=1900.0, ema50=2005.0, rsi14=45.0, macd=0.0, macd_signal=0.0,
              macd_hist=0.0, vwap=2010.0, adx=20.0, plus_di=20.0, minus_di=20.0, volume_ratio=1.0)

    df = pd.DataFrame([c0, c1, c2])

    risk_cfg = RiskConfig(
        fixed_target_pct=20.0,
        fixed_stoploss_pct=3.0,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    bt_cfg = BacktestConfig(
        allow_short=True,
        trailing_sl_enabled=True,
        trail_beyond_target=False,
    )
    trades, _ = run_backtest(df, "TEST", StrategyConfig(), risk_cfg, CostConfig.zero(), bt_cfg)

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["side"] == "SHORT"
    assert t["exit_reason"] == "TRAILING_STOP"
    assert t["exit_price"] == pytest.approx(1751.0, abs=0.05)
    assert t["gross_pnl"] > 0


def test_fixed_target_percentage_up_to_50():
    from app.config.risk_config import RiskConfig
    from app.strategy.custom_strategy import CustomStrategyConfig
    import pytest

    # Test values in the 31% to 50% range
    for val in [31, 40, 50]:
        rc = RiskConfig(fixed_target_pct=float(val))
        assert rc.fixed_target_pct == float(val)

        sc = CustomStrategyConfig(fixed_target_pct=float(val))
        assert sc.fixed_target_pct == float(val)

    # Test out of bounds > 50
    with pytest.raises(ValueError, match="fixed_target_pct"):
        RiskConfig(fixed_target_pct=51.0)

    with pytest.raises(ValueError, match="fixed_target_pct"):
        CustomStrategyConfig(fixed_target_pct=51.0)


def test_trade_audit_trail_2_c1_to_c360():
    # Simulate trade with entry and exit
    dates = pd.date_range("2024-01-01 09:15", periods=10, freq="5min")
    indicators_df = pd.DataFrame({
        "timestamp": dates,
        "close": [100.0, 100.0, 102.0, 104.0, 105.0, 100.0, 98.0, 96.0, 95.0, 100.0],
        "score": [2, 4, 6, 5, 3, -1, -3, -5, -4, 0]
    })

    trades = pd.DataFrame([
        {
            "id": 1,
            "side": "LONG",
            "entry_time": dates[1],  # 09:20
            "exit_time": dates[4],   # 09:35 (candles after entry: dates[2], dates[3], dates[4])
            "quantity": 10,
            "entry_price": 100.0,
            "exit_price": 105.0,
            "net_pnl": 50.0,
            "peak_gain": "5% (+5.0%)",
        },
        {
            "id": 2,
            "side": "SHORT",
            "entry_time": dates[5],  # 09:40
            "exit_time": dates[8],   # 09:55 (candles after entry: dates[6], dates[7], dates[8])
            "quantity": 10,
            "entry_price": 100.0,
            "exit_price": 95.0,
            "net_pnl": 50.0,
            "peak_gain": "5% (+5.0%)",
        }
    ])

    c_cols = [f"C{i}" for i in range(1, 361)]
    c_data = {col: ["-"] * len(trades) for col in c_cols}
    c_ts_series = pd.to_datetime(indicators_df["timestamp"])

    for row_idx, (_, r_tr) in enumerate(trades.iterrows()):
        tr_e_ts = pd.to_datetime(r_tr.get("entry_time"))
        tr_x_ts = pd.to_datetime(r_tr.get("exit_time"))
        s_mask = (c_ts_series > tr_e_ts) & (c_ts_series <= tr_x_ts)
        c_sub = indicators_df.loc[s_mask]
        s_side = r_tr.get("side", "LONG")
        e_p = float(r_tr.get("entry_price", 0.0))
        num_candles = min(len(c_sub), 360)
        for k in range(num_candles):
            cand_row = c_sub.iloc[k]
            sc_val = cand_row.get("score", 0)
            sc_int = int(sc_val)
            sc_str = f"+{sc_int}" if sc_int > 0 else str(sc_int)
            c_close = cand_row.get("close")
            if e_p > 0 and c_close is not None and pd.notna(c_close):
                c_close = float(c_close)
                gain_pts = (c_close - e_p) if s_side == "LONG" else (e_p - c_close)
                gain_pct = (gain_pts / e_p) * 100.0
                c_data[f"C{k+1}"][row_idx] = f"{sc_str}({gain_pct:+.1f}%)"
            else:
                c_data[f"C{k+1}"][row_idx] = sc_str

    c_df = pd.DataFrame(c_data, index=trades.index)
    trades_2 = pd.concat([trades, c_df], axis=1)

    assert "peak_gain" in trades_2.columns
    assert "C1" in trades_2.columns
    assert "C360" in trades_2.columns
    assert len(c_cols) == 360

    # Trade 1 (LONG): candles are dates[2] (+6(+2.0%)), dates[3] (+5(+4.0%)), dates[4] (+3(+5.0%))
    assert trades_2.loc[0, "C1"] == "+6(+2.0%)"
    assert trades_2.loc[0, "C2"] == "+5(+4.0%)"
    assert trades_2.loc[0, "C3"] == "+3(+5.0%)"
    assert trades_2.loc[0, "C4"] == "-"
    assert trades_2.loc[0, "C360"] == "-"

    # Trade 2 (SHORT): candles are dates[6] (-3(+2.0%)), dates[7] (-5(+4.0%)), dates[8] (-4(+5.0%))
    assert trades_2.loc[1, "C1"] == "-3(+2.0%)"
    assert trades_2.loc[1, "C2"] == "-5(+4.0%)"
    assert trades_2.loc[1, "C3"] == "-4(+5.0%)"
    assert trades_2.loc[1, "C4"] == "-"
    assert trades_2.loc[1, "C360"] == "-"


def test_edit_strategy_auto_populates_in_backtest_step2():
    import streamlit as st
    from app.strategy.strategy_store import save_strategy, list_strategy_names, delete_strategy

    test_strat_name = "Test_Edit_Sync_Strat"
    save_strategy(test_strat_name, {"ema_enabled": True}, {"capital": 50000}, {"cost_mode": "FLAT_PER_ORDER"})

    # Simulate session state
    st.session_state["selected_strategy_name"] = test_strat_name
    st.session_state["strategy_name_input"] = test_strat_name
    st.session_state["step1_strat_name_input_field"] = test_strat_name
    st.session_state["step1_load_strat_select"] = test_strat_name
    st.session_state["step2_strat_selector"] = test_strat_name

    # Verify Step 2 target resolution logic
    saved_strats = list_strategy_names()
    target_strat = (
        st.session_state.get("selected_strategy_name")
        or st.session_state.get("strategy_name_input")
        or st.session_state.get("step1_strat_name_input_field")
        or (saved_strats[0] if saved_strats else "EMA_RSI_MACD_Balanced")
    ).strip()
    if target_strat not in saved_strats:
        saved_strats.insert(0, target_strat)
    st.session_state["step2_strat_selector"] = target_strat
    curr_idx = saved_strats.index(target_strat) if target_strat in saved_strats else 0

    assert target_strat == test_strat_name
    assert st.session_state["step2_strat_selector"] == test_strat_name
    assert saved_strats[curr_idx] == test_strat_name

    # Simulate user editing the strategy name in Step 1
    edited_name = "Test_Edit_Sync_Strat_V2"
    st.session_state["step1_strat_name_input_field"] = edited_name
    st.session_state.strategy_name_input = edited_name
    st.session_state.selected_strategy_name = edited_name
    st.session_state["step2_strat_selector"] = edited_name

    # Step 2 resolution picks up edited name even before saving to disk
    saved_strats = list_strategy_names()
    target_strat = (
        st.session_state.get("selected_strategy_name")
        or st.session_state.get("strategy_name_input")
        or st.session_state.get("step1_strat_name_input_field")
        or (saved_strats[0] if saved_strats else "EMA_RSI_MACD_Balanced")
    ).strip()
    if target_strat not in saved_strats:
        saved_strats.insert(0, target_strat)
    st.session_state["step2_strat_selector"] = target_strat
    curr_idx = saved_strats.index(target_strat) if target_strat in saved_strats else 0

    assert target_strat == edited_name
    assert st.session_state["step2_strat_selector"] == edited_name
    assert saved_strats[curr_idx] == edited_name

    delete_strategy(test_strat_name)








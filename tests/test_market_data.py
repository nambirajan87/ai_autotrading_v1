import pandas as pd
import pytest

from app.data.historical_data import load_csv
from app.data.market_data import (
    DataValidationError, find_gaps, normalize_ohlcv, validate_ohlcv,
)
from app.data.sample_data import generate_sample


def make_df(rows):
    return pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def test_normalize_sorts_dedups_and_sets_ist():
    raw = make_df([
        ("2026-08-03 09:20:00", 101, 102, 100, 101, 500),
        ("2026-08-03 09:15:00", 100, 101, 99, 100, 400),
        ("2026-08-03 09:15:00", 100, 101, 99, 100, 400),  # duplicate
    ])
    df = normalize_ohlcv(raw)
    assert len(df) == 2
    assert df["timestamp"].is_monotonic_increasing
    assert str(df["timestamp"].dt.tz) == "Asia/Kolkata"


def test_missing_column_rejected():
    with pytest.raises(DataValidationError):
        normalize_ohlcv(pd.DataFrame({"open": [1], "close": [1]}))


def test_impossible_candle_rejected():
    raw = make_df([("2026-08-03 09:15:00", 100, 98, 99, 100, 400)])  # high < low
    with pytest.raises(DataValidationError):
        validate_ohlcv(normalize_ohlcv(raw))


def test_gap_detection():
    raw = make_df([
        ("2026-08-03 09:15:00", 100, 101, 99, 100, 400),
        ("2026-08-03 09:30:00", 100, 101, 99, 100, 400),  # 09:20, 09:25 missing
    ])
    gaps = find_gaps(normalize_ohlcv(raw), 5)
    assert len(gaps) == 1


def test_sample_data_passes_validation(tmp_path):
    path = tmp_path / "sample.csv"
    generate_sample(days=3).to_csv(path, index=False)
    df = load_csv(str(path))
    assert len(df) == 3 * 75
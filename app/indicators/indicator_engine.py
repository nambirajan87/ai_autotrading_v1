import logging

import pandas as pd

from app.indicators.adx import adx
from app.indicators.atr import atr
from app.indicators.ema import ema
from app.indicators.macd import macd
from app.indicators.regime import detect_regime
from app.indicators.rsi import rsi
from app.indicators.volume import volume_features
from app.indicators.vwap import vwap

logger = logging.getLogger(__name__)


def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return a COPY of df with all indicator columns added.
    Periods are fixed defaults for now; Phase 4 moves them into config."""
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["rsi14"] = rsi(out["close"], 14)
    out = out.join(macd(out["close"]))
    out["vwap"] = vwap(out)
    out["atr14"] = atr(out, 14)
    out = out.join(adx(out, 14))
    out = out.join(volume_features(out, 20))
    if "regime" not in out.columns:
        out["regime"] = detect_regime(out)
    logger.info("Calculated indicators for %d candles", len(out))
    return out


if __name__ == "__main__":
    from app.data.historical_data import load_csv
    from app.monitoring.logging_setup import setup_logging

    setup_logging("INFO")
    result = calculate_all(load_csv("data/sample_RELIANCE_5m.csv"))
    print("shape:", result.shape)
    print("first fully-valid row index:", result.dropna().index[0])
    print(result.iloc[-1])
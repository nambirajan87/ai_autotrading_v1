import numpy as np
import pandas as pd

from app.data.sample_data import CANDLES_PER_DAY

# regime -> (drift per candle, volatility per candle)
REGIMES = {"UP": (0.0003, 0.0006), "DOWN": (-0.0003, 0.0006), "SIDEWAYS": (0.0, 0.0006)}
DEFAULT_PLAN = (("UP", 5), ("SIDEWAYS", 5), ("DOWN", 5), ("SIDEWAYS", 5))  # (regime, trading days)


def generate_regime_sample(plan=DEFAULT_PLAN, start_price: float = 2500.0, seed: int = 7) -> pd.DataFrame:
    """Synthetic candles with known trend segments. For plumbing checks ONLY."""
    rng = np.random.default_rng(seed)
    rows = []
    price = start_price
    day = pd.Timestamp("2026-08-03")

    for regime, n_days in plan:
        drift, vol = REGIMES[regime]
        made = 0
        while made < n_days:
            if day.weekday() < 5:
                t = day + pd.Timedelta(hours=9, minutes=15)
                for _ in range(CANDLES_PER_DAY):
                    o = price
                    c = o * (1 + rng.normal(drift, vol))
                    h = max(o, c) * (1 + abs(rng.normal(0, 0.0004)))
                    l = min(o, c) * (1 - abs(rng.normal(0, 0.0004)))
                    v = int(rng.integers(20_000, 120_000))
                    rows.append((t, round(o, 2), round(h, 2), round(l, 2), round(c, 2), v, regime))
                    price = c
                    t += pd.Timedelta(minutes=5)
                made += 1
            day += pd.Timedelta(days=1)

    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "regime"])
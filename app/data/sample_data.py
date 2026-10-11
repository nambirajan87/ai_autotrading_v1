import numpy as np
import pandas as pd

CANDLES_PER_DAY = 75  # 09:15 to 15:30 IST is 375 minutes = 75 five-minute candles


def generate_sample(days: int = 20, start_price: float = 2500.0, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)  # fixed seed -> same data every run
    rows = []
    price = start_price
    day = pd.Timestamp("2026-08-03")
    made = 0

    while made < days:
        if day.weekday() < 5:  # Mon-Fri (ignores exchange holidays)
            t = day + pd.Timedelta(hours=9, minutes=15)
            for _ in range(CANDLES_PER_DAY):
                o = price
                c = o * (1 + rng.normal(0, 0.0008))
                h = max(o, c) * (1 + abs(rng.normal(0, 0.0004)))
                l = min(o, c) * (1 - abs(rng.normal(0, 0.0004)))
                v = int(rng.integers(20_000, 120_000))
                rows.append((t, round(o, 2), round(h, 2), round(l, 2), round(c, 2), v))
                price = c
                t += pd.Timedelta(minutes=5)
            made += 1
        day += pd.Timedelta(days=1)

    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


if __name__ == "__main__":
    df = generate_sample()
    df.to_csv("data/sample_RELIANCE_5m.csv", index=False)
    print(f"Wrote {len(df)} synthetic candles to data/sample_RELIANCE_5m.csv")
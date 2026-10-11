import sys

import pandas as pd

from app.data.historical_data import load_csv
from app.data.market_data import find_gaps

path = sys.argv[1] if len(sys.argv) > 1 else "data/upstox_5m.csv"
df = load_csv(path)
day = df["timestamp"].dt.date
same_day = day == day.shift()

per_day = df.groupby(day).size()
print("trading days:", len(per_day))
print("candles per day:", per_day.value_counts().to_dict(), "(75 = full day)")
print("first candle times:", df.groupby(day)["timestamp"].min().dt.time.value_counts().to_dict())
print("last candle times: ", df.groupby(day)["timestamp"].max().dt.time.value_counts().to_dict())
print("intra-day gaps:", len(find_gaps(df)))
print("zero-volume candles:", int((df["volume"] == 0).sum()))

move = df["close"].pct_change().abs().where(same_day) * 100
print("\nlargest 5-min closes moves (%):")
print(pd.DataFrame({"timestamp": df["timestamp"], "move_%": move}).nlargest(5, "move_%").round(2).to_string(index=False))

gap = (df["open"] / df["close"].shift() - 1).abs().where(~same_day) * 100
print("\nlargest overnight gaps (%):")
print(pd.DataFrame({"timestamp": df["timestamp"], "gap_%": gap}).nlargest(5, "gap_%").round(2).to_string(index=False))
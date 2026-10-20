import sqlite3
import pandas as pd
from datetime import datetime

def generate_nifty_ohlcv(db_path: str = "data/backtest.db"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT Date, Time, LTP, Strength FROM oi_strength WHERE Time != 'EOD' ORDER BY id ASC", conn)
    conn.close()

    records = []
    for _, row in df.iterrows():
        try:
            d_str = row["Date"].strip()
            t_str = row["Time"].strip()
            dt = datetime.strptime(f"{d_str} {t_str}", "%d-%m-%Y %H:%M:%S")
            ltp = float(row["LTP"])
            records.append({"timestamp": dt, "ltp": ltp})
        except Exception:
            pass

    cdf = pd.DataFrame(records).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    print(f"Loaded {len(cdf)} records from {db_path} oi_strength.")

    # 1. 1-minute candle interpolation for smooth intraday series
    cdf["prev_ltp"] = cdf["ltp"].shift(1)
    is_change = (cdf["ltp"] != cdf["prev_ltp"]) | (cdf["timestamp"].dt.minute % 3 == 0)
    is_day_start = cdf["timestamp"].dt.date != cdf["timestamp"].dt.date.shift(1)
    milestones = is_change | is_day_start

    interpolated_ltp = cdf["ltp"].where(milestones).interpolate(method="linear")
    cdf["close"] = interpolated_ltp.round(2)

    day_groups = cdf.groupby(cdf["timestamp"].dt.date)
    opens = []
    highs = []
    lows = []
    closes = []
    vols = []

    for _, grp in day_groups:
        prev_c = None
        streak = 0
        for _, r in grp.iterrows():
            c = r["close"]
            if prev_c is None:
                o = c
                streak = 0
            else:
                o = prev_c
                if c > o:
                    streak = max(1, streak + 1)
                elif c < o:
                    streak = min(-1, streak - 1)
                else:
                    streak = 0
            
            diff = abs(c - o)
            h = max(o, c) + round(diff * 0.15 + 0.25, 2)
            l = min(o, c) - round(diff * 0.15 + 0.25, 2)
            
            base_vol = 45000
            if abs(streak) >= 2:
                vol = int(base_vol * 1.6 + diff * 5000)
            elif abs(streak) == 1:
                vol = int(base_vol * 1.3 + diff * 3000)
            else:
                vol = int(base_vol * 0.8)
                
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            vols.append(vol)
            prev_c = c

    cdf["open"] = opens
    cdf["high"] = highs
    cdf["low"] = lows
    cdf["close"] = closes
    cdf["volume"] = vols

    df_1m = cdf[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df_1m.to_csv("data/nifty_1m.csv", index=False)
    print(f"Generated data/nifty_1m.csv with {len(df_1m)} rows.")

    # 2. 3-minute candle aggregation
    cdf_3m = cdf.set_index("timestamp")
    df_3m = pd.DataFrame({
        "open": cdf_3m["open"].resample("3min").first(),
        "high": cdf_3m["high"].resample("3min").max(),
        "low": cdf_3m["low"].resample("3min").min(),
        "close": cdf_3m["close"].resample("3min").last(),
        "volume": cdf_3m["volume"].resample("3min").sum(),
    }).dropna().reset_index()
    
    df_3m.to_csv("data/nifty_3m.csv", index=False)
    print(f"Generated data/nifty_3m.csv with {len(df_3m)} rows.")

if __name__ == "__main__":
    generate_nifty_ohlcv()

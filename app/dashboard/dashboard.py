import json
import sqlite3

import pandas as pd
import streamlit as st

DB_PATH = "data/paper_trading.db"

st.set_page_config(page_title="AI Auto-Trading Dashboard", layout="wide")

from app.monitoring.audit_logger import record_session_visit
record_session_visit()



@st.cache_data(ttl=5)   # re-read the DB at most every 5 seconds
def load(sql: str, params: tuple = ()) -> pd.DataFrame:
    # read-only connection: the dashboard can never modify the audit trail
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def max_drawdown(equity: pd.Series) -> float:
    return float((equity.cummax() - equity).max())


st.title("AI Auto-Trading: Paper Trading Dashboard")
st.caption("Read-only view of the audit database. Synthetic data tests machinery, not profitability.")

try:
    runs = load("SELECT id, started_at, mode, symbol, strategy_version, config_json "
                "FROM strategy_runs ORDER BY id DESC")
except Exception as exc:
    st.error(f"Cannot open {DB_PATH}: {exc}\n\nRun `python -m app.execution.paper_trader` first.")
    st.stop()

if runs.empty:
    st.warning("No runs found yet.")
    st.stop()

# ---------- sidebar ----------
run_id = int(st.sidebar.selectbox(
    "Run", runs["id"].tolist(),
    format_func=lambda i: f"run {i}  ({runs.loc[runs.id == i, 'started_at'].iloc[0]})"))
run = runs[runs.id == run_id].iloc[0]
cfg = json.loads(run["config_json"]) if run["config_json"] else {}
capital = cfg.get("risk", {}).get("capital", 100_000.0)

st.sidebar.markdown(f"**Mode:** {run['mode']}  \n**Symbol:** {run['symbol']}  \n"
                    f"**Strategy:** {run['strategy_version']}")
st.sidebar.caption("Refresh the page to reload. Data is cached for 5 seconds.")

trades = load("SELECT * FROM trades WHERE run_id=? ORDER BY id", (run_id,))
signals = load("SELECT timestamp, signal, score FROM signals WHERE run_id=?", (run_id,))
risk = load("SELECT timestamp, approved, reason FROM risk_events WHERE run_id=?", (run_id,))
orders = load("SELECT status, COUNT(*) AS n FROM orders WHERE run_id=? GROUP BY status", (run_id,))
events = load("SELECT timestamp, level, message FROM system_events WHERE run_id=? ORDER BY id DESC",
              (run_id,))

# ---------- KPIs ----------
if trades.empty:
    st.info("This run has no closed trades.")
else:
    net = trades["net_pnl"]
    equity = pd.concat([pd.Series([capital]), capital + net.cumsum()], ignore_index=True)
    wins = net[net > 0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Trades", len(trades))
    c2.metric("Net P&L", f"{net.sum():,.0f}", f"{net.sum() / capital * 100:.2f}%")
    c3.metric("Gross P&L", f"{trades['gross_pnl'].sum():,.0f}")
    c4.metric("Costs", f"{trades['costs'].sum():,.0f}")
    c5.metric("Win rate", f"{len(wins) / len(net) * 100:.1f}%")
    c6.metric("Max drawdown", f"{max_drawdown(equity):,.0f}")
    st.caption(f"Final equity: {equity.iloc[-1]:,.2f}  |  Starting capital: {capital:,.0f}")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Equity curve (after each closed trade)")
        st.line_chart(equity.rename("equity"))
    with c2:
        st.subheader("Daily net P&L")
        daily = trades.assign(day=pd.to_datetime(trades["exit_time"]).dt.date) \
                      .groupby("day")["net_pnl"].sum()
        st.bar_chart(daily)
    with c3:
        st.subheader("Monthly net P&L")
        dt = pd.to_datetime(trades["exit_time"].astype(str).str.slice(0, 10), errors="coerce")
        m_df = trades.assign(
            month_key=dt.dt.strftime("%Y-%m"),
            month_label=dt.dt.strftime("%b %Y")
        ).dropna(subset=["month_key"])
        if not m_df.empty:
            monthly = m_df.groupby(["month_key", "month_label"])["net_pnl"].sum().reset_index()
            monthly = monthly.sort_values("month_key").set_index("month_label")["net_pnl"]
            st.bar_chart(monthly)

    left, right = st.columns(2)
    with left:
        st.subheader("Exit reasons")
        by_exit = trades.groupby("exit_reason")["net_pnl"].agg(
            trades="count", net_pnl="sum", avg_trade="mean").round(2)
        st.dataframe(by_exit, width="stretch")
    with right:
        st.subheader("By side")
        by_side = trades.groupby("side")["net_pnl"].agg(
            trades="count", net_pnl="sum", avg_trade="mean").round(2)
        st.dataframe(by_side, width="stretch")
        st.caption("Treat buckets under ~30 trades as noise.")

    st.subheader("Recent trades")
    cols = ["id", "side", "score", "entry_time", "exit_time", "quantity", "entry_price",
            "exit_price", "exit_reason", "gross_pnl", "costs", "net_pnl"]
    # st.dataframe(trades[cols].tail(25).iloc[::-1], width="stretch", hide_index=True)
    show_n = st.number_input("Rows to show (newest first)", min_value=5,
                             max_value=max(len(trades), 5), value=min(25, len(trades)), step=5)
    st.dataframe(trades[cols].tail(int(show_n)).iloc[::-1],
                 width="stretch", hide_index=True)

# ---------- signals and risk ----------
st.header("Signals and risk")
a, b, c = st.columns(3)
with a:
    st.subheader("Signals evaluated")
    if signals.empty:
        st.write("None")
    else:
        st.bar_chart(signals["signal"].value_counts())
with b:
    st.subheader("Risk decisions")
    if risk.empty:
        st.write("None")
    else:
        st.write(f"Approved: **{int(risk['approved'].sum())}** | "
                 f"Rejected: **{int((risk['approved'] == 0).sum())}**")
        rejected = risk[risk["approved"] == 0]["reason"].str.split(r" \(").str[0].value_counts()
        st.dataframe(rejected.rename("count"), width="stretch")
with c:
    st.subheader("Orders by status")
    st.dataframe(orders, width="stretch", hide_index=True)

# ---------- events and placeholders ----------
st.header("System events")
if events.empty:
    st.success("No system events logged for this run.")
else:
    st.dataframe(events.head(50), width="stretch", hide_index=True)

st.header("AI analysis")
st.info("AI Market Analysis and Trade RCA arrive in Phase 9.")
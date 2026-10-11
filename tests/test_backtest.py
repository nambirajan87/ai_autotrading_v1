import dataclasses

import pandas as pd
import pytest

from app.backtest.costs import apply_slippage, leg_cost
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.metrics import compute_metrics
from app.config.cost_config import CostConfig
from app.config.risk_config import RiskConfig
from app.config.strategy_config import StrategyConfig

ZERO = CostConfig.zero()


def candle(ts, o, h, l, c, kind="neutral", atr=10.0):
    """kind: 'bull' -> score +8, 'bear' -> score -8, 'neutral' -> score 0 (HOLD)."""
    row = dict(timestamp=pd.Timestamp(ts), open=o, high=h, low=l, close=c,
               volume=1000, atr14=atr)
    if kind == "bull":
        row.update(ema20=c + 5, ema50=c - 5, rsi14=60.0, macd=1.0, macd_signal=0.0,
                   macd_hist=1.0, vwap=c - 10, adx=25.0, plus_di=30.0, minus_di=15.0,
                   volume_ratio=1.5)
    elif kind == "bear":
        row.update(ema20=c - 5, ema50=c + 5, rsi14=40.0, macd=-1.0, macd_signal=0.0,
                   macd_hist=-1.0, vwap=c + 10, adx=25.0, plus_di=15.0, minus_di=30.0,
                   volume_ratio=1.5)
    else:
        row.update(ema20=c, ema50=c, rsi14=50.0, macd=0.0, macd_signal=0.0,
                   macd_hist=0.0, vwap=c, adx=10.0, plus_di=10.0, minus_di=10.0,
                   volume_ratio=1.0)
    return row


def run(rows, cost=ZERO, allow_short=True, bt_cfg=None):
    cfg = bt_cfg or BacktestConfig(allow_short=allow_short)
    trades, _ = run_backtest(pd.DataFrame(rows), "TEST", StrategyConfig(), RiskConfig(),
                             cost, cfg)
    return trades


def test_leg_cost_buy_and_sell():
    cfg = CostConfig()
    # turnover 100,000. BUY: 20 + 2.97 + 0.10 + GST 4.1526 + stamp 3.00 = 30.2226
    assert leg_cost("BUY", 2500.0, 40, cfg) == pytest.approx(30.22, abs=0.01)
    # SELL: 20 + STT 25 + 2.97 + 0.10 + GST 4.1526 = 52.2226
    assert leg_cost("SELL", 2500.0, 40, cfg) == pytest.approx(52.22, abs=0.01)
    assert apply_slippage(2500.0, "BUY", 0.001) == pytest.approx(2502.5)
    assert apply_slippage(2500.0, "SELL", 0.001) == pytest.approx(2497.5)


def test_long_hits_target_exact_pnl():
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2500, 2532, 2499, 2528),
             candle("2026-08-03 10:10", 2528, 2530, 2526, 2528)])
    assert len(t) == 1
    r = t.iloc[0]
    assert (r["side"], r["quantity"], r["exit_reason"]) == ("LONG", 40, "TARGET")
    assert (r["entry_price"], r["exit_price"]) == (2500.0, 2530.0)
    assert r["gross_pnl"] == 1200.0 and r["net_pnl"] == 1200.0   # (2530-2500) x 40


def test_stop_wins_when_both_hit_same_candle():
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2500, 2535, 2480, 2500),
             candle("2026-08-03 10:10", 2500, 2501, 2499, 2500)])
    r = t.iloc[0]
    assert r["exit_reason"] == "STOP"
    assert r["net_pnl"] == -600.0                                 # (2485-2500) x 40


def test_gap_through_stop_fills_at_open_and_loses_more_than_planned():
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2500, 2505, 2495, 2500),
             candle("2026-08-03 10:10", 2470, 2475, 2465, 2470)])
    r = t.iloc[0]
    assert r["exit_reason"] == "STOP_GAP"
    assert r["exit_price"] == 2470.0
    assert r["net_pnl"] == -1200.0                                # planned risk was only 600


def test_entry_slippage_hurts_long():
    cost = dataclasses.replace(ZERO, slippage_pct=0.001)
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2500, 2532, 2499, 2528),
             candle("2026-08-03 10:10", 2528, 2530, 2526, 2528)], cost=cost)
    r = t.iloc[0]
    assert r["entry_price"] == pytest.approx(2502.5)
    assert r["gross_pnl"] == pytest.approx(1100.0)                # (2530-2502.5) x 40; target has no slippage
    assert r["slippage_cost"] == pytest.approx(100.0)             # 2.5 x 40


def test_fill_is_next_candle_open_not_signal_candle_close():
    cfg = BacktestConfig(allow_short=True, execution_mode="NEXT_BAR_OPEN")
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2501, 2502, 2499, 2500),
             candle("2026-08-03 10:10", 2500, 2505, 2495, 2500)], bt_cfg=cfg)
    r = t.iloc[0]
    assert r["signal_time"] == pd.Timestamp("2026-08-03 10:00")
    assert r["entry_time"] == pd.Timestamp("2026-08-03 10:05")    # next candle, not the same one
    assert r["entry_price"] == 2501.0                             # that candle's OPEN
    assert r["exit_reason"] == "END_OF_DATA"


def test_fill_is_signal_candle_close():
    # Default execution_mode is SIGNAL_BAR_CLOSE
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2501, 2502, 2499, 2500),
             candle("2026-08-03 10:10", 2500, 2505, 2495, 2500)])
    r = t.iloc[0]
    assert r["signal_time"] == pd.Timestamp("2026-08-03 10:00")
    assert r["entry_time"] == pd.Timestamp("2026-08-03 10:00")    # same candle close
    assert r["entry_price"] == 2500.0                             # that candle's CLOSE
    assert r["exit_reason"] == "END_OF_DATA"


def test_square_off_exits_at_open():
    rows = [candle("2026-08-03 14:55", 2500, 2502, 2498, 2500, "bull")]
    for ts in ["15:00", "15:05", "15:10"]:
        rows.append(candle(f"2026-08-03 {ts}", 2500, 2505, 2495, 2500))
    rows.append(candle("2026-08-03 15:15", 2502, 2505, 2498, 2502))
    r = run(rows).iloc[0]
    assert r["exit_reason"] == "SQUARE_OFF"
    assert r["exit_price"] == 2502.0
    assert r["net_pnl"] == 80.0                                   # (2502-2500) x 40


def test_short_hits_target():
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bear"),
             candle("2026-08-03 10:05", 2500, 2501, 2470, 2480),
             candle("2026-08-03 10:10", 2480, 2481, 2479, 2480)])
    r = t.iloc[0]
    assert (r["side"], r["exit_reason"], r["exit_price"]) == ("SHORT", "TARGET", 2470.0)
    assert r["net_pnl"] == 1200.0                                 # (2500-2470) x 40


def test_short_disabled_by_config():
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bear"),
             candle("2026-08-03 10:05", 2500, 2501, 2470, 2480)], allow_short=False)
    assert len(t) == 0


def test_costs_reduce_net_pnl():
    cfg = dataclasses.replace(CostConfig(), slippage_pct=0.0)
    t = run([candle("2026-08-03 10:00", 2500, 2502, 2498, 2500, "bull"),
             candle("2026-08-03 10:05", 2500, 2532, 2499, 2528),
             candle("2026-08-03 10:10", 2528, 2530, 2526, 2528)], cost=cfg)
    r = t.iloc[0]
    expected = leg_cost("BUY", 2500.0, 40, cfg) + leg_cost("SELL", 2530.0, 40, cfg)
    assert r["costs"] > 0
    assert r["costs"] == pytest.approx(expected, abs=0.011)
    assert r["net_pnl"] == pytest.approx(1200.0 - expected, abs=0.011)


def make_trades(pnls):
    days = pd.date_range("2026-08-03", periods=len(pnls), freq="D")
    df = pd.DataFrame({"net_pnl": pnls, "gross_pnl": pnls, "costs": 0.0,
                       "slippage_cost": 0.0, "exit_time": days})
    return df, [d.date() for d in days]


def test_metrics_known_values():
    trades, days = make_trades([100.0, -50.0, 200.0, -50.0, -50.0])
    m = compute_metrics(trades, 1000.0, days)
    assert m["num_trades"] == 5
    assert m["net_pnl"] == 150.0
    assert m["return_pct"] == 15.0
    assert m["win_rate_pct"] == 40.0 and m["loss_rate_pct"] == 60.0
    assert m["profit_factor"] == 2.0                              # 300 / 150
    assert (m["avg_win"], m["avg_loss"], m["risk_reward"]) == (150.0, -50.0, 3.0)
    assert m["max_drawdown"] == 100.0                             # peak 1250 -> 1150
    assert m["max_consecutive_losses"] == 2


def test_metrics_with_no_trades():
    m = compute_metrics(pd.DataFrame(columns=["net_pnl"]), 1000.0, [])
    assert m["num_trades"] == 0 and m["net_pnl"] == 0.0
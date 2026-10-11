import dataclasses

import pandas as pd
import pytest

from app.backtest.costs import leg_cost
from app.config.cost_config import CostConfig
from app.execution.broker_interface import Broker
from app.execution.paper_broker import PaperBroker

ZERO = CostConfig.zero()


def candle(o, h, l, c, v=1000, ts="2026-08-03 10:00"):
    return pd.Series({"timestamp": pd.Timestamp(ts), "open": o, "high": h,
                      "low": l, "close": c, "volume": v})


def new_broker(capital=100_000.0, cost=ZERO, participation=None):
    return PaperBroker(capital, cost, max_volume_participation=participation)


def test_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Broker()


def test_market_round_trip():
    b = new_broker()
    o = b.place_order("X", "BUY", 10)
    assert o.status == "NEW" and b.get_positions() == []     # nothing fills until a candle arrives
    b.on_candle("X", candle(100, 101, 99, 100))
    assert b.get_order_status(o.order_id).status == "FILLED"
    pos = b.get_positions()[0]
    assert (pos.quantity, pos.avg_price) == (10, 100.0)
    assert b.get_balance()["cash"] == 99_000.0

    b.place_order("X", "SELL", 10)
    b.on_candle("X", candle(110, 111, 109, 110))
    assert b.get_positions() == []
    bal = b.get_balance()
    assert bal["cash"] == 100_100.0
    assert bal["realized_pnl"] == 100.0
    assert bal["equity"] == 100_100.0
    assert len(b.executions) == 2


def test_market_order_slippage():
    b = new_broker(cost=dataclasses.replace(ZERO, slippage_pct=0.01))   # 1%
    o = b.place_order("X", "BUY", 10)
    b.on_candle("X", candle(100, 101, 99, 100))
    assert b.get_order_status(o.order_id).avg_fill_price == pytest.approx(101.0)
    assert b.get_balance()["cash"] == pytest.approx(98_990.0)           # 100000 - 1010


def test_limit_buy_fill_rules():
    b = new_broker()
    o = b.place_order("X", "BUY", 10, "LIMIT", limit_price=99.0)
    b.on_candle("X", candle(100, 101, 99.5, 100))            # low never reaches 99
    assert b.get_order_status(o.order_id).status == "NEW"
    b.on_candle("X", candle(100, 101, 98.5, 100))            # touches 99
    assert b.get_order_status(o.order_id).avg_fill_price == 99.0

    g = new_broker()
    o2 = g.place_order("X", "BUY", 10, "LIMIT", limit_price=99.0)
    g.on_candle("X", candle(97, 98, 96, 97))                 # gapped below the limit
    assert g.get_order_status(o2.order_id).avg_fill_price == 97.0   # better price at the open


def test_limit_sell_fill_rules():
    b = new_broker()
    o = b.place_order("X", "SELL", 10, "LIMIT", limit_price=105.0)
    b.on_candle("X", candle(100, 104, 99, 100))
    assert b.get_order_status(o.order_id).status == "NEW"
    b.on_candle("X", candle(100, 106, 99, 100))
    assert b.get_order_status(o.order_id).avg_fill_price == 105.0

    g = new_broker()
    o2 = g.place_order("X", "SELL", 10, "LIMIT", limit_price=105.0)
    g.on_candle("X", candle(107, 108, 106, 107))             # gapped above the limit
    assert g.get_order_status(o2.order_id).avg_fill_price == 107.0


def test_partial_fills_across_candles():
    b = new_broker(participation=0.1)                        # volume 100 -> 10 shares per candle
    o = b.place_order("X", "BUY", 25)
    b.on_candle("X", candle(100, 101, 99, 100, v=100))
    s = b.get_order_status(o.order_id)
    assert (s.status, s.filled_quantity) == ("PARTIALLY_FILLED", 10)
    b.on_candle("X", candle(102, 103, 101, 102, v=100))
    b.on_candle("X", candle(104, 105, 103, 104, v=100))
    s = b.get_order_status(o.order_id)
    assert (s.status, s.filled_quantity) == ("FILLED", 25)
    assert s.avg_fill_price == pytest.approx(101.6)          # (10*100 + 10*102 + 5*104) / 25
    assert b.get_balance()["cash"] == pytest.approx(97_460.0)
    assert len(b.executions) == 3


def test_cancel_order():
    b = new_broker()
    o = b.place_order("X", "BUY", 10, "LIMIT", limit_price=90.0)
    assert b.cancel_order(o.order_id) is True
    assert b.get_order_status(o.order_id).status == "CANCELLED"
    b.on_candle("X", candle(80, 81, 79, 80))                 # would have filled
    assert b.get_positions() == []
    assert b.cancel_order(o.order_id) is False               # already cancelled
    assert b.cancel_order("nope") is False                   # unknown id

    f = b.place_order("X", "BUY", 1)
    b.on_candle("X", candle(100, 101, 99, 100))
    assert b.cancel_order(f.order_id) is False               # already filled


def test_rejections():
    b = new_broker()
    assert b.place_order("X", "BUY", 0).status == "REJECTED"
    assert b.place_order("X", "HOLD", 10).status == "REJECTED"
    assert b.place_order("X", "BUY", 10, "LIMIT").status == "REJECTED"   # no limit price

    poor = new_broker(capital=1_000.0)
    o = poor.place_order("X", "BUY", 100)                    # needs 10,000
    poor.on_candle("X", candle(100, 101, 99, 100))
    s = poor.get_order_status(o.order_id)
    assert s.status == "REJECTED" and s.reject_reason == "insufficient cash"
    assert poor.get_balance()["cash"] == 1_000.0 and poor.get_positions() == []


def test_duplicate_client_order_id_is_ignored():
    b = new_broker()
    first = b.place_order("X", "BUY", 10, client_order_id="sig-001")
    second = b.place_order("X", "BUY", 10, client_order_id="sig-001")
    assert first.order_id == second.order_id
    b.on_candle("X", candle(100, 101, 99, 100))
    assert b.get_positions()[0].quantity == 10               # bought once, not twice


def test_short_and_cover():
    b = new_broker()
    b.place_order("X", "SELL", 10)
    b.on_candle("X", candle(100, 101, 99, 100))
    assert b.get_positions()[0].quantity == -10
    assert b.get_balance()["cash"] == 101_000.0              # sale proceeds (no margin modelled)
    b.place_order("X", "BUY", 10)
    b.on_candle("X", candle(90, 91, 89, 90))
    assert b.get_positions() == []
    bal = b.get_balance()
    assert bal["realized_pnl"] == 100.0                      # (100 - 90) x 10
    assert bal["cash"] == 100_100.0


def test_flip_position_and_equity_identity():
    b = new_broker()
    b.place_order("X", "BUY", 10)
    b.on_candle("X", candle(100, 101, 99, 100))
    b.place_order("X", "SELL", 15)                           # closes 10, opens short 5
    b.on_candle("X", candle(110, 111, 109, 110))
    pos = b.get_positions()[0]
    assert (pos.quantity, pos.avg_price) == (-5, 110.0)
    bal = b.get_balance()
    assert bal["realized_pnl"] == 100.0                      # 10 x (110 - 100)
    assert bal["cash"] == 100_650.0                          # 100000 - 1000 + 1650
    # equity = initial capital + realized - charges + unrealized
    assert bal["equity"] == 100_000.0 + bal["realized_pnl"] - bal["charges"] + bal["unrealized_pnl"]


def test_unrealized_pnl_uses_last_close():
    b = new_broker()
    b.place_order("X", "BUY", 10)
    b.on_candle("X", candle(100, 106, 99, 105))              # fills at 100, closes at 105
    bal = b.get_balance()
    assert bal["unrealized_pnl"] == 50.0                     # 10 x (105 - 100)
    assert bal["equity"] == 100_050.0


def test_charges_are_applied():
    cfg = dataclasses.replace(CostConfig(), slippage_pct=0.0)
    b = new_broker(capital=200_000.0, cost=cfg)
    b.place_order("X", "BUY", 40)
    b.on_candle("X", candle(2500, 2501, 2499, 2500))
    assert b.get_balance()["cash"] == pytest.approx(200_000 - 100_000 - 30.22, abs=0.01)
    b.place_order("X", "SELL", 40)
    b.on_candle("X", candle(2500, 2501, 2499, 2500))
    bal = b.get_balance()
    expected = leg_cost("BUY", 2500.0, 40, cfg) + leg_cost("SELL", 2500.0, 40, cfg)   # ~82.44
    assert bal["realized_pnl"] == 0.0
    assert bal["charges"] == pytest.approx(expected, abs=0.011)
    assert bal["equity"] == pytest.approx(200_000 - expected, abs=0.02)


def test_unknown_order_id_raises():
    with pytest.raises(KeyError):
        new_broker().get_order_status("PAPER-999999")
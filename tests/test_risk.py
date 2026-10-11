import dataclasses

import pandas as pd
import pytest

from app.config.risk_config import RiskConfig
from app.config.settings import Settings
from app.risk.exposure import OpenPosition
from app.risk.position_sizing import calculate_quantity
from app.risk.risk_engine import PortfolioState, RiskEngine, validate_order
from app.strategy.signal_engine import Signal

CFG = RiskConfig()   # capital 100,000 | risk 1% | daily loss 2,000 | 3 positions | no leverage


def make_signal(side="LONG", close=2500.0, atr=10.0, ts="2026-08-03 10:00") -> Signal:
    return Signal(symbol="RELIANCE", timestamp=pd.Timestamp(ts), signal=side,
                  score=0,  # the risk engine ignores the score
                  max_score=8, confidence=0.0, strategy_version="strategy_v1",
                  indicators={"close": close, "atr": atr})


def test_normal_long_trade_approved():
    d = RiskEngine(CFG).evaluate(make_signal(), PortfolioState())
    assert d.approved
    assert d.quantity == 40          # exposure cap (100000/2500) beats risk cap (66)
    assert d.stop_loss == 2485.0
    assert d.target == 2530.0
    assert d.risk_amount == 600.0


def test_short_trade_levels():
    d = RiskEngine(CFG).evaluate(make_signal("SHORT"), PortfolioState())
    assert d.approved
    assert d.stop_loss == 2515.0
    assert d.target == 2470.0


def test_hold_signal_rejected():
    d = RiskEngine(CFG).evaluate(make_signal("HOLD"), PortfolioState())
    assert not d.approved


def test_daily_loss_latches_trading_off():
    engine = RiskEngine(CFG)
    assert not engine.evaluate(make_signal(), PortfolioState(daily_pnl=-2000.0)).approved
    assert engine.trading_enabled is False
    # even if P&L "recovers", trading stays off until an explicit reset
    again = engine.evaluate(make_signal(ts="2026-08-03 10:05"), PortfolioState(daily_pnl=0.0))
    assert not again.approved
    engine.reset_for_new_day()
    assert engine.evaluate(make_signal(ts="2026-08-03 10:10"), PortfolioState()).approved


def test_max_open_positions():
    state = PortfolioState(open_positions=[OpenPosition("A", 1, 10.0),
                                           OpenPosition("B", 1, 10.0),
                                           OpenPosition("C", 1, 10.0)])
    d = RiskEngine(CFG).evaluate(make_signal(), state)
    assert not d.approved
    assert "max open positions" in d.rejection_reason


def test_same_symbol_already_open():
    state = PortfolioState(open_positions=[OpenPosition("RELIANCE", 1, 10.0)])
    assert not RiskEngine(CFG).evaluate(make_signal(), state).approved


def test_exposure_exhausted_rejects():
    state = PortfolioState(open_positions=[OpenPosition("TCS", 99, 1000.0)])  # 99,000 used
    d = RiskEngine(CFG).evaluate(make_signal(), state)                        # room 1,000 < 1 share
    assert not d.approved
    assert "quantity is 0" in d.rejection_reason


def test_missing_or_zero_atr_rejected():
    engine = RiskEngine(CFG)
    assert not engine.evaluate(make_signal(atr=None), PortfolioState()).approved
    assert not engine.evaluate(make_signal(atr=0.0), PortfolioState()).approved


def test_outside_entry_hours_rejected():
    engine = RiskEngine(CFG)
    assert not engine.evaluate(make_signal(ts="2026-08-03 15:10"), PortfolioState()).approved
    d = engine.evaluate(make_signal(ts="2026-08-03 09:15"), PortfolioState())
    assert not d.approved
    assert "entry hours" in d.rejection_reason


def test_consecutive_losses_rejected():
    d = RiskEngine(CFG).evaluate(make_signal(), PortfolioState(consecutive_losses=3))
    assert not d.approved


def test_kill_switch_is_manual_only():
    engine = RiskEngine(CFG)
    engine.activate_kill_switch("manual test")
    assert not engine.evaluate(make_signal(), PortfolioState()).approved
    engine.reset_for_new_day()
    assert engine.kill_switch is True            # new day does NOT clear it
    engine.release_kill_switch()
    assert engine.evaluate(make_signal(), PortfolioState()).approved


def test_duplicate_signal_rejected():
    engine = RiskEngine(CFG)
    sig = make_signal()
    assert engine.evaluate(sig, PortfolioState()).approved
    second = engine.evaluate(sig, PortfolioState())
    assert not second.approved
    assert "duplicate" in second.rejection_reason


def test_quantity_never_exceeds_risk_budget():
    for entry, stop in [(2500, 2485), (500, 485), (99.5, 97.0), (3000.0, 3010.5)]:
        qty = calculate_quantity(100_000, 0.01, entry, stop)
        assert qty * abs(entry - stop) <= 1_000 + 1e-9


def test_lot_size_rounding():
    assert calculate_quantity(100_000, 0.01, 500, 490) == 100
    assert calculate_quantity(100_000, 0.01, 500, 485, lot_size=25) == 50   # 66 -> 50


def test_zero_risk_per_unit_raises():
    with pytest.raises(ValueError):
        calculate_quantity(100_000, 0.01, 500, 500)


def test_square_off_time():
    engine = RiskEngine(CFG)
    assert engine.should_square_off(pd.Timestamp("2026-08-03 15:15"))
    assert not engine.should_square_off(pd.Timestamp("2026-08-03 15:10"))


def test_config_from_settings_and_validation():
    s = Settings(trading_mode="PAPER", log_level="INFO", max_daily_loss=1500.0,
                 max_risk_per_trade=0.005, max_positions=2, database_url="")
    cfg = RiskConfig.from_settings(s, capital=200_000)
    assert (cfg.capital, cfg.max_daily_loss, cfg.risk_per_trade_pct, cfg.max_open_positions) \
        == (200_000, 1500.0, 0.005, 2)
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=1.0)       # typo guard


def test_validate_order_catches_tampering():
    d = RiskEngine(CFG).evaluate(make_signal(), PortfolioState())
    assert validate_order(d, CFG) is None
    assert validate_order(dataclasses.replace(d, quantity=10_000), CFG) is not None
    assert validate_order(dataclasses.replace(d, stop_loss=2600.0), CFG) is not None

def test_new_validations():
    for bad in [dict(tick_size=0), dict(atr_stop_multiplier=0),
                dict(reward_risk_ratio=-1), dict(max_consecutive_losses=0)]:
        with pytest.raises(ValueError):
            RiskConfig(**bad)

def test_available_cash_limits_quantity():
    d = RiskEngine(CFG).evaluate(make_signal(), PortfolioState(available_cash=50_000.0))
    assert d.approved
    assert d.quantity == 19      # 50,000 x 0.99 = 49,500 -> floor(49,500 / 2,500)
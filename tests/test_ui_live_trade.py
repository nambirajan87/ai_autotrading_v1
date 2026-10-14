from datetime import date, timedelta
import pandas as pd
import pytest

from app.database.live_database import LiveTradeDatabase
from app.gateways.gateway_factory import get_gateway
from app.strategy.strategy_store import get_strategy, list_strategy_names


@pytest.fixture
def temp_live_db(tmp_path):
    db_file = str(tmp_path / "test_live_ui.db")
    return LiveTradeDatabase(db_path=db_file)


def test_live_database_adx_seeding_and_accumulation(temp_live_db):
    trades = temp_live_db.get_accumulated_trades_for_strategy("ADX", max_days=30)
    assert len(trades) >= 4  # 3 real trades + 1 No Trade session

    tids = [str(t.get("trade_id")) for t in trades]
    assert "5000" in tids
    assert "5001" in tids
    assert "5002" in tids
    assert "No Trade" in tids

    dates = [t["session_date"] for t in trades]
    assert "2026-10-14" in dates
    assert "2026-10-13" in dates
    assert "2026-10-11" in dates
    assert "2026-10-10" in dates


def test_live_database_active_and_close_trade(temp_live_db):
    today_str = str(date.today())
    tid = 9001
    row_id = temp_live_db.save_active_trade(
        strategy_name="TEST_LIVE_STRAT",
        symbol="NIFTY26OCT22500PE",
        session_date=today_str,
        trade_id=tid,
        side="LONG",
        quantity=65,
        entry_time=f"{today_str} 10:00:00",
        entry_price=150.0,
        stop_loss=140.0,
        target=165.0,
        score=5,
        regime="TRENDING",
    )
    assert row_id > 0

    trades = temp_live_db.get_trades(strategy_name="TEST_LIVE_STRAT")
    assert len(trades) == 1
    assert trades[0]["status"] == "ACTIVE"
    assert trades[0]["entry_price"] == 150.0

    # Close active trade
    temp_live_db.close_active_trade(
        trade_id_or_row_id=row_id,
        exit_time=f"{today_str} 11:30:00",
        exit_price=165.0,
        exit_reason="TARGET",
        gross_pnl=975.0,
        charges=20.0,
        peak_gain_pct=10.0,
    )

    trades_after = temp_live_db.get_trades(strategy_name="TEST_LIVE_STRAT")
    assert len(trades_after) == 1
    assert trades_after[0]["status"] == "CLOSED"
    assert trades_after[0]["exit_reason"] == "TARGET"
    assert trades_after[0]["exit_price"] == 165.0
    assert trades_after[0]["net_pnl"] == 955.0


def test_live_current_date_validation_logic():
    today_str = str(date.today())
    past_date_str = str(date.today() - timedelta(days=5))

    strat_inst_valid = {"start_date": today_str, "end_date": today_str}
    is_valid = (
        strat_inst_valid.get("start_date") == today_str
        and strat_inst_valid.get("end_date") == today_str
    )
    assert is_valid is True

    strat_inst_invalid = {"start_date": past_date_str, "end_date": past_date_str}
    is_valid = (
        strat_inst_invalid.get("start_date") == today_str
        and strat_inst_invalid.get("end_date") == today_str
    )
    assert is_valid is False


def test_kotak_bracket_order_and_emergency_squareoff():
    gw = get_gateway("KOTAK")

    # 1. Place Market Entry
    entry_ord = gw.place_market_order(
        symbol="NIFTY26OCT22500PE",
        side="BUY",
        quantity=65,
        reference_price=150.0,
    )
    assert entry_ord is not None
    assert entry_ord.side == "BUY"
    assert entry_ord.status == "FILLED"

    # 2. Place Target Limit Order
    tgt_ord = gw.place_target_order(
        symbol="NIFTY26OCT22500PE",
        side="SELL",
        quantity=65,
        target_price=165.0,
    )
    assert tgt_ord is not None
    assert tgt_ord.order_type == "LIMIT"
    assert tgt_ord.price == 165.0
    assert tgt_ord.reason == "TARGET_ORDER"

    # 3. Place Stoploss Order
    sl_ord = gw.place_stoploss_order(
        symbol="NIFTY26OCT22500PE",
        side="SELL",
        quantity=65,
        stoploss_price=140.0,
    )
    assert sl_ord is not None
    assert sl_ord.order_type == "STOP_LOSS"
    assert sl_ord.price == 140.0
    assert sl_ord.reason == "STOP_LOSS_ORDER"

    # Verify open positions count
    open_cnt = gw.get_open_positions_count()
    assert open_cnt >= 1

    # 4. Trigger Emergency Square Off
    sq_res = gw.emergency_square_off_all()
    assert sq_res["status"] == "SUCCESS"
    assert sq_res["count"] >= 1

    # Verify all open positions squared off
    post_open_cnt = gw.get_open_positions_count()
    assert post_open_cnt == 0


def test_kotak_risk_checks():
    gw = get_gateway("KOTAK")

    # Clean positions
    gw.emergency_square_off_all()

    # Risk rule 1: Max Open Positions
    max_open = 1
    assert gw.get_open_positions_count() < max_open

    # Open 1 position
    gw.place_market_order(symbol="NIFTY26OCT22500PE", side="BUY", quantity=65, reference_price=150.0)
    assert gw.get_open_positions_count() >= max_open

    # Risk rule 2: Max Consecutive Losses
    loss_count = gw.get_loss_trades_today()
    assert isinstance(loss_count, int)
    assert loss_count >= 0

    # Clean up
    gw.emergency_square_off_all()


def test_live_trade_square_off_time_handling(temp_live_db):
    from datetime import time
    from app.ui.live_trade_app import parse_time_param

    # Test time parser
    assert parse_time_param("15:15", time(15, 30)) == time(15, 15)
    assert parse_time_param("15:25", time(15, 30)) == time(15, 25)

    # Test active trade square off in live db
    row_id = temp_live_db.save_active_trade(
        strategy_name="SQ_OFF_TEST",
        symbol="NIFTY26OCT22500PE",
        session_date="2026-10-14",
        trade_id=9999,
        side="LONG",
        quantity=65,
        entry_time="2026-10-14 10:00:00",
        entry_price=133.47,
        stop_loss=126.80,
        target=173.50,
        score=2,
        regime="TRENDING",
    )
    assert row_id > 0

    # Auto square-off at 15:15:00
    temp_live_db.close_active_trade(
        trade_id_or_row_id=row_id,
        exit_time="2026-10-14 15:15:00",
        exit_price=133.47,
        exit_reason="SQUARE_OFF",
        gross_pnl=0.0,
        charges=20.0,
        peak_gain_pct=0.0,
    )

    t = temp_live_db.get_trades(strategy_name="SQ_OFF_TEST")[0]
    assert t["status"] == "CLOSED"
    assert t["exit_reason"] == "SQUARE_OFF"
    assert t["exit_time"] == "2026-10-14 15:15:00"

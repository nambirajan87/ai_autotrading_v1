from datetime import datetime, timedelta
import pandas as pd
import pytest

from app.execution.market_feed import HistoricalReplayFeed
from app.execution.paper_simulation_session import PaperSimulationSession
from app.execution.streaming_strategy_engine import StreamingStrategyEngine
from app.strategy.strategy_store import list_strategy_names


def make_dummy_candles(n: int = 50, start_price: float = 1000.0) -> pd.DataFrame:
    base_time = datetime(2026, 9, 15, 9, 15)
    records = []
    price = start_price

    for i in range(n):
        ts = base_time + timedelta(minutes=5 * i)
        # Up and down wave
        delta = 2.0 if i % 2 == 0 else -1.0
        open_p = price
        close_p = price + delta
        high_p = max(open_p, close_p) + 1.0
        low_p = min(open_p, close_p) - 1.0
        vol = 10000 + (i * 200)

        records.append({
            "timestamp": ts,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
        })
        price = close_p

    return pd.DataFrame(records)


def test_historical_replay_feed():
    df = make_dummy_candles(10)
    feed = HistoricalReplayFeed(df, symbol="RELIANCE")

    assert feed.total_candles == 10
    assert feed.current_index == 0
    assert not feed.is_finished()

    c1 = feed.step()
    assert c1 is not None
    assert feed.current_index == 1
    assert feed.progress_pct == 10.0

    # Step through all
    while not feed.is_finished():
        feed.step()

    assert feed.is_finished()
    assert feed.step() is None

    # Reset
    feed.reset()
    assert feed.current_index == 0
    assert not feed.is_finished()


def test_streaming_strategy_engine():
    df = make_dummy_candles(40)
    strats = list_strategy_names()
    strat_name = strats[0]

    engine = StreamingStrategyEngine.from_saved_strategy("RELIANCE", strat_name)
    assert engine.symbol == "RELIANCE"

    for _, row in df.iterrows():
        sig = engine.on_candle(row)
        assert sig.decision in ("LONG", "SHORT", "HOLD")
        assert isinstance(sig.score, int)
        assert sig.close > 0


def test_paper_simulation_session_run():
    df = make_dummy_candles(60)
    strats = list_strategy_names()
    strat_name = strats[0]

    session = PaperSimulationSession(
        symbol="RELIANCE",
        isin="INE002A01018",
        date_str="2026-09-15",
        strategy_name=strat_name,
        candles_df=df,
        capital=100000.0,
    )

    # Initial state
    init_state = session.get_state()
    assert init_state["symbol"] == "RELIANCE"
    assert init_state["total_candles"] == 60
    assert init_state["current_index"] == 0

    # Step through 5 candles
    for _ in range(5):
        s = session.step()
        assert s is not None

    assert session.feed.current_index == 5
    assert len(session.equity_curve) == 5

    # Run remaining
    final_state = session.run_all()
    assert final_state["is_finished"] is True
    assert final_state["current_index"] == 60
    assert len(session.equity_curve) == 60


def test_paper_simulation_session_warmup_and_reset():
    warmup_df = make_dummy_candles(20)
    day_df = make_dummy_candles(30)
    strats = list_strategy_names()
    strat_name = strats[0]

    session = PaperSimulationSession(
        symbol="RELIANCE",
        isin="INE002A01018",
        date_str="2026-09-15",
        strategy_name=strat_name,
        candles_df=day_df,
        capital=100000.0,
        warmup_df=warmup_df,
    )

    # Feed should only have day_df candles (30), not warmup candles
    assert session.feed.total_candles == 30
    assert session.feed.current_index == 0

    # Engine indicators buffer should already contain warmup bars
    assert session.engine.latest_indicators_df is not None
    assert len(session.engine.latest_indicators_df) == 20

    # Advance 5 steps
    for _ in range(5):
        session.step()
    assert session.feed.current_index == 5
    assert len(session.engine.latest_indicators_df) == 25

    # Reset
    session.reset()
    assert session.feed.current_index == 0
    assert len(session.engine.latest_indicators_df) == 20
    assert len(session.trade_history) == 0
    assert session.open_trade is None


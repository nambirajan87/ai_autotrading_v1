import pytest
import datetime as dt
import pandas as pd
from unittest.mock import MagicMock
from app.gateways.gateway_factory import get_gateway, list_supported_gateways
from app.gateways.upstox_gateway import UpstoxGateway
from app.gateways.kotak_gateway import KotakGateway


def test_gateway_factory():
    assert "UPSTOX" in list_supported_gateways()
    assert "KOTAK" in list_supported_gateways()

    gw_upstox = get_gateway("UPSTOX")
    assert isinstance(gw_upstox, UpstoxGateway)
    assert gw_upstox.gateway_name == "UPSTOX"

    gw_kotak = get_gateway("KOTAK")
    assert isinstance(gw_kotak, KotakGateway)
    assert gw_kotak.gateway_name == "KOTAK"


def test_upstox_gateway_operations():
    gw = UpstoxGateway()
    info = gw.get_connection_info()
    assert "gateway" in info
    assert info["gateway"] == "Upstox Pro v2"

    balance = gw.get_balance()
    assert balance.gateway == "UPSTOX"
    assert balance.total_capital >= 100000.0

    # Place Market Order
    order = gw.place_market_order(
        symbol="NIFTY26OCT22500PE",
        side="BUY",
        quantity=65,
        reference_price=185.0,
    )
    assert order.status == "FILLED"
    assert order.gateway == "UPSTOX"
    assert order.quantity == 65
    assert order.fill_price > 0
    assert order.charges > 0

    positions = gw.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "NIFTY26OCT22500PE"
    assert positions[0].quantity == 65


def test_kotak_gateway_operations():
    gw = KotakGateway()
    info = gw.get_connection_info()
    assert "gateway" in info
    assert info["gateway"] == "Kotak Neo API"
    assert "Trade Free" in info["brokerage_plan"]

    balance = gw.get_balance()
    assert balance.gateway == "KOTAK"

    # Place Market Order
    order = gw.place_market_order(
        symbol="NIFTY26OCT22500PE",
        side="SELL",
        quantity=65,
        reference_price=185.0,
    )
    assert order.status == "FILLED"
    assert order.gateway == "KOTAK"
    assert order.quantity == 65
    assert "KOTAK-PAPER" in order.order_id or "KOTAK-NEO" in order.order_id

    positions = gw.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == -65


def test_kotak_dynamic_instruments():
    gw = KotakGateway()
    # Test smart search with parsed query
    results = gw.smart_search_instruments("NIFTY 22500 PE")
    assert isinstance(results, list)
    if results:
        r = results[0]
        assert "tradingsymbol" in r
        assert "lot_size" in r
        assert "instrument_key" in r
        assert r["lot_size"] > 0
        assert "22500" in r["tradingsymbol"] or "PE" in r["tradingsymbol"]

    # Test expiries
    expiries = gw.get_expiries("NIFTY")
    assert isinstance(expiries, list)
    if expiries:
        assert len(expiries) > 0
        assert "-" in expiries[0]  # e.g. YYYY-MM-DD format


def test_kotak_fetch_historical_candles_chunking():
    import datetime as dt
    from unittest.mock import MagicMock

    gw = KotakGateway()
    mock_client = MagicMock()

    # Mock historical_data response for two chunks
    def mock_hist(neosymbol, interval, from_date, to_date):
        return {
            "status": "SUCCESS",
            "data": {
                "candles": [
                    [f"{from_date}T09:16:00+05:30", 100.0, 105.0, 99.0, 104.0, 5000],
                    [f"{to_date}T15:30:00+05:30", 104.0, 106.0, 103.0, 105.5, 4500],
                ]
            },
        }

    mock_client.historical_data.side_effect = mock_hist
    gw._client = mock_client

    # Request 37-day range (which triggers >= 2 chunks of <= 25 days)
    df = gw.fetch_historical_candles(
        symbol="NIFTY26OCT22500PE",
        instrument_key="nse_fo|51331",
        timeframe_minutes=1,
        start_date=dt.date(2026, 9, 1),
        end_date=dt.date(2026, 10, 7),
    )

    assert not df.empty
    assert mock_client.historical_data.call_count >= 2
    # Verify chunks stay within 25 days
    for call in mock_client.historical_data.call_args_list:
        c_kwargs = call.kwargs
        c_from = dt.date.fromisoformat(c_kwargs["from_date"])
        c_to = dt.date.fromisoformat(c_kwargs["to_date"])
        assert (c_to - c_from).days <= 25


def test_kotak_prevents_invalid_neosymbol_call():
    """Verifies that non-numeric tokens like 'NIFTY' are NEVER sent to Kotak API."""
    gw = KotakGateway()
    mock_client = MagicMock()
    gw._client = mock_client

    # 1. get_live_market_depth with non-numeric token (e.g. "NSE_FO|NIFTY" or "NIFTY")
    depth = gw.get_live_market_depth(symbol="NIFTY", instrument_key="NSE_FO|NIFTY")
    assert depth is not None
    # quotes MUST NOT have been called with "NIFTY"
    assert mock_client.quotes.call_count == 0

    # 2. fetch_historical_candles with non-numeric token
    df = gw.fetch_historical_candles(
        symbol="NIFTY",
        instrument_key="NSE_FO|NIFTY",
        timeframe_minutes=5,
        start_date=dt.date(2026, 10, 26),
        end_date=dt.date(2026, 10, 26),
    )
    # historical_data MUST NOT have been called with "nse_fo|NIFTY"
    for call in mock_client.historical_data.call_args_list:
        assert "nse_fo|NIFTY" not in str(call)



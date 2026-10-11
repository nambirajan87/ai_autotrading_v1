import pytest
import pandas as pd
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
    assert "KOTAK-NEO" in order.order_id

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

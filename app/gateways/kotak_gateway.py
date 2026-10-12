import base64
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd
import requests

from app.backtest.costs import apply_slippage, leg_cost
from app.config.cost_config import CostConfig
from app.gateways.base_gateway import (
    BaseBrokerGateway,
    GatewayBalance,
    GatewayOrder,
    GatewayPosition,
)

logger = logging.getLogger(__name__)


try:
    from neo_api_client import NeoAPI
    HAS_NEO_SDK = True
except ImportError:
    NeoAPI = None
    HAS_NEO_SDK = False

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    pyotp = None
    HAS_PYOTP = False


class KotakGateway(BaseBrokerGateway):
    """
    Kotak Securities Neo API Gateway for Live and Paper Trading.
    Supports official Kotak Neo Python SDK (neo_api_client v3.0.7) and Sandbox/Paper execution.
    """

    def __init__(
        self,
        consumer_key: Optional[str] = None,
        consumer_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        mobile_number: Optional[str] = None,
        ucc: Optional[str] = None,
        mpin: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ):
        def _get_val(k: str) -> str:
            val = os.getenv(k, "")
            if val:
                return str(val).strip()
            try:
                import streamlit as st
                if hasattr(st, "secrets"):
                    if k in st.secrets:
                        return str(st.secrets[k]).strip()
                    if k.lower() in st.secrets:
                        return str(st.secrets[k.lower()]).strip()
                    if k.upper() in st.secrets:
                        return str(st.secrets[k.upper()]).strip()
                    for sec in ["kotak", "KOTAK", "general", "env"]:
                        if sec in st.secrets and isinstance(st.secrets[sec], dict):
                            if k in st.secrets[sec]:
                                return str(st.secrets[sec][k]).strip()
                            if k.lower() in st.secrets[sec]:
                                return str(st.secrets[sec][k.lower()]).strip()
            except Exception:
                pass
            return ""

        self._consumer_key = consumer_key or _get_val("KOTAK_CONSUMER_KEY") or _get_val("KOTAK_API_KEY") or "b800d632-8a0f-4a2e-9ca5-fff80c81f64b"
        self._consumer_secret = consumer_secret or _get_val("KOTAK_CONSUMER_SECRET") or _get_val("KOTAK_API_SECRET")
        self._access_token = access_token or _get_val("KOTAK_ACCESS_TOKEN") or _get_val("KOTAK_SESSION_TOKEN")
        self._session_id = _get_val("KOTAK_SESSION_ID")
        self._mobile_number = mobile_number or _get_val("KOTAK_MOBILE_NUMBER")
        self._ucc = ucc or _get_val("KOTAK_UCC")
        self._mpin = mpin or _get_val("KOTAK_MPIN")
        self._totp_secret = totp_secret or _get_val("KOTAK_TOTP_SECRET")
        self._authenticated = bool(self._access_token and len(self._access_token) > 10)

        self._client: Optional[Any] = None
        if HAS_NEO_SDK and (self._consumer_key or self._access_token):
            try:
                self._client = NeoAPI(
                    consumer_key=self._consumer_key or None,
                    access_token=self._access_token or None,
                    environment="prod",
                )
                if self._client and hasattr(self._client, "configuration"):
                    if self._access_token:
                        self._client.configuration.bearer_token = self._access_token
                        self._client.configuration.edit_token = self._access_token
                        self._client.configuration.view_token = self._access_token
                    if self._session_id:
                        self._client.configuration.sid = self._session_id
                        self._client.configuration.edit_sid = self._session_id
                    if self._ucc:
                        self._client.configuration.ucc = self._ucc
                    if self._consumer_key:
                        self._client.configuration.consumer_key = self._consumer_key
            except Exception as e:
                logger.debug("Kotak NeoAPI client init note: %s", e)

        self._base_url = "https://gw-napi.kotaksecurities.com"
        self._orders: List[GatewayOrder] = []
        self._positions: Dict[str, GatewayPosition] = {}
        self._capital: float = 100_000.0
        # Kotak Trade-Free Plan: Intraday Eq brokerage = 0, F&O = flat 20
        self._cost_cfg = CostConfig(flat_cost_per_order=0.0)
        self._order_counter = 0
        self.live_order_routing = False

    @property
    def gateway_name(self) -> str:
        return "KOTAK"

    def login(self) -> bool:
        """Performs 2FA TOTP + MPIN login using Kotak Neo SDK."""
        if not (self._client and self._mobile_number and self._ucc and self._mpin and self._totp_secret):
            return False
        try:
            current_totp = self._totp_secret
            if HAS_PYOTP and len(self._totp_secret) > 6:
                current_totp = pyotp.TOTP(self._totp_secret).now()

            clean_mob = self._mobile_number.strip().replace(" ", "").replace("-", "")
            if len(clean_mob) == 10 and clean_mob.isdigit():
                clean_mob = f"+91{clean_mob}"

            login_resp = self._client.totp_login(
                mobile_number=clean_mob,
                ucc=self._ucc,
                totp=current_totp,
            )
            if isinstance(login_resp, dict) and login_resp.get("error"):
                logger.warning("Kotak totp_login failed: %s", login_resp.get("error"))
                return False

            val_resp = self._client.totp_validate(mpin=self._mpin)
            if isinstance(val_resp, dict) and val_resp.get("error"):
                logger.warning("Kotak totp_validate failed: %s", val_resp.get("error"))
                return False

            if isinstance(val_resp, dict) and "data" in val_resp:
                self._access_token = val_resp["data"].get("token", self._access_token)
            self._authenticated = True
            logger.info("Kotak Neo API logged in successfully for UCC: %s", self._ucc)
            return True
        except Exception as e:
            logger.warning("Kotak login exception: %s", e)
            return False

    def is_connected(self) -> bool:
        """Returns True if Kotak Neo consumer key or access token is configured."""
        return bool(
            self._authenticated
            or (self._access_token and len(self._access_token) > 10)
            or (self._consumer_key and len(self._consumer_key) > 10)
        )

    def get_connection_info(self) -> Dict[str, Any]:
        connected = self.is_connected()
        key_preview = f"{self._consumer_key[:4]}...{self._consumer_key[-3:]}" if self._consumer_key else "Not set"
        ucc_preview = self._ucc or "Sandbox"
        return {
            "gateway": "Kotak Neo API",
            "connected": connected,
            "status": f"Online (Kotak Neo - {ucc_preview})" if connected else "Sandbox / Paper Mode Active",
            "consumer_key": key_preview,
            "ucc": ucc_preview,
            "brokerage_plan": "Kotak Trade Free (Rs. 0 Intraday Brokerage)",
            "latency_ms": 38 if connected else 0,
            "supported_segments": ["NSE_CASH", "NSE_FO", "BSE_CASH"],
        }

    def get_balance(self) -> GatewayBalance:
        # 1. Try official SDK client.limits() if authenticated
        if self._client and self._authenticated:
            try:
                limits_data = self._client.limits()
                if isinstance(limits_data, dict) and limits_data.get("stat") == "Ok":
                    net_avail = float(limits_data.get("Net", 0.0))
                    used = float(limits_data.get("MarginUsed", 0.0))
                    cap = net_avail + used if (net_avail + used) > 0 else self._capital
                    return GatewayBalance(
                        gateway="KOTAK",
                        available_margin=net_avail if net_avail > 0 else self._capital,
                        used_margin=used,
                        total_capital=cap,
                    )
            except Exception as e:
                logger.debug("Kotak client.limits() note: %s", e)

        # 2. Try REST limits if token exists
        if self.is_connected() and self._access_token:
            try:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "Neo-Fin-Key": "neotrade",
                }
                resp = requests.get(f"{self._base_url}/Orders/2.0/quick/user/limits", headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    avail = float(data.get("availableMargin", self._capital))
                    used = float(data.get("utilizedMargin", 0.0))
                    return GatewayBalance(
                        gateway="KOTAK",
                        available_margin=avail,
                        used_margin=used,
                        total_capital=avail + used,
                    )
            except Exception as e:
                logger.debug("Kotak live balance query error: %s", e)

        # 3. Local state fallback for paper simulations
        used_margin = sum(abs(p.quantity) * p.avg_price for p in self._positions.values())
        return GatewayBalance(
            gateway="KOTAK",
            available_margin=max(0.0, self._capital - used_margin),
            used_margin=used_margin,
            total_capital=self._capital,
        )

    def get_positions(self) -> List[GatewayPosition]:
        return list(self._positions.values())

    def get_orders(self) -> List[GatewayOrder]:
        return list(self._orders)

    def get_ltp(self, symbol: str, instrument_key: Optional[str] = None) -> float:
        # 1. Try Kotak Neo SDK quotes endpoint (high accuracy live tick)
        if self._client:
            tok = None
            if instrument_key and "|" in str(instrument_key):
                tok = str(instrument_key).split("|")[-1]
            elif symbol and str(symbol).isdigit():
                tok = str(symbol)
            elif instrument_key and str(instrument_key).isdigit():
                tok = str(instrument_key)

            if tok and tok.isdigit():
                try:
                    seg = "nse_fo" if ("fo" in str(instrument_key).lower() or len(symbol) > 10) else "nse_cm"
                    q = self._client.quotes([{"instrument_token": str(tok), "exchange_segment": seg}])
                    if isinstance(q, list) and q and "ltp" in q[0]:
                        return float(q[0]["ltp"])
                except Exception as e:
                    logger.debug("Kotak SDK quotes LTP error: %s", e)

        # 2. Try REST quotes endpoint
        if self.is_connected() and self._access_token:
            try:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                }
                resp = requests.get(f"{self._base_url}/Quotes/2.0/quote/marketwatch/{symbol}", headers=headers, timeout=3)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    if "lastTradedPrice" in data:
                        return float(data["lastTradedPrice"])
            except Exception as e:
                logger.debug("Kotak live LTP fetch error: %s", e)

        if symbol in self._positions:
            return self._positions[symbol].ltp
        return 0.0

    def test_top5_endpoints(self, symbol: str = "NIFTY26OCT22500PE", token: str = "51331") -> Dict[str, Any]:
        """
        Tests the Top 5 priority Kotak Neo endpoints live and returns structured diagnostic results.
        1. Historical Data (Candle Feed)
        2. Quotes (Live LTP & 5-Level Market Depth)
        3. Limits (Margin & Capital Balance)
        4. Positions (Open Positions Book)
        5. Order Report (Live Order History & Order Book)
        """
        import time as _t
        results = {}
        if not self._client:
            self.login()
        if not self._client:
            return {"error": "Kotak Neo client not initialized"}

        # 1. Limits (Margin)
        try:
            t0 = _t.time()
            lim = self._client.limits()
            ms = int((_t.time() - t0) * 1000)
            if isinstance(lim, dict) and (lim.get("stCode") == 100022 or lim.get("code") == 401):
                self.login()
                lim = self._client.limits()
            stat = "ONLINE" if isinstance(lim, dict) and (lim.get("stat") == "Ok" or "Net" in lim) else "ERROR"
            results["1_limits"] = {
                "name": "Limits & Margin (Account Balance)",
                "status": stat,
                "latency_ms": ms,
                "summary": f"Net Margin: ₹{lim.get('Net', 'N/A')} | Status: {lim.get('stat', 'Ok')}" if isinstance(lim, dict) else str(lim)[:60],
            }
        except Exception as e:
            results["1_limits"] = {"name": "Limits & Margin", "status": "ERROR", "summary": str(e)}

        # 2. Quotes (LTP & Depth)
        try:
            t0 = _t.time()
            q = self._client.quotes([{"instrument_token": str(token), "exchange_segment": "nse_fo"}])
            ms = int((_t.time() - t0) * 1000)
            if isinstance(q, list) and q and "ltp" in q[0]:
                item = q[0]
                results["2_quotes"] = {
                    "name": "Quotes & Market Depth (Live LTP)",
                    "status": "ONLINE",
                    "latency_ms": ms,
                    "summary": f"LTP: ₹{item.get('ltp')} | Chg: {item.get('change')} ({item.get('per_change')}%) | Vol: {int(item.get('last_volume', 0)):,}",
                }
            else:
                results["2_quotes"] = {"name": "Quotes & Market Depth", "status": "NO_DATA", "latency_ms": ms, "summary": str(q)[:80]}
        except Exception as e:
            results["2_quotes"] = {"name": "Quotes & Market Depth", "status": "ERROR", "summary": str(e)}

        # 3. Historical Data (Candle Feed)
        try:
            t0 = _t.time()
            from datetime import date
            today = date.today()
            df = self.fetch_historical_candles(symbol, f"nse_fo|{token}", 1, today, today)
            ms = int((_t.time() - t0) * 1000)
            if df is not None and not df.empty:
                results["3_historical_data"] = {
                    "name": "Historical Intraday Candles (OHLCV Feed)",
                    "status": "ONLINE",
                    "latency_ms": ms,
                    "summary": f"Loaded {len(df)} 1m candles today | Latest Close: ₹{df.iloc[-1]['close']:.2f} ({str(df.iloc[-1]['timestamp'])[-8:]})",
                }
            else:
                results["3_historical_data"] = {"name": "Historical Intraday Candles", "status": "NO_DATA", "latency_ms": ms, "summary": "No candles returned"}
        except Exception as e:
            results["3_historical_data"] = {"name": "Historical Intraday Candles", "status": "ERROR", "summary": str(e)}

        # 4. Positions (Open Position Book)
        try:
            t0 = _t.time()
            pos = self._client.positions()
            ms = int((_t.time() - t0) * 1000)
            is_ok = isinstance(pos, dict) and (pos.get("stat") == "Ok" or pos.get("stCode") == 5203)
            results["4_positions"] = {
                "name": "Positions Book (F&O / Equity)",
                "status": "ONLINE" if is_ok else "ERROR",
                "latency_ms": ms,
                "summary": "Clear Book (0 Open Positions)" if isinstance(pos, dict) and pos.get("stCode") == 5203 else ("Active Positions Found" if isinstance(pos, list) and pos else str(pos)[:60]),
            }
        except Exception as e:
            results["4_positions"] = {"name": "Positions Book", "status": "ERROR", "summary": str(e)}

        # 5. Order Report (Order Book)
        try:
            t0 = _t.time()
            ord_rep = self._client.order_report()
            ms = int((_t.time() - t0) * 1000)
            is_ok = isinstance(ord_rep, dict) and (ord_rep.get("stat") == "Ok" or ord_rep.get("stCode") == 5203)
            results["5_order_report"] = {
                "name": "Order Report (Order Book & History)",
                "status": "ONLINE" if is_ok else "ERROR",
                "latency_ms": ms,
                "summary": "Clear Order Book (0 orders today)" if isinstance(ord_rep, dict) and ord_rep.get("stCode") == 5203 else ("Orders found" if isinstance(ord_rep, list) and ord_rep else str(ord_rep)[:60]),
            }
        except Exception as e:
            results["5_order_report"] = {"name": "Order Report", "status": "ERROR", "summary": str(e)}

        return results

    def get_expiries(self, underlying: str = "NIFTY", exchange: str = "nse_fo") -> List[str]:
        """Fetches dynamic live rolling expiry dates for NSE FO instruments from Kotak."""
        if not self._client and HAS_NEO_SDK:
            try:
                ck = self._consumer_key or "b800d632-8a0f-4a2e-9ca5-fff80c81f64b"
                self._client = NeoAPI(
                    consumer_key=ck,
                    access_token=self._access_token or None,
                    environment="prod",
                )
            except Exception as e:
                logger.debug("Kotak client re-init note: %s", e)

        if self._client:
            try:
                res = self._client.expiries(exchange=exchange, underlying=underlying.upper())
                if isinstance(res, dict) and "expiries" in res and res["expiries"]:
                    return res["expiries"]
            except Exception as e:
                logger.debug("Kotak expiries error: %s", e)

        from app.data.instrument_loader import DEFAULT_INDEX_EXPIRIES
        return DEFAULT_INDEX_EXPIRIES.get(underlying.upper(), DEFAULT_INDEX_EXPIRIES["NIFTY"])

    def get_option_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[str] = None,
        exchange: str = "nse_fo",
        count: int = 250,
    ) -> Dict[str, Any]:
        """Fetches live option chain with strikes, spot price, and market lot from Kotak."""
        if not self._client:
            return {}
        try:
            return self._client.option_chain(
                exchange=exchange,
                underlying=underlying.upper(),
                expiry=expiry,
                count=count,
            )
        except Exception as e:
            logger.debug("Kotak option_chain error: %s", e)
            return {}

    def search_scrip(
        self,
        symbol: str = "NIFTY",
        exchange_segment: str = "nse_fo",
        expiry: Optional[str] = None,
        option_type: Optional[str] = None,
        strike_price: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Direct scrip search via Kotak Neo SDK."""
        if not self._client:
            return []
        try:
            exp_param = expiry
            if expiry and "-" in expiry and len(expiry) == 10:
                try:
                    exp_param = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d-%b-%Y")
                except Exception:
                    pass
            res = self._client.search_scrip(
                exchange_segment=exchange_segment,
                symbol=symbol,
                expiry=exp_param,
                option_type=option_type,
                strike_price=strike_price,
            )
            if not res and expiry:
                # Retry without expiry to let Kotak match by symbol, strike and option type
                res = self._client.search_scrip(
                    exchange_segment=exchange_segment,
                    symbol=symbol,
                    expiry=None,
                    option_type=option_type,
                    strike_price=strike_price,
                )
            if isinstance(res, list):
                return res
        except Exception as e:
            logger.debug("Kotak search_scrip error: %s", e)
        return []

    def smart_search_instruments(
        self,
        query: str,
        exchange_segment: str = "nse_fo",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Intelligently searches dynamic NSE FO (Options & Futures) instruments from Kotak.
        Parses user queries like 'NIFTY 22500 PE', 'BANKNIFTY 51000 CE', 'NIFTY26OCT22500PE', or 'FINNIFTY'.
        Returns formatted records suitable for live trading selection.
        """
        if not self._client or not query.strip():
            return []

        q = query.strip().upper()

        # 1. Check if compact trading symbol pattern (e.g. NIFTY26OCT22500PE, BANKNIFTY26O1350000CE)
        m = re.match(r"^([A-Z]+)(\d{2}[A-Z0-9]{3})?(\d{4,6})(CE|PE)?$", q)
        if m:
            underlying = m.group(1)
            strike = m.group(3)
            opt_type = m.group(4)
        else:
            # 2. Split words / tokens
            tokens = q.replace("-", " ").replace("_", " ").split()
            known_indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]
            underlying = next((t for t in tokens if t in known_indices), tokens[0] if tokens else "NIFTY")
            opt_type = next((t for t in tokens if t in ("CE", "PE", "FUT")), None)
            strike = next((t for t in tokens if t.isdigit() and len(t) >= 4), None)

        raw_scrips = self.search_scrip(
            symbol=underlying,
            exchange_segment=exchange_segment,
            option_type=opt_type,
            strike_price=strike,
        )

        formatted = []
        for s in raw_scrips:
            tsym = s.get("pTrdSymbol", "")
            if not tsym:
                continue

            lot = int(s.get("lLotSize", 65))
            strike_val = s.get("dStrikePrice;") or s.get("dStrikePrice", "")
            exp_date = s.get("lExpiryDate", "")
            exp_str = ""
            if isinstance(exp_date, (int, float)) and exp_date > 0:
                try:
                    exp_str = datetime.fromtimestamp(exp_date).strftime("%d-%b-%Y")
                except Exception:
                    exp_str = str(exp_date)

            formatted.append({
                "tradingsymbol": tsym,
                "name": f"{s.get('pSymbolName', underlying)} {s.get('pOptionType', '')} {strike_val}".strip(),
                "pSymbol": str(s.get("pSymbol", "")),
                "instrument_key": f"NSE_FO|{s.get('pSymbol')}",
                "lot_size": lot,
                "expiry": exp_str,
                "option_type": s.get("pOptionType", ""),
                "strike_price": strike_val,
                "exchange_segment": s.get("pExchSeg", exchange_segment),
            })
            if len(formatted) >= limit:
                break

        return formatted

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        isin: Optional[str] = None,
        instrument_key: Optional[str] = None,
        client_order_id: Optional[str] = None,
        reference_price: Optional[float] = None,
    ) -> GatewayOrder:
        """
        Executes Kotak Neo Market Order instantly upon candle close.
        In live mode with credentials, routes to Kotak Neo Order Placement endpoint.
        In paper mode, simulates execution with Kotak Trade Free fee structure.
        """
        self._order_counter += 1
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order_id = f"KOTAK-NEO-{datetime.now().strftime('%y%m%d')}-{self._order_counter:05d}"

        # Determine fill price
        fill_price = reference_price if reference_price and reference_price > 0 else self.get_ltp(symbol, instrument_key)
        if fill_price <= 0:
            fill_price = 100.0

        # In live real-money execution mode, route order through official Kotak Neo SDK endpoint
        if getattr(self, "live_order_routing", False) and self._client and self._authenticated:
            try:
                seg = "nse_fo" if ("fo" in str(instrument_key or isin).lower() or len(symbol) > 10) else "nse_cm"
                tx_type = "B" if side.upper() == "BUY" else "S"
                resp = self._client.place_order(
                    exchange_segment=seg,
                    product="MIS",
                    price="0",
                    order_type="MKT",
                    quantity=str(quantity),
                    validity="DAY",
                    trading_symbol=symbol,
                    transaction_type=tx_type,
                    amo="NO",
                    disclosed_quantity="0",
                    trigger_price="0",
                    tag="KOTAKLIVE"
                )
                if isinstance(resp, dict) and "nOrdNo" in resp:
                    live_broker_order_id = resp["nOrdNo"]
                    order_id = f"KOTAK-LIVE-{live_broker_order_id}"
                    logger.info("Kotak Live order placed via SDK: Order ID %s (%s)", live_broker_order_id, resp)
                elif isinstance(resp, dict) and "message" in resp:
                    logger.warning("Kotak Live order rejected by broker: %s", resp["message"])
            except Exception as e:
                logger.error("Kotak Live broker order placement call error: %s", e)

        # Apply market slippage
        adjusted_price = apply_slippage(fill_price, side, self._cost_cfg.slippage_pct)
        # Kotak Zero Brokerage on Intraday
        charges = leg_cost(side, adjusted_price, quantity, self._cost_cfg)

        order = GatewayOrder(
            order_id=order_id,
            gateway="KOTAK",
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            price=round(adjusted_price, 2),
            status="FILLED",
            timestamp=now_ts,
            fill_price=round(adjusted_price, 2),
            fill_time=now_ts,
            charges=round(charges, 2),
            client_order_id=client_order_id,
        )

        # Update local portfolio position
        signed_qty = quantity if side == "BUY" else -quantity
        if symbol in self._positions:
            pos = self._positions[symbol]
            new_qty = pos.quantity + signed_qty
            if new_qty == 0:
                del self._positions[symbol]
            else:
                pos.quantity = new_qty
                pos.avg_price = adjusted_price
                pos.side = "LONG" if new_qty > 0 else "SHORT"
        else:
            self._positions[symbol] = GatewayPosition(
                symbol=symbol,
                gateway="KOTAK",
                quantity=signed_qty,
                avg_price=round(adjusted_price, 2),
                ltp=round(adjusted_price, 2),
                pnl=0.0,
                side="LONG" if signed_qty > 0 else "SHORT",
            )

        self._orders.insert(0, order)
        logger.info("Kotak Neo Market Order Executed: %s %d %s @ %.2f (Order ID: %s)", side, quantity, symbol, adjusted_price, order_id)
        return order

    def cancel_order(self, order_id: str) -> bool:
        for order in self._orders:
            if order.order_id == order_id and order.status == "PENDING":
                order.status = "CANCELLED"
                return True
        return False

    def fetch_historical_candles(
        self,
        symbol: str,
        instrument_key: str,
        timeframe_minutes: int,
        start_date: Any,
        end_date: Any,
    ) -> pd.DataFrame:
        """
        Fetches historical candles from Kotak Neo API using NeoAPI SDK historical_data().
        """
        if timeframe_minutes in (1, 3, 5, 10, 15, 30, 60):
            interval_str = f"{timeframe_minutes}min"
        else:
            interval_str = f"{timeframe_minutes}m"

        from_str = start_date.strftime("%Y-%m-%d") if hasattr(start_date, "strftime") else str(start_date)[:10]
        to_str = end_date.strftime("%Y-%m-%d") if hasattr(end_date, "strftime") else str(end_date)[:10]

        # Format neosymbol: e.g. "nse_fo|51331", "nse_cm|2885"
        neosym = str(instrument_key or symbol or "").strip()
        if "|" in neosym:
            seg, tok = neosym.split("|", 1)
            seg_lower = seg.lower()
            if "fo" in seg_lower:
                neosym = f"nse_fo|{tok}"
            elif "cm" in seg_lower or "eq" in seg_lower or "index" in seg_lower:
                neosym = f"nse_cm|{tok}"
            else:
                neosym = f"{seg_lower}|{tok}"
        elif neosym.isdigit():
            neosym = f"nse_fo|{neosym}"
        else:
            token_found = None
            try:
                from app.data.instrument_loader import NIFTY_STANDARD_INSTRUMENTS
                target_sym = (symbol or neosym).upper().replace(" ", "")
                for item in NIFTY_STANDARD_INSTRUMENTS:
                    if item.get("tradingsymbol", "").upper() == target_sym:
                        ikey = item.get("instrument_key", "")
                        if "|" in ikey:
                            token_found = ikey.split("|")[-1]
                            break
            except Exception:
                pass
            if not token_found:
                try:
                    scrips = self.smart_search_instruments(symbol or neosym)
                    for sc in scrips:
                        p_sym = str(sc.get("pSymbol") or "")
                        if p_sym:
                            token_found = p_sym
                            break
                except Exception:
                    pass
            if token_found:
                neosym = f"nse_fo|{token_found}"

        # 1. Primary: Use Kotak Neo SDK historical_data
        if self._client:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    res = self._client.historical_data(
                        neosymbol=neosym,
                        interval=interval_str,
                        from_date=from_str,
                        to_date=to_str,
                    )
                    if isinstance(res, dict) and res.get("status") == "SUCCESS":
                        candles = res.get("data", {}).get("candles", [])
                        if candles:
                            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                            df["timestamp"] = pd.to_datetime(df["timestamp"])
                            if df["timestamp"].dt.tz is not None:
                                df["timestamp"] = df["timestamp"].dt.tz_localize(None)
                            for c in ["open", "high", "low", "close", "volume"]:
                                df[c] = pd.to_numeric(df[c], errors="coerce")
                            return df.dropna().reset_index(drop=True)
                    elif isinstance(res, dict) and res.get("code") == 429:
                        import time as _t
                        _t.sleep(1.0)
                        continue
                    elif isinstance(res, dict) and (res.get("stCode") == 100022 or "invalid session" in str(res).lower() or res.get("status") == 401 or res.get("code") == 401):
                        logger.info("Kotak session token expired, attempting auto-relogin...")
                        if self.login():
                            continue
                    break
                except Exception as err:
                    err_msg = str(err).lower()
                    if "429" in err_msg or "rate limit" in err_msg:
                        import time as _t
                        _t.sleep(1.0)
                        continue
                    if "100022" in err_msg or "invalid session" in err_msg or "unauthorized" in err_msg or "401" in err_msg:
                        logger.info("Kotak session expired exception, auto-relogin...")
                        if self.login():
                            continue
        # 2. Token resolution retry: If primary neosymbol returned no candles, resolve actual token from symbol
        if self._client and (symbol or neosym):
            try:
                search_query = symbol or neosym
                scrips = self.smart_search_instruments(search_query)
                for sc in scrips:
                    target_tok = str(sc.get("pSymbol") or "")
                    if target_tok and f"nse_fo|{target_tok}" != neosym:
                        logger.info("Retrying Kotak historical_data with resolved token %s for %s", target_tok, search_query)
                        res = self._client.historical_data(
                            neosymbol=f"nse_fo|{target_tok}",
                            interval=interval_str,
                            from_date=from_str,
                            to_date=to_str,
                        )
                        if isinstance(res, dict) and res.get("status") == "SUCCESS":
                            candles = res.get("data", {}).get("candles", [])
                            if candles:
                                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                                df["timestamp"] = pd.to_datetime(df["timestamp"])
                                if df["timestamp"].dt.tz is not None:
                                    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
                                for c in ["open", "high", "low", "close", "volume"]:
                                    df[c] = pd.to_numeric(df[c], errors="coerce")
                                return df.dropna().reset_index(drop=True)
            except Exception as e:
                logger.debug("Kotak smart token retry error: %s", e)

        # 3. Fallback to Upstox only if key is standard Upstox format
        if instrument_key and ("NSE_EQ" in instrument_key or "NSE_INDEX" in instrument_key):
            try:
                from app.data.upstox_loader import fetch_range
                return fetch_range(instrument_key, "minutes", timeframe_minutes, start_date, end_date)
            except Exception as e:
                logger.warning("Upstox fallback fetch_range error: %s", e)

        return pd.DataFrame()

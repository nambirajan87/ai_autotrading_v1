from typing import Dict, Optional
from app.gateways.base_gateway import BaseBrokerGateway
from app.gateways.upstox_gateway import UpstoxGateway
from app.gateways.kotak_gateway import KotakGateway

_GATEWAY_INSTANCES: Dict[str, BaseBrokerGateway] = {}


def get_gateway(gateway_name: str = "UPSTOX") -> BaseBrokerGateway:
    """Factory to retrieve or instantiate Upstox or Kotak Neo gateway."""
    normalized = gateway_name.strip().upper()
    if "KOTAK" in normalized:
        key = "KOTAK"
        if key not in _GATEWAY_INSTANCES:
            _GATEWAY_INSTANCES[key] = KotakGateway()
        return _GATEWAY_INSTANCES[key]
    else:
        key = "UPSTOX"
        if key not in _GATEWAY_INSTANCES:
            _GATEWAY_INSTANCES[key] = UpstoxGateway()
        return _GATEWAY_INSTANCES[key]


def list_supported_gateways():
    return ["UPSTOX", "KOTAK"]

from typing import Optional

from app.config.cost_config import CostConfig


def leg_cost(leg: str, price: float, quantity: int, cfg: CostConfig) -> float:
    """Total charges for ONE order (a BUY or a SELL). A round trip = two legs."""
    if leg not in ("BUY", "SELL"):
        raise ValueError(f"leg must be BUY or SELL, got {leg}")

    if getattr(cfg, "cost_mode", "PERCENT_MODEL") == "FLAT_PER_ORDER":
        return round(float(cfg.flat_cost_per_order), 2)

    turnover = price * quantity
    brokerage = min(turnover * cfg.brokerage_pct, cfg.brokerage_cap)
    stt = turnover * cfg.stt_sell_pct if leg == "SELL" else 0.0
    exchange = turnover * cfg.exch_txn_pct
    sebi = turnover * cfg.sebi_pct
    gst = cfg.gst_pct * (brokerage + exchange + sebi)
    stamp = turnover * cfg.stamp_buy_pct if leg == "BUY" else 0.0
    return round(brokerage + stt + exchange + sebi + gst + stamp, 2)


def apply_slippage(price: float, leg: str, slippage_pct: float = 0.0,
                   cfg: Optional[CostConfig] = None, quantity: int = 1) -> float:
    """Slippage always hurts: buyers pay more, sellers receive less.
    In FLAT_PER_ORDER mode: fill price is worsened by (flat_slippage_per_order / quantity) per share."""
    if leg not in ("BUY", "SELL"):
        raise ValueError(f"leg must be BUY or SELL, got {leg}")

    if cfg is not None and getattr(cfg, "slippage_mode", "PERCENT") == "FLAT_PER_ORDER":
        qty = max(quantity, 1)
        per_share_adverse = cfg.flat_slippage_per_order / qty
        fill = price + per_share_adverse if leg == "BUY" else price - per_share_adverse
        return round(fill, 2)

    factor = 1 + slippage_pct if leg == "BUY" else 1 - slippage_pct
    return round(price * factor, 2)
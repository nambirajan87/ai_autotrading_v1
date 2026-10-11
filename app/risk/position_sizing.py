import math
from typing import Optional


def calculate_quantity(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    lot_size: int = 1,
    max_exposure_value: Optional[float] = None,
    max_quantity: Optional[int] = None,
) -> int:
    """Quantity = floor(risk amount / risk per unit), then limited by every cap.
    Returns 0 if nothing fits. Never rounds UP (that would exceed the risk budget)."""
    if capital <= 0 or entry <= 0 or lot_size < 1:
        raise ValueError("capital and entry must be positive, lot_size >= 1")

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        raise ValueError("stop must differ from entry")

    risk_amount = round(capital * risk_pct, 6)   # round() guards against 999.9999999 float noise
    qty = math.floor(risk_amount / risk_per_unit)

    if max_exposure_value is not None:
        qty = min(qty, math.floor(max_exposure_value / entry))
    if max_quantity is not None:
        qty = min(qty, max_quantity)

    qty = (qty // lot_size) * lot_size           # round DOWN to a whole number of lots
    return max(qty, 0)
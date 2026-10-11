from dataclasses import dataclass


@dataclass
class OpenPosition:
    symbol: str
    quantity: int
    entry_price: float
    side: str = "LONG"


def total_exposure(positions: list) -> float:
    """Total position value. Uses entry price for now; Phase 7 will mark to market."""
    return sum(p.quantity * p.entry_price for p in positions)


def remaining_exposure(capital: float, max_exposure_pct: float, positions: list) -> float:
    return max(capital * max_exposure_pct - total_exposure(positions), 0.0)
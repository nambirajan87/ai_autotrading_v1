def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 2)


def calculate_levels(side: str, entry: float, atr: float, stop_multiplier: float,
                     reward_risk: float, tick: float = 0.05) -> tuple:
    """ATR-based stop and target. Returns (stop_loss, target)."""
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side}")
    if entry <= 0 or atr <= 0:
        raise ValueError("entry and atr must be positive")

    distance = stop_multiplier * atr
    if side == "LONG":
        stop, target = entry - distance, entry + reward_risk * distance
    else:
        stop, target = entry + distance, entry - reward_risk * distance
    return round_to_tick(stop, tick), round_to_tick(target, tick)


def calculate_fixed_pct_levels(side: str, entry: float, target_pct: float, stoploss_pct: float,
                               tick: float = 0.05) -> tuple:
    """Fixed percentage stop and target. Returns (stop_loss, target)."""
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side}")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if target_pct <= 0 or stoploss_pct <= 0:
        raise ValueError("target_pct and stoploss_pct must be positive")

    target_distance = entry * (target_pct / 100.0)
    stop_distance = entry * (stoploss_pct / 100.0)

    if side == "LONG":
        stop, target = entry - stop_distance, entry + target_distance
    else:
        stop, target = entry + stop_distance, entry - target_distance
    return round_to_tick(stop, tick), round_to_tick(target, tick)


def is_stop_valid(side: str, entry: float, stop: float) -> bool:
    """A LONG stop must be below entry, a SHORT stop above it, and never <= 0."""
    if stop <= 0:
        return False
    return stop < entry if side == "LONG" else stop > entry
from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    # ILLUSTRATIVE PLACEHOLDERS: verify against current broker/exchange/SEBI sources.
    brokerage_pct: float = 0.0003      # 0.03% of turnover...
    brokerage_cap: float = 20.0        # ...capped at this many rupees per order
    stt_sell_pct: float = 0.00025      # charged on the SELL leg only
    exch_txn_pct: float = 0.0000297
    sebi_pct: float = 0.000001
    gst_pct: float = 0.18              # applied on brokerage + exchange + SEBI charges
    stamp_buy_pct: float = 0.00003     # charged on the BUY leg only
    slippage_pct: float = 0.0005       # 0.05% adverse price move per market-style fill
    cost_mode: str = "PERCENT_MODEL"   # "FLAT_PER_ORDER" | "PERCENT_MODEL"
    flat_cost_per_order: float = 20.0  # rupees per order
    slippage_mode: str = "PERCENT"     # "FLAT_PER_ORDER" | "PERCENT"
    flat_slippage_per_order: float = 20.0  # rupees total adverse slippage per order

    def __post_init__(self):
        if self.cost_mode not in ("FLAT_PER_ORDER", "PERCENT_MODEL"):
            raise ValueError(f"cost_mode must be FLAT_PER_ORDER or PERCENT_MODEL, got {self.cost_mode}")
        if self.slippage_mode not in ("FLAT_PER_ORDER", "PERCENT"):
            raise ValueError(f"slippage_mode must be FLAT_PER_ORDER or PERCENT, got {self.slippage_mode}")
        for name, value in self.__dict__.items():
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{name} must not be negative")

    @staticmethod
    def zero() -> "CostConfig":
        """All costs off. Used for tests and for 'gross vs net' comparisons."""
        return CostConfig(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                          cost_mode="PERCENT_MODEL", flat_cost_per_order=0.0,
                          slippage_mode="PERCENT", flat_slippage_per_order=0.0)

    @staticmethod
    def flat(cost: float = 20.0, slippage: float = 20.0) -> "CostConfig":
        """Flat cost and slippage per order."""
        return CostConfig(cost_mode="FLAT_PER_ORDER", flat_cost_per_order=cost,
                          slippage_mode="FLAT_PER_ORDER", flat_slippage_per_order=slippage)
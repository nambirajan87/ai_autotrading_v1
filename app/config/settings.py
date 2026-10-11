import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env into environment variables (won't override existing ones)

VALID_MODES = ("PAPER", "LIVE")


@dataclass(frozen=True)  # frozen=True -> immutable, like a Java record
class Settings:
    trading_mode: str
    log_level: str
    max_daily_loss: float
    max_risk_per_trade: float
    max_positions: int
    database_url: str

    @staticmethod
    def from_env() -> "Settings":
        mode = os.getenv("TRADING_MODE", "PAPER").upper()
        if mode not in VALID_MODES:
            raise ValueError(f"TRADING_MODE must be one of {VALID_MODES}, got '{mode}'")

        # Safeguard: LIVE needs an explicit second confirmation flag
        if mode == "LIVE" and os.getenv("CONFIRM_LIVE_TRADING") != "YES":
            raise ValueError("LIVE mode requires CONFIRM_LIVE_TRADING=YES")

        return Settings(
            trading_mode=mode,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "2000")),
            max_risk_per_trade=float(os.getenv("MAX_RISK_PER_TRADE", "0.01")),
            max_positions=int(os.getenv("MAX_POSITIONS", "3")),
            database_url=os.getenv("DATABASE_URL", ""),
        )
import logging
import platform
import sys
from pathlib import Path

from app.config.settings import Settings
from app.monitoring.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def health_check(settings: Settings) -> dict:
    """Returns a dict (like a HashMap) describing system health."""
    return {
        "status": "OK",
        "python_version": platform.python_version(),
        "trading_mode": settings.trading_mode,
        "data_dir_exists": Path("data").exists(),
        "database_configured": bool(settings.database_url),
    }


def main() -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level)

    logger.info("Starting AI Auto-Trading (mode=%s)", settings.trading_mode)
    result = health_check(settings)
    for key, value in result.items():
        logger.info("health | %s = %s", key, value)

    if not result["data_dir_exists"]:
        logger.warning("'data' folder missing; create it before Phase 2")
    return 0


if __name__ == "__main__":  # like Java's public static void main
    sys.exit(main())
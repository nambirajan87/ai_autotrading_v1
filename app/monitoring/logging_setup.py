import logging
import os


def setup_logging(level: str = "INFO") -> None:
    os.makedirs("logs", exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),                    # console
            logging.FileHandler("logs/app.log"),        # file
        ],
    )
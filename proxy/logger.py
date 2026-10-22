import logging
import os
from pathlib import Path


def get_logger(name="cn-proxy"):
    """Console + file logger. Set CN_PROXY_LOG_LEVEL=WARNING for benchmarks."""

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    level_name = os.environ.get("CN_PROXY_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)

    file_handler = logging.FileHandler(
        log_dir / "cn_proxy.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

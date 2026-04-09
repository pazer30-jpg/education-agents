"""
logger.py — לוג מרוכז
כל ההודעות נכתבות ל-output/moki.log + טרמינל.

שימוש:
  from logger import log
  log.info("Agent 1 started")
  log.error("researcher", "NoneType error")
  log.step("writer", "completed", duration=45.2)
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR

LOG_FILE = OUTPUT_DIR / "moki.log"


def _setup() -> logging.Logger:
    logger = logging.getLogger("moki")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-5s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — everything
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console — INFO and above (no debug spam)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


_logger = _setup()


class MokiLogger:
    """Structured logger for pipeline events."""

    def info(self, msg: str):
        _logger.info(msg)

    def debug(self, msg: str):
        _logger.debug(msg)

    def warn(self, msg: str):
        _logger.warning(msg)

    def error(self, agent: str, msg: str):
        _logger.error(f"[{agent}] {msg}")

    def step(self, agent: str, status: str, duration: float = 0, **kwargs):
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        _logger.info(f"[{agent}] {status} ({duration:.1f}s) {extra}".strip())

    def run_start(self, topic: str):
        _logger.info(f"{'='*50}")
        _logger.info(f"PIPELINE START — {topic}")
        _logger.info(f"{'='*50}")

    def run_end(self, success: bool, duration: float):
        status = "SUCCESS" if success else "FAILED"
        _logger.info(f"PIPELINE {status} — {duration/60:.1f} min")
        _logger.info(f"{'='*50}")


log = MokiLogger()

"""Structured logging for BenderPi.

Usage:
    from logger import get_logger
    log = get_logger("stt")
    log.info("Wake word detected")
    log.error("TTS failed", exc_info=True)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "bender.log")
_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"

_initialised = False


def _init_root():
    """Set up the root 'bender' logger with console + file handlers."""
    global _initialised
    if _initialised:
        return
    _initialised = True

    try:
        from config import cfg
        console_level = getattr(logging, cfg.log_level.upper(), logging.INFO)
        file_level = getattr(logging, cfg.log_level_file.upper(), logging.DEBUG)
    except Exception:
        console_level = logging.INFO
        file_level = logging.DEBUG

    root = logging.getLogger("bender")
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        fh.setLevel(file_level)
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
    except Exception:
        pass


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'bender' namespace."""
    _init_root()
    return logging.getLogger(f"bender.{name}")

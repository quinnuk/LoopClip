"""
logging_setup.py
-----------------
Configures a simple rotating-free file logger for the application.

Only lifecycle and download-outcome events are logged (startup, shutdown,
download start/finish/failure, FFmpeg detection, settings errors).
Passwords, cookies, authentication tokens and other sensitive data must
never be passed to this logger.
"""

import logging
from pathlib import Path

from settings import app_data_dir


def _log_dir() -> Path:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(debug: bool = False) -> logging.Logger:
    """Return the application logger, configuring it on first call."""
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        # Already configured (e.g. called again from another module).
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        return logger

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    try:
        handler = logging.FileHandler(_log_dir() / "app.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    except OSError:
        # Logging is best-effort; never let a logging failure crash the app.
        pass
    return logger

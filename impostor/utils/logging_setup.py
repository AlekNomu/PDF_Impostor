"""Logging configuration for the application."""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Also write to a log file in temp dir
    try:
        log_path = Path(tempfile.gettempdir()) / "pdf_impostor.log"
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    logging.getLogger("pypdf").setLevel(logging.WARNING)

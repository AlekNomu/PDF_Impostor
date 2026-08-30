"""
Configuration persistence using a simple JSON file stored in user's AppData.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_NAME = "PDFImpostor"


def _config_dir() -> Path:
    """Return the platform-appropriate config directory."""
    if os.name == "nt":  # Windows
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / APP_NAME


def _config_file() -> Path:
    return _config_dir() / "settings.json"


_DEFAULTS: dict[str, Any] = {
    "last_input_dir": str(Path.home()),
    "last_output_dir": str(Path.home()),
    "sheets_per_signature": 0,
    "duplex_mode": "AUTO_DUPLEX",
    "zoom": 1.0,
    "inside_offset": 0.0,
    "creep_compensation": 0.0,
    "center_adjustment": 0.0,
    "window_geometry": "",
    "recent_files": [],   # list[str], derniers PDFs ouverts
    "profiles": {},       # dict[str, dict[str, Any]], réglages nommés
}

_RECENT_MAX = 10

_PROFILE_KEYS = (
    "sheets_per_signature", "duplex_mode", "zoom",
    "inside_offset", "creep_compensation", "center_adjustment",
)


def add_recent_file(config: "AppConfig", path: str) -> None:
    """Prepend *path* to the recent-files list, dedup, and cap at _RECENT_MAX."""
    recent: list[str] = list(config.get("recent_files", []))
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    config.set("recent_files", recent[:_RECENT_MAX])


class AppConfig:
    """Lightweight config store with dict-like access and auto-save."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        path = _config_file()
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    stored = json.load(fh)
                self._data.update(stored)
                logger.debug("Config loaded from %s", path)
            except (OSError, ValueError, TypeError):
                logger.warning("Could not read config; using defaults.", exc_info=True)

    def save(self) -> None:
        path = _config_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except (OSError, TypeError):
            logger.warning("Could not save config.", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

"""
settings.py
-----------
Loads and saves persistent user settings (window values, folder, quality,
audio, trim defaults, etc.) to a JSON file in the user's AppData folder.
"""

import json
import os
from pathlib import Path

APP_NAME = "AquariumDownloader"

DEFAULTS = {
    "output_folder": r"C:\Projectivy\Screensavers\Aquariums",
    "quality": "best",  # "best" | "4k_hdr" | "1080p"
    "no_audio": False,
    "audio_bitrate": "original",  # "original" | "128" | "192" | "256" | "320"
    "trim_enabled": True,
    "trim_start": "00:05:00",
    "trim_duration": "00:15:00",
    "delete_original": True,
    "open_folder_when_finished": True,
    "last_url": "",
}


def _settings_path() -> Path:
    """
    Returns the path to settings.json inside %APPDATA%\\AquariumDownloader
    (falls back to the user's home directory on non-Windows platforms,
    e.g. during development/testing on macOS/Linux).
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base / "settings.json"


def load_settings() -> dict:
    """Load settings from disk, merged with defaults for any missing keys.

    Any settings saved by an earlier version of the app (without the newer
    audio keys) continue to work - missing keys are simply filled in from
    DEFAULTS.
    """
    path = _settings_path()
    data = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            data.update(saved)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable settings file - fall back to defaults
            pass
    return data


def save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    path = _settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        # Non-fatal: app should continue to work even if it can't persist
        pass

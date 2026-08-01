"""
settings.py
-----------
Loads and saves persistent user settings (window values, folder, quality,
audio, trim defaults, etc.) to a JSON file in the user's AppData folder.
"""

import json
import os
import shutil
from pathlib import Path

APP_NAME = "LoopClip"
_OLD_APP_NAME = "AquariumDownloader"  # pre-rename folder, for one-time migration

DEFAULTS = {
    "output_folder": r"C:\Projectivy\Screensavers\Aquariums",
    "quality": "best",  # "best" | "4k_hdr" | "1080p"
    "no_audio": False,
    "audio_bitrate": "original",  # "original" | "128" | "192" | "256" | "320"
    "video_bitrate": "15",  # "8" | "15" | "25" | "40" (Mbps, re-encode trims only)
    "seamless_loop": False,
    "crossfade_seconds": "2",  # "1" | "2" | "3"
    "trim_enabled": True,
    "trim_start": "00:05:00",
    "trim_duration": "00:15:00",
    "delete_original": True,
    "open_folder_when_finished": True,
    "last_url": "",
}


def _appdata_base(app_name: str) -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / app_name
    return Path.home() / f".{app_name.lower()}"


def _settings_path() -> Path:
    """
    Returns the path to settings.json inside %APPDATA%\\LoopClip (falls
    back to the user's home directory on non-Windows platforms, e.g.
    during development/testing on macOS/Linux).

    One-time migration: if this is the first run since the app was
    renamed from Aquarium Downloader, and settings already exist in the
    old %APPDATA%\\AquariumDownloader folder but not yet in the new one,
    the old settings file is copied across so nothing is lost.
    """
    base = _appdata_base(APP_NAME)
    base.mkdir(parents=True, exist_ok=True)
    new_path = base / "settings.json"

    if not new_path.exists():
        old_path = _appdata_base(_OLD_APP_NAME) / "settings.json"
        if old_path.exists():
            try:
                shutil.copy2(old_path, new_path)
            except OSError:
                pass  # Non-fatal - just starts fresh with defaults instead.

    return new_path


def load_settings() -> dict:
    """Load settings from disk, merged with defaults for any missing keys.

    Any settings saved by an earlier version of the app (without the newer
    audio/video keys) continue to work - missing keys are simply filled in
    from DEFAULTS.
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

"""
settings.py
-----------
Loads and saves persistent user settings to a JSON file in the user's
AppData folder.
"""

import json
import os
from pathlib import Path

APP_NAME = "VideoDownloader"

DEFAULTS = {
    "output_folder": str(Path.home() / "Videos" / "Downloads"),
    "quality": "best",  # "best" | "2160p" | "1440p" | "1080p" | "720p" | "480p" | "360p" | "audio_only"
    "audio_bitrate": "192",  # "128" | "192" | "256" | "320"
    "format_container": "best",  # "best" | "mp4" | "mkv" | "webm"
    "subtitle_mode": "none",  # "none" | "english" | "all"
    "embed_subs": False,
    "speed_limit": "Unlimited",  # "Unlimited" | "1 MB/s" | "2 MB/s" | "5 MB/s" | "10 MB/s" | "Custom"
    "speed_limit_custom_mbps": 5,
    "simultaneous_downloads": 1,  # 1-4
    "cookies_from_browser": "none",  # "none" | "chrome" | "edge" | "firefox" | "brave"
    "notify_on_complete": False,
    "notify_on_failure": True,
    "notify_on_queue_complete": True,
    "play_sound_on_queue_complete": False,
    "delete_temp_on_error": True,
    "open_folder_when_finished": True,
}


def app_data_dir() -> Path:
    """The per-user folder the app stores settings, queue, history and logs
    in. Shared with queue_store.py, history.py and logging_setup.py so
    everything lands in one place.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings() -> dict:
    path = _settings_path()
    data = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            data.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save_settings(settings: dict) -> None:
    path = _settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass

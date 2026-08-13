"""
update_check.py
-----------------
Checks the installed yt-dlp version against the latest one published on
PyPI. Network access only happens when the user explicitly asks (Help menu
"Check for yt-dlp Updates") - never automatically on startup.
"""

import json
from urllib.request import urlopen

import yt_dlp


def installed_version() -> str:
    return getattr(yt_dlp.version, "__version__", "unknown")


def latest_version(timeout: int = 6) -> str | None:
    try:
        with urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=timeout) as response:
            data = json.load(response)
        return data.get("info", {}).get("version")
    except Exception:  # noqa: BLE001 - no internet / PyPI unreachable is an expected outcome here
        return None


def check() -> dict:
    installed = installed_version()
    latest = latest_version()
    return {
        "installed": installed,
        "latest": latest,
        "up_to_date": latest is None or installed == latest,
    }

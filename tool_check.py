"""
tool_check.py
-------------
Detects whether yt-dlp (Python package) and ffmpeg (executable) are
available, so the app can show a clear message if something is missing.

FFmpeg detection explicitly checks, in order:
    1. The bundled PyInstaller directory (sys._MEIPASS), for a onefile build.
    2. The folder the .exe itself lives in, for a onedir build.
    3. The system PATH.
This avoids relying only on PATH, which may not be configured the same way
in every packaged environment.
"""

import shutil
import sys
from pathlib import Path

FFMPEG_EXE_NAMES = ("ffmpeg.exe", "ffmpeg") if sys.platform != "win32" else ("ffmpeg.exe",)


def _bundled_dir() -> Path | None:
    """Directory PyInstaller extracts bundled files to (onefile builds only)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return None


def _app_dir() -> Path:
    """Directory containing the running .exe (or this script, when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_ffmpeg() -> str | None:
    """Return the first valid ffmpeg executable found, or None."""
    search_dirs = []
    bundled = _bundled_dir()
    if bundled:
        search_dirs.append(bundled)
    search_dirs.append(_app_dir())

    for directory in search_dirs:
        for name in FFMPEG_EXE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    return None


def check_ytdlp() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def check_ffmpeg() -> bool:
    return find_ffmpeg() is not None


def missing_tools_message() -> str | None:
    missing = []
    if not check_ytdlp():
        missing.append(
            "yt-dlp (Python package) was not found.\n"
            "Install it with:  pip install yt-dlp"
        )
    if not check_ffmpeg():
        missing.append(
            "FFmpeg was not found. Please install FFmpeg or add it to your "
            "system PATH.\nDownload it from: https://ffmpeg.org/download.html\n"
            "(FFmpeg is only needed to merge separate video+audio streams for "
            "some sites/qualities - many downloads will work without it.)"
        )
    if not missing:
        return None
    return "\n\n".join(missing)

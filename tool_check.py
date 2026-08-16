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

import re
import shutil
import subprocess
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


def get_ffmpeg_version() -> str | None:
    """Return FFmpeg's version string (e.g. '6.1.1'), or None if unavailable."""
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return None
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
        match = re.search(r"ffmpeg version (\S+)", first_line)
        return match.group(1) if match else None
    except Exception:
        return None


_nvenc_cache: tuple[bool, str] | None = None


def check_nvenc(use_cache: bool = True) -> tuple[bool, str]:
    """
    Probe whether NVENC (NVIDIA hardware encoding) actually works, via a
    tiny real test encode - just checking for an NVIDIA GPU isn't enough,
    since NVENC also needs a compatible driver, so this mirrors the
    NVENC-first/CPU-fallback pattern used for real encodes elsewhere in
    the app (see processor.py).

    The result is cached after the first real probe (use_cache=True, the
    default) since this hasn't changed mid-run on any real machine and
    the probe itself costs real time - callers that want a guaranteed
    fresh check (e.g. after a driver update) can pass use_cache=False.

    Returns (available, human_readable_message).
    """
    global _nvenc_cache
    if use_cache and _nvenc_cache is not None:
        return _nvenc_cache

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        result = False, "Not available (FFmpeg not found)"
        _nvenc_cache = result
        return result

    try:
        result = subprocess.run(
            [
                ffmpeg_path, "-v", "error", "-f", "lavfi",
                "-i", "color=black:s=64x64:d=0.1",
                "-c:v", "hevc_nvenc", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            outcome = True, "Available (NVIDIA GPU detected)"
        else:
            outcome = False, (
                "Not available (no compatible NVIDIA GPU/driver detected) - "
                "using CPU encoding"
            )
    except Exception:
        outcome = False, (
            "Not available (no compatible NVIDIA GPU/driver detected) - "
            "using CPU encoding"
        )
    _nvenc_cache = outcome
    return outcome


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
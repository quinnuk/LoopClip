"""
tool_check.py
-------------
Detects whether yt-dlp (as a Python package) and ffmpeg (as an executable)
are available, so the app can show a clear message if something is missing
instead of failing with a cryptic error. Also provides the diagnostic
info shown in the GUI's About dialog (ffmpeg version, NVENC availability).
"""

import subprocess
import sys
import shutil

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def check_ytdlp() -> bool:
    """True if the yt-dlp Python package is importable."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def check_ffmpeg() -> bool:
    """True if an ffmpeg executable is on PATH."""
    return shutil.which("ffmpeg") is not None


def check_loop_detection_deps() -> bool:
    """True if the packages loop_detector.py needs (opencv, numpy) are importable.

    scikit-image is a soft dependency - loop_detector falls back to hash
    comparison for the "ssim"/"combined" methods if it's missing, so it's
    not checked here (not required, just recommended for best results).
    """
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def get_ffmpeg_version() -> str | None:
    """Returns FFmpeg's reported version string (e.g. '6.1.1'), or None if
    ffmpeg isn't on PATH or its output couldn't be parsed."""
    if not check_ffmpeg():
        return None
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=5, creationflags=_NO_WINDOW,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        # Expected format: "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) ..."
        parts = first_line.split()
        if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
            return parts[2]
        return first_line.strip() or None
    except (subprocess.TimeoutExpired, OSError, IndexError):
        return None


def check_nvenc() -> tuple[bool, str]:
    """
    Attempts a tiny real hevc_nvenc encode (a fraction-of-a-second null
    source, discarded output) to check whether NVENC is genuinely usable
    on this machine - not just whether this FFmpeg build was compiled
    with NVENC support. A build can list hevc_nvenc as an available
    encoder while an actual encode still fails without a compatible
    NVIDIA GPU/driver present, which is the more useful thing to know
    here (this mirrors what processor.py's real NVENC-then-CPU-fallback
    logic actually experiences, rather than just checking the encoder
    list).

    Returns (available, status_message) - status_message is meant to be
    shown directly to the user (e.g. in the About dialog), so it's a full
    sentence rather than a code.
    """
    if not check_ffmpeg():
        return False, "Could not be tested - FFmpeg was not found"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                "-c:v", "hevc_nvenc", "-f", "null", "-",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=10, creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, "Available"
        return False, "Not available (no compatible NVIDIA GPU/driver detected) - using CPU encoding"
    except (subprocess.TimeoutExpired, OSError):
        return False, "Could not be tested - using CPU encoding"


def missing_tools_message() -> str | None:
    """
    Returns a friendly message describing what's missing, or None if
    everything required is present.
    """
    missing = []
    if not check_ytdlp():
        missing.append(
            "yt-dlp (Python package) was not found.\n"
            "Install it with:  pip install yt-dlp"
        )
    if not check_ffmpeg():
        missing.append(
            "FFmpeg was not found. Please install FFmpeg or add it to your "
            "system PATH.\nDownload it from: https://ffmpeg.org/download.html"
        )
    if not check_loop_detection_deps():
        missing.append(
            "opencv-python and/or numpy (needed for automatic loop detection) "
            "were not found.\nInstall them with:  pip install opencv-python numpy"
        )
    if not missing:
        return None
    return "\n\n".join(missing)

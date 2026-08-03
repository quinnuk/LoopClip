"""
tool_check.py
-------------
Detects whether yt-dlp (as a Python package) and ffmpeg (as an executable)
are available, so the app can show a clear message if something is missing
instead of failing with a cryptic error.
"""

import shutil


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

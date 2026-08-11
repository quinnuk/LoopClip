"""
Shared pytest fixtures for the LoopClip test suite.

Adds the project root to sys.path (tests run against the source modules
directly, no packaging step) and provides:
  - isolated_appdata: redirects settings.py's storage to a throwaway temp
    directory for the duration of a test, so tests never touch the real
    developer machine's %APPDATA%\\LoopClip.
  - make_test_clip: generates a small synthetic H.264 video via ffmpeg for
    tests that need a real, decodable video file (extract_frames,
    process_video, the CLI end-to-end path, etc). Skips the test instead
    of failing if ffmpeg isn't installed on the machine running the suite.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None

requires_ffmpeg = pytest.mark.skipif(
    not (FFMPEG_AVAILABLE and FFPROBE_AVAILABLE),
    reason="ffmpeg/ffprobe not available on this machine",
)


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    """Redirects settings.py to a throwaway APPDATA-like directory."""
    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    import settings
    yield fake_appdata
    # Settings module caches nothing at import time, so no extra teardown
    # is needed - the env var reverts automatically when the test ends.


@pytest.fixture
def make_test_clip(tmp_path):
    """
    Returns a factory: make_test_clip(name, width=320, height=240, fps=30,
    duration=2, pattern="testsrc") -> str (path to a generated .mp4).

    Uses ffmpeg's lavfi testsrc/testsrc2 source filters, so no external
    fixture files are needed - the whole suite is self-contained.
    """
    if not (FFMPEG_AVAILABLE and FFPROBE_AVAILABLE):
        pytest.skip("ffmpeg/ffprobe not available on this machine")

    counter = {"n": 0}

    def _make(name=None, width=320, height=240, fps=30, duration=2, pattern="testsrc"):
        counter["n"] += 1
        filename = name or f"clip_{counter['n']}.mp4"
        out_path = tmp_path / filename
        # Most lavfi source filters (testsrc, testsrc2, smptebars, ...) take
        # their first option right after "=" (e.g. "testsrc=size=..."), but
        # a caller can also pass a pattern that already has its own
        # key=value params (e.g. "color=c=gray"), in which case additional
        # params need to be joined with ":" instead.
        sep = ":" if "=" in pattern else "="
        filter_str = f"{pattern}{sep}size={width}x{height}:rate={fps}:duration={duration}"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", filter_str,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-loglevel", "error",
            str(out_path),
        ]
        subprocess.run(cmd, check=True)
        return str(out_path)

    return _make

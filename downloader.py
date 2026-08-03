"""
downloader.py
-------------
Wraps yt-dlp to download the best-available video, preferring 4K/HDR/60fps,
while allowing the user to fall back to a more compatible format.

Uses yt-dlp as a Python library (the same reliable approach as the
single-download version of this app) rather than shelling out to a
separate process - that's simpler and avoids environment mismatches
between the running app and a spawned child process (e.g. when launched
via pythonw.exe for a silent start).

Cancellation is handled through yt-dlp's own progress_hooks: yt-dlp calls
the hook many times per second while downloading, so raising
KeyboardInterrupt from inside it (a technique yt-dlp explicitly supports
for exactly this purpose) stops the download almost immediately.
"""

import re
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?"
    r"(youtube\.com/(watch\?(?:.*&)?v=[\w\-]+|shorts/[\w\-]+|live/[\w\-]+)"
    r"|youtu\.be/[\w\-]+)",
    re.IGNORECASE,
)


class CancelledError(Exception):
    """Raised when a download is aborted because the user pressed Cancel."""


def is_valid_youtube_url(url: str) -> bool:
    """Basic validation for a YouTube video URL."""
    if not url:
        return False
    return bool(YOUTUBE_URL_RE.match(url.strip()))


# Format strings per quality mode, with and without an audio stream.
# "best": true best-available, any codec (AV1/VP9/H.264), 4K/HDR/60fps welcome.
# "4k_hdr": explicitly prefer 4K + HDR, falling back gracefully.
# "1080p": capped at 1080p, prefer H.264 for broad compatibility.
FORMAT_SELECTORS = {
    "best": "bestvideo+bestaudio/best",
    "4k_hdr": (
        "bestvideo[height<=2160][vcodec^=vp09.02]+bestaudio/"
        "bestvideo[height<=2160][vcodec^=av01][dynamic_range=HDR]+bestaudio/"
        "bestvideo[height<=2160]+bestaudio/best"
    ),
    "1080p": (
        "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
    ),
}

FORMAT_SELECTORS_NO_AUDIO = {
    "best": "bestvideo/best",
    "4k_hdr": (
        "bestvideo[height<=2160][vcodec^=vp09.02]/"
        "bestvideo[height<=2160][vcodec^=av01][dynamic_range=HDR]/"
        "bestvideo[height<=2160]/best"
    ),
    "1080p": (
        "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]/"
        "bestvideo[height<=1080]/best[height<=1080]"
    ),
}


class DownloadResult:
    def __init__(self, filepath: str, title: str):
        self.filepath = filepath
        self.title = title


def get_installed_version() -> str:
    """The installed yt-dlp version string, e.g. '2026.07.04'."""
    return yt_dlp.version.__version__


def download_video(
    url: str,
    temp_dir: str,
    quality: str,
    no_audio: bool = False,
    section_range_seconds: Optional[tuple] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    process_holder: Optional[dict] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> DownloadResult:
    """
    Downloads the given YouTube URL into temp_dir using the selected
    quality mode. Returns the path (and title) of the downloaded file.

    section_range_seconds, if given, is a (start_seconds, end_seconds)
    tuple - only that portion of the video is downloaded (via yt-dlp's
    download_sections), instead of the whole thing. This is what makes
    trimming a few minutes out of a multi-hour video fast: yt-dlp only
    fetches the needed range from YouTube's servers rather than the full
    video, and force_keyframes_at_cuts asks it to re-mux for a clean cut
    at those boundaries. The caller is expected to pass a slightly padded
    range (a bit before/after the actual desired trim points) so the
    existing local FFmpeg trim step afterward still has enough margin to
    make a frame-accurate final cut.

    progress_callback receives a dict with keys like:
      status ('downloading' | 'finished')
      percent (float, 0-100) - when known
      speed (str) - human readable, e.g. "4.2 MB/s"
      eta (str) - human readable, e.g. "00:42"

    process_holder is accepted for API symmetry with processor.process_video
    (which does use it, for its FFmpeg subprocess) but isn't used here -
    there's no separate OS process to hold a handle to.

    cancel_check, if given, is polled from inside yt-dlp's own progress
    hook (called many times per second while downloading); if it returns
    True, CancelledError is raised almost immediately.
    """
    Path(temp_dir).mkdir(parents=True, exist_ok=True)

    selectors = FORMAT_SELECTORS_NO_AUDIO if no_audio else FORMAT_SELECTORS
    fmt = selectors.get(quality, selectors["best"])

    def hook(d):
        if cancel_check is not None and cancel_check():
            # yt-dlp explicitly supports cancelling a download by raising
            # KeyboardInterrupt from inside a progress hook.
            raise KeyboardInterrupt()

        if progress_callback is None:
            return

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else None
            speed = d.get("speed")
            eta = d.get("eta")
            eta_int = int(eta) if eta else None
            progress_callback({
                "status": "downloading",
                "percent": percent,
                "speed": f"{speed / 1024 / 1024:.1f} MB/s" if speed else "-",
                "eta": f"{eta_int // 60:02d}:{eta_int % 60:02d}" if eta_int else "-",
            })
        elif d["status"] == "finished":
            progress_callback({"status": "finished", "percent": 100.0})

    ydl_opts = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(Path(temp_dir) / "%(title)s.%(ext)s"),
        "progress_hooks": [hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    if section_range_seconds is not None:
        start_s, end_s = section_range_seconds
        ydl_opts["download_sections"] = [f"*{start_s}-{end_s}"]
        # Asks yt-dlp to re-mux so the section boundaries land on clean
        # cut points, rather than the nearest (possibly much earlier)
        # keyframe - keeps the downloaded section tight to what was asked
        # for instead of over-fetching.
        ydl_opts["force_keyframes_at_cuts"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except KeyboardInterrupt as exc:
        raise CancelledError("Download cancelled by user.") from exc
    except Exception as exc:  # noqa: BLE001
        # A cancel can sometimes surface as a different exception once
        # yt-dlp has wrapped/re-raised it internally - if we know a cancel
        # was requested, treat it as one regardless of the exact type.
        if cancel_check is not None and cancel_check():
            raise CancelledError("Download cancelled by user.") from exc
        raise RuntimeError(str(exc)) from exc

    filepath = ydl.prepare_filename(info)
    # merge_output_format may change the extension to .mp4
    mp4_path = str(Path(filepath).with_suffix(".mp4"))
    final_path = mp4_path if Path(mp4_path).exists() else filepath
    if not Path(final_path).exists():
        final_path = _guess_downloaded_file(temp_dir) or final_path

    if not Path(final_path).exists():
        raise RuntimeError("Could not locate the downloaded file.")

    return DownloadResult(filepath=final_path, title=info.get("title", "video"))


def _guess_downloaded_file(temp_dir: str) -> Optional[str]:
    """Fallback: pick the most recently modified file in temp_dir."""
    candidates = [p for p in Path(temp_dir).glob("*") if p.is_file()]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)

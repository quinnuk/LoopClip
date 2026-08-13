"""Small yt-dlp wrapper used by the desktop application."""

import re
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yt_dlp

import tool_check

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

FORMAT_SELECTORS = {
    "best": "bestvideo+bestaudio/best",
    "2160p": "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best",
    "1440p": "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
    "audio_only": "bestaudio/best",
}

# Container ("format") the final video is remuxed into. None means "Best
# Available": don't force a container at all, so yt-dlp keeps whatever
# native container avoids a re-encode for that particular source.
CONTAINER_FORMATS = {
    "best": None,
    "mp4": "mp4",
    "mkv": "mkv",
    "webm": "webm",
}

# Query-string keys that don't change which video a URL points at. Stripping
# these lets "example.com/video/123" and "example.com/video/123?utm_source=x"
# be recognised as the same video for duplicate detection.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "si", "feature", "fbclid", "gclid", "igshid", "ref", "ref_src",
    "ref_url", "spm", "context", "app",
}

# Windows-only long-path signals (WinError 3 = path not found because it's
# too long, WinError 206 = filename or extension too long).
_LONG_PATH_MARKERS = ("winerror 3", "winerror 206", "too long", "filename too long")


class DownloadCancelled(Exception):
    """Raised when the user abandons a download: the partial file is removed."""


class DownloadPaused(Exception):
    """Raised when the user pauses a download: the partial file is kept."""


class DownloadControl:
    """Coordinates pause/cancel requests between the UI thread and the
    background download thread for whichever item is currently downloading.
    """

    def __init__(self):
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self.current_filename: str | None = None

    def request_pause(self) -> None:
        self._pause.set()

    def request_cancel(self) -> None:
        self._cancel.set()

    def is_pause_requested(self) -> bool:
        return self._pause.is_set()

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def reset(self) -> None:
        self._pause.clear()
        self._cancel.clear()
        self.current_filename = None


class DownloadResult:
    def __init__(self, filepath: str, title: str):
        self.filepath = filepath
        self.title = title


def is_valid_url(url: str) -> bool:
    return bool(url) and bool(URL_RE.match(url.strip()))


def normalize_url(url: str) -> str:
    """Canonical form of a URL for duplicate detection: strips tracking
    query parameters, a leading "www.", and any trailing slash, and sorts
    remaining query parameters so ordering doesn't matter either.
    """
    parts = urlsplit(url.strip())
    kept_params = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    )
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(kept_params), ""))


def classify_error(exc: Exception) -> tuple[str, str]:
    """Turn a raw yt-dlp/OS exception into (short human reason, full detail)."""
    detail = str(exc)
    lowered = detail.lower()

    if any(marker in lowered for marker in _LONG_PATH_MARKERS):
        reason = "The destination file path is too long for Windows."
    elif "private video" in lowered:
        reason = "This video is private."
    elif "sign in" in lowered or "login required" in lowered:
        reason = "This video requires you to be signed in (login required)."
    elif "age" in lowered and "restrict" in lowered:
        reason = "This video is age-restricted."
    elif "region" in lowered or "not available in your country" in lowered:
        reason = "This video is not available in your region."
    elif "unavailable" in lowered or "removed" in lowered:
        reason = "This video is unavailable. It may have been removed or made private."
    elif "requested format is not available" in lowered:
        reason = "The requested format is not available for this video."
    elif "429" in lowered or ("rate" in lowered and "limit" in lowered):
        reason = "You're being rate-limited by the site. Try again in a few minutes."
    elif isinstance(exc, (TimeoutError, ConnectionError)) or any(
        term in lowered for term in ("timed out", "connection", "network")
    ):
        reason = "A network error occurred."
    else:
        reason = "The download failed."
    return reason, detail


def get_video_info(url: str, cookies_from_browser: str = "none") -> dict:
    """Return lightweight information for the preview panel without downloading.

    If the URL points at a playlist, returns {"is_playlist": True, "playlist_title": ...,
    "entries": [{"url": ..., "title": ...}, ...]} using yt-dlp's flat extraction, which is
    fast even for large playlists since it doesn't fetch full metadata per video.
    Otherwise returns the usual single-video info shape with "is_playlist": False.

    cookies_from_browser lets private/login-required videos be previewed using an
    existing browser session ("chrome", "edge", "firefox", "brave") - "none" skips this.
    """
    probe_options = {
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
    }
    if cookies_from_browser and cookies_from_browser != "none":
        probe_options["cookiesfrombrowser"] = (cookies_from_browser,)
    with yt_dlp.YoutubeDL(probe_options) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist" or "entries" in info:
        entries = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            entry_url = entry.get("url") or entry.get("webpage_url")
            if not entry_url:
                continue
            if not is_valid_url(entry_url):
                # Flat extraction sometimes returns a bare video ID rather than a full
                # URL for some sites; skip anything we can't turn into a real link.
                continue
            entries.append({
                "url": entry_url,
                "title": entry.get("title") or entry.get("id") or "Video",
            })
        return {
            "is_playlist": True,
            "playlist_title": info.get("title") or "Playlist",
            "entries": entries,
        }

    return {
        "is_playlist": False,
        "title": info.get("title", "Video"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }


def download_video(
    url: str,
    output_dir: str,
    quality: str,
    progress_callback: Optional[Callable[[dict], None]] = None,
    include_audio: bool = True,
    keep_original: bool = False,
    duplicate_mode: str = "Rename automatically",
    control: Optional[DownloadControl] = None,
    duplicate_callback: Optional[Callable[[str], str]] = None,
    audio_bitrate: str = "192",
    format_container: str = "best",
    subtitle_mode: str = "none",
    embed_subs: bool = False,
    speed_limit_bytes: Optional[int] = None,
    retry_attempts: int = 3,
    retry_delay: int = 5,
    cookies_from_browser: str = "none",
) -> DownloadResult:
    """Download one item, optionally without sound, and report its progress.

    Pause vs. cancel: if `control.request_pause()` was called, the progress
    hook raises DownloadPaused and the partial file (.part) is left in place
    so a later call with the same url/output_dir/quality can resume it. If
    `control.request_cancel()` was called instead, the hook raises
    DownloadCancelled; the caller is expected to remove the partial file
    since the download has been abandoned rather than paused.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    is_audio = quality == "audio_only"
    video_only = not is_audio and not include_audio
    format_selector = (
        "bestvideo[ext=mp4]/bestvideo" if video_only
        else FORMAT_SELECTORS.get(quality, FORMAT_SELECTORS["best"])
    )

    def hook(data):
        filename = data.get("filename")
        if filename and control is not None:
            control.current_filename = filename
        if control is not None and control.is_cancel_requested():
            raise DownloadCancelled()
        if control is not None and control.is_pause_requested():
            raise DownloadPaused()
        if progress_callback is None:
            return
        if data["status"] == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else None
            speed = data.get("speed")
            eta = data.get("eta")
            progress_callback({
                "status": "downloading", "percent": percent,
                "speed": f"{speed / 1024 / 1024:.1f} MB/s" if speed else "-",
                "eta": f"{eta // 60:02d}:{eta % 60:02d}" if eta else "-",
            })
        elif data["status"] == "finished":
            progress_callback({"status": "finished", "percent": 100.0})

    output_template = str(Path(output_dir) / "%(title)s.%(ext)s")
    if duplicate_mode == "Ask me":
        probe_options = {"outtmpl": output_template, "noplaylist": True, "quiet": True, "no_warnings": True}
        if cookies_from_browser and cookies_from_browser != "none":
            probe_options["cookiesfrombrowser"] = (cookies_from_browser,)
        with yt_dlp.YoutubeDL(probe_options) as probe:
            probe_info = probe.extract_info(url, download=False)
            existing_path = Path(probe.prepare_filename(probe_info))
        if is_audio:
            existing_path = existing_path.with_suffix(".mp3")
        if existing_path.exists():
            action = duplicate_callback(str(existing_path)) if duplicate_callback else "skip"
            if action == "skip":
                raise FileExistsError(f"Skipped because this file already exists: {existing_path.name}")
            duplicate_mode = "Overwrite" if action == "overwrite" else "Rename automatically"

    ydl_opts = {
        "format": format_selector,
        "outtmpl": output_template,
        "progress_hooks": [hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Windows-safe filenames: strip characters like : ? * " < > | and
        # reserved names (CON, NUL, ...), and cap component length so very
        # long titles don't blow past Windows' path-length limits.
        "windowsfilenames": True,
        "trim_file_name": 150,
        # Resume partial downloads (the default, made explicit) and keep the
        # .part file on disk rather than deleting it, so a paused download
        # can be resumed instead of restarting from zero.
        "continuedl": True,
        "nopart": False,
        # Network resilience: retry transient failures a fixed number of
        # times with a fixed delay; permanent errors (private video, 404,
        # unsupported site) are raised by yt-dlp immediately without a retry.
        "retries": retry_attempts,
        "fragment_retries": retry_attempts,
        "retry_sleep_functions": {
            "http": lambda n, _d=retry_delay: _d,
            "fragment": lambda n, _d=retry_delay: _d,
        },
    }
    if speed_limit_bytes:
        ydl_opts["ratelimit"] = speed_limit_bytes
    if cookies_from_browser and cookies_from_browser != "none":
        # Reads cookies directly from the named browser's local profile via
        # yt-dlp - we never read, display, log, or transmit the cookie data
        # ourselves; only the browser name is ever stored or logged.
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if duplicate_mode == "Rename automatically":
        ydl_opts["outtmpl"] = str(Path(output_dir) / "%(title)s [%(id)s].%(ext)s")
    elif duplicate_mode == "Overwrite":
        ydl_opts["overwrites"] = True
    else:
        ydl_opts["nooverwrites"] = True

    ffmpeg_path = tool_check.find_ffmpeg()
    if ffmpeg_path:
        ydl_opts["ffmpeg_location"] = ffmpeg_path

    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": audio_bitrate,
        }]
        ydl_opts["keepvideo"] = keep_original
    elif not video_only:
        container = CONTAINER_FORMATS.get(format_container)
        if container:
            ydl_opts["merge_output_format"] = container
        # "best" (None) leaves the container unforced so yt-dlp remuxes into
        # whatever native container avoids re-encoding, rather than always
        # transcoding to mp4.

    # Subtitles only make sense for video downloads, not audio extraction.
    if not is_audio and subtitle_mode != "none":
        ydl_opts["writesubtitles"] = True
        ydl_opts["subtitlesformat"] = "srt/best"
        if subtitle_mode == "english":
            ydl_opts["subtitleslangs"] = ["en"]
        else:
            ydl_opts["allsubtitles"] = True
        if embed_subs:
            ydl_opts.setdefault("postprocessors", []).append({"key": "FFmpegEmbedSubtitle"})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
    except OSError as exc:
        message = str(exc).lower()
        if getattr(exc, "winerror", None) in (3, 206) or any(m in message for m in _LONG_PATH_MARKERS):
            raise OSError(
                "The destination file path is too long for Windows. Try a "
                "shorter output folder, or a shorter video title if possible."
            ) from exc
        raise

    if is_audio:
        final_path = str(Path(filepath).with_suffix(".mp3"))
    else:
        container = CONTAINER_FORMATS.get(format_container)
        if container:
            forced_path = str(Path(filepath).with_suffix("." + container))
            final_path = forced_path if Path(forced_path).exists() else filepath
        else:
            final_path = filepath
    if not Path(final_path).exists():
        final_path = filepath
    return DownloadResult(filepath=final_path, title=info.get("title", "video"))

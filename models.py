"""
models.py
---------
Shared data model for queue items. Having one DownloadItem object (instead
of passing loose values around) makes the queue considerably easier to
maintain, and gives every part of the app the same vocabulary for status.
"""

import uuid
from dataclasses import dataclass, field


class Status:
    """The six states a download can be in.

    Paused, Cancelled and Failed are kept deliberately distinct:
    - Paused: stopped safely, partial file kept, can be resumed.
    - Cancelled: abandoned by the user, partial file removed.
    - Failed: stopped because of an error, partial file's fate depends on
      the error but the item itself is not silently retried.
    """

    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

    # Statuses from which a queue run will pick an item up.
    RUNNABLE = (QUEUED, PAUSED)

    ICONS = {
        QUEUED: "\u23f3",       # hourglass
        DOWNLOADING: "\u2b07",  # down arrow
        PAUSED: "\u23f8",       # pause
        COMPLETED: "\u2713",    # check
        FAILED: "\u274c",       # cross mark
        CANCELLED: "\u2715",    # multiplication x
    }


@dataclass
class DownloadItem:
    url: str
    output_folder: str
    quality: str
    include_audio: bool = True
    keep_original: bool = False
    duplicate_mode: str = "Rename automatically"
    audio_bitrate: str = "192"
    format_container: str = "best"
    subtitle_mode: str = "none"
    embed_subs: bool = False
    speed_limit_bytes: int | None = None
    cookies_from_browser: str = "none"  # "none" | "chrome" | "edge" | "firefox" | "brave"
    title: str = "Video link"
    status: str = Status.QUEUED
    filepath: str | None = None
    error: str | None = None
    error_detail: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

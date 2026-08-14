"""
models.py
---------
Shared data model for one queue item as it moves through LoopClip's
pipeline: download -> (optionally) analyze for a loop point -> (optionally)
trim to it -> (optionally) crossfade into a seamless loop -> finished.

This replaces the older, simpler DownloadItem/Status pair (download-only,
no processing stages) that used to live here. That older model is what
download_manager.py and queue_store.py were built against - since nothing
in the current app (main.py) imports either of those two files anymore,
they're now orphaned. Safe to delete, or leave in place unused.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional


class QueueState:
    """The states one queue item can move through.

    Not every item visits every state: ANALYZING/TRIMMING are skipped
    when `auto_loop` is off, and LOOPING is skipped when `seamless_loop`
    is off. See queue_manager.py's `_process_item` for the exact flow.
    """

    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    ANALYZING = "Analyzing"
    TRIMMING = "Trimming"
    LOOPING = "Looping"
    FINISHED = "Finished"
    ERROR = "Error"
    CANCELLED = "Cancelled"


@dataclass
class QueueItem:
    # -- set once, at creation (mirrors main.py's QueueItem(...) call) -----
    url: str
    quality: str
    no_audio: bool
    audio_bitrate: str
    video_bitrate: str
    auto_loop: bool
    loop_method: str
    similarity: float
    search_start: str
    search_stop: str
    downsample: int
    min_loop_seconds: Optional[float]
    max_loop_seconds: Optional[float]
    output_folder: str
    delete_original: bool
    seamless_loop: bool
    crossfade_seconds: float

    # -- runtime/display fields, updated by queue_manager as the item runs --
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = QueueState.QUEUED
    title: str = ""
    percent: float = 0.0
    operation: str = ""
    speed: str = "-"
    eta: str = "-"
    output_name: Optional[str] = None
    error_message: Optional[str] = None
    warning_message: Optional[str] = None

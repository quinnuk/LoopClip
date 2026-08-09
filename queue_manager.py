"""
queue_manager.py
----------------
Owns the download queue and the single background worker thread that
processes it one item at a time. Keeps download/FFmpeg logic (downloader.py
/ processor.py) separate from the UI (main.py) - the UI only ever talks to
this module and reacts to its on_update callback.
"""

import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import downloader
import loop_detector
import processor


class QueueState:
    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    ANALYZING = "Analyzing"
    TRIMMING = "Trimming"
    LOOPING = "Creating Loop"
    FINISHED = "Finished"
    ERROR = "Error"
    CANCELLED = "Cancelled"


@dataclass
class QueueItem:
    url: str
    quality: str
    no_audio: bool
    audio_bitrate: str
    video_bitrate: str

    # Auto loop-detection settings (LoopyCut-style), replacing manual trim.
    auto_loop: bool
    loop_method: str            # "combined" | "ssim" | "histogram" | "hash"
    similarity: float           # 0-100
    search_start: str           # "HH:MM:SS" - start of the window to analyze
    search_stop: str            # "HH:MM:SS", or "" to search to the end of the video
    downsample: int             # analyze every Nth frame
    min_loop_seconds: Optional[float]  # None = auto
    max_loop_seconds: Optional[float]  # None = auto

    output_folder: str
    delete_original: bool
    seamless_loop: bool
    crossfade_seconds: str

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = QueueState.QUEUED
    operation: str = "Queued"
    percent: float = 0.0
    speed: str = "-"
    eta: str = "-"
    error_message: str = ""
    warning_message: str = ""
    output_name: str = ""
    title: str = ""
    detected_loop: "Optional[loop_detector.LoopCandidate]" = None


# Fallback message used only when nothing else below matched - one per
# pipeline stage, so a failure that happens *after* the download has
# already succeeded (analysis, trimming, loop creation) never gets
# reported to the user as a download problem. Keyed by the QueueState the
# item was in when the exception was raised.
_GENERIC_STAGE_ERRORS = {
    QueueState.DOWNLOADING: "The video could not be downloaded. Check the URL or try again.",
    QueueState.ANALYZING: (
        "Something went wrong while analyzing the video for a seamless loop. "
        "Try a different method, lower the similarity %, or widen the search window."
    ),
    QueueState.TRIMMING: "Something went wrong while processing the video. Check the settings and try again.",
    QueueState.LOOPING: "Something went wrong while creating the seamless loop. Try a shorter crossfade length.",
}
_DEFAULT_GENERIC_ERROR = "Something went wrong while processing this item. Please try again."


def _friendly_error(message: str, stage: str = QueueState.DOWNLOADING) -> str:
    low = message.lower()
    if "ffmpeg executable was not found" in low:
        return (
            "FFmpeg was not found. Please install FFmpeg or add it to your "
            "system PATH."
        )
    if "ffmpeg reported an error" in low:
        # This is FFmpeg's own diagnostic output (not a raw Python
        # traceback), so it's safe and useful to show as-is.
        return message
    if "no seamless loop found" in low or "not enough frames" in low or "could not open video for analysis" in low or "leaves no room to search" in low:
        # loop_detector's own messages are already user-friendly guidance
        # (lower similarity / widen search window / try another method).
        return message
    if "could not locate the downloaded file" in low:
        return "The download finished but the file could not be located. Please try again."
    if "private video" in low:
        return "This video is private and can't be downloaded."
    if "unavailable" in low or "removed" in low:
        return "This video is unavailable (it may have been removed or region-locked)."
    if "sign in" in low or "age" in low:
        return "This video is age-restricted and can't be downloaded without sign-in."
    if "urlopen" in low or "network" in low or "timed out" in low:
        return "The video could not be downloaded. Check your internet connection and try again."
    if "no space left" in low or "disk full" in low or "not enough space" in low:
        return "Ran out of disk space partway through. Free up some space and try again."
    # Nothing specific matched - fall back to a message for whichever
    # stage the item was actually in, instead of always assuming download.
    return _GENERIC_STAGE_ERRORS.get(stage, _DEFAULT_GENERIC_ERROR)


# Minimum free space to require even when the item's size can't be
# estimated (e.g. no trim, so the full source video's size is unknown
# up-front) - a conservative floor rather than a real estimate.
_MIN_FREE_SPACE_BYTES = 2 * 1024 ** 3  # 2 GB
_FREE_SPACE_SAFETY_MARGIN = 512 * 1024 ** 2  # 512 MB on top of any estimate


def _estimate_output_bytes(item: "QueueItem") -> Optional[int]:
    """
    Rough estimate of the final output file's size in bytes, when it's
    knowable in advance. With auto loop-detection, the output duration
    isn't known until the loop is actually found, so this returns None
    (the disk-space check then just falls back to a conservative flat
    minimum) rather than guessing.
    """
    return None


def _check_disk_space(item: "QueueItem", temp_root: Path) -> Optional[str]:
    """
    Checks that there's likely enough free space to complete this item,
    on both the output folder's drive and the temp folder's drive (they
    may be different drives). Returns an error message string if space
    looks insufficient, or None if it's fine to proceed.

    This is a best-effort pre-flight check, not a guarantee - actual
    usage during download can still vary, which is why _friendly_error
    above also recognizes an actual "no space left" failure if one
    happens anyway.
    """
    estimated = _estimate_output_bytes(item)
    required = (estimated or _MIN_FREE_SPACE_BYTES) + _FREE_SPACE_SAFETY_MARGIN

    for label, path in (("output folder", item.output_folder), ("temp folder", str(temp_root))):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free
        except OSError:
            continue  # Can't check this one - don't block on it.
        if free < required:
            free_gb = free / 1024 ** 3
            required_gb = required / 1024 ** 3
            return (
                f"Not enough free disk space on the drive for your {label} "
                f"({free_gb:.1f} GB free, roughly {required_gb:.1f} GB needed). "
                "Free up some space and try again."
            )
    return None


class QueueManager:
    """
    Manages a list of QueueItems and a worker thread that processes items
    with state QUEUED one at a time, in order. New items can be appended
    at any time, including while the worker is running.

    Cancel immediately terminates whatever yt-dlp/FFmpeg process is
    currently running, marks the in-progress item Cancelled, and stops the
    whole queue (it does not continue with remaining items).
    """

    def __init__(self, on_update: Callable[[], None], temp_root: Path):
        self.items: list[QueueItem] = []
        self._lock = threading.RLock()
        self._on_update = on_update
        self._temp_root = temp_root
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._cancel_requested = False
        self._current_process = {"proc": None}

    # ------------------------------------------------------------ public

    def add_item(self, item: QueueItem):
        with self._lock:
            self.items.append(item)
        self._notify()

    def clear_all(self) -> bool:
        """
        Removes every item from the queue. Refuses to do anything while
        the queue is actively running, since that would orphan whatever
        item is mid-download - the caller (UI) should Cancel first if a
        download is in progress. Returns True if the queue was cleared,
        False if it was left untouched because the queue is running.
        """
        with self._lock:
            if self._running:
                return False
            self.items.clear()
        self._notify()
        return True

    def remove_item(self, item_id: str) -> bool:
        """
        Removes a single item from the queue, but only if it's still
        QUEUED (hasn't started downloading/processing yet) - removing an
        item that's actively in progress isn't safe without also killing
        whatever FFmpeg/yt-dlp process is working on it. Returns True if
        removed, False if the item wasn't found or wasn't safe to remove.
        """
        with self._lock:
            for i, item in enumerate(self.items):
                if item.id == item_id and item.state == QueueState.QUEUED:
                    del self.items[i]
                    self._notify()
                    return True
        return False

    def retry_item(self, item_id: str) -> bool:
        """
        Resets a failed (or cancelled) item back to QUEUED so it will be
        picked up again - either immediately, if the queue is still
        running and simply hasn't reached the end yet, or the next time
        Start Queue is clicked. Returns True if the item was reset, False
        if it wasn't found or wasn't in a retryable state.
        """
        with self._lock:
            for item in self.items:
                if item.id == item_id and item.state in (QueueState.ERROR, QueueState.CANCELLED):
                    item.state = QueueState.QUEUED
                    item.operation = "Queued"
                    item.percent = 0.0
                    item.speed = "-"
                    item.eta = "-"
                    item.error_message = ""
                    self._notify()
                    return True
        return False

    def has_url(self, url: str) -> bool:
        """Whether this URL is already present in the queue (any state)."""
        with self._lock:
            return any(item.url == url for item in self.items)

    def start(self):
        """Starts the worker thread if it isn't already running."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._cancel_requested = False
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        self._notify()

    def cancel(self):
        """Stops the current item immediately and halts the whole queue."""
        with self._lock:
            self._cancel_requested = True
            proc = self._current_process.get("proc")
        # Kill the running process right away, don't just wait for the
        # worker's own poll loop to notice the cancel flag.
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._notify()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested

    # ------------------------------------------------------------ worker

    def _worker(self):
        while True:
            if self._is_cancelled():
                break
            with self._lock:
                next_item = next(
                    (i for i in self.items if i.state == QueueState.QUEUED), None
                )
            if next_item is None:
                break
            self._process_item(next_item)
            if self._is_cancelled():
                break

        with self._lock:
            self._running = False
        self._notify()

    def _process_item(self, item: QueueItem):
        space_error = _check_disk_space(item, self._temp_root)
        if space_error:
            item.state = QueueState.ERROR
            item.error_message = space_error
            item.operation = "Error"
            self._notify()
            return

        item.state = QueueState.DOWNLOADING
        item.operation = "Downloading..."
        item.percent = 0.0
        self._notify()

        temp_dir = self._temp_root / item.id
        out_path = None

        def on_progress(info):
            if info["status"] == "downloading":
                item.percent = info.get("percent") or item.percent
                item.speed = info.get("speed", "-")
                item.eta = info.get("eta", "-")
                item.operation = "Downloading..."
            elif info["status"] == "finished":
                item.percent = 100.0
                item.operation = "Merging streams..."
            self._notify()

        try:
            section_range = None
            search_offset = 0.0
            # Only download the window that's actually needed, instead of
            # the whole video. This used to only apply when auto-loop
            # detection was on, which meant ticking "Limit search window"
            # by itself (with auto-loop off) silently did nothing and the
            # full video was downloaded - now it applies whenever a search
            # window is set, whether or not loop detection is also used.
            # A padding buffer is added on each side so there's still
            # margin for a frame-accurate local FFmpeg cut afterward, even
            # if yt-dlp's section cut lands a little early/late.
            search_start_s = processor.hms_to_seconds(item.search_start)
            search_stop_s = (
                processor.hms_to_seconds(item.search_stop) if item.search_stop else None
            )
            # A bounded section download (both start and stop known) is
            # only possible - and only re-bases the downloaded file's
            # timeline - when a stop time was actually given. An
            # open-ended stop means the whole video is downloaded from
            # 0:00, so the timeline offset stays 0 in that case.
            if search_stop_s is not None:
                pad_seconds = 30
                section_start = max(0, search_start_s - pad_seconds)
                section_end = search_stop_s + pad_seconds
                section_range = (section_start, section_end)
                search_offset = section_start

            result = downloader.download_video(
                url=item.url,
                temp_dir=str(temp_dir),
                quality=item.quality,
                no_audio=item.no_audio,
                section_range_seconds=section_range,
                progress_callback=on_progress,
                process_holder=self._current_process,
                cancel_check=self._is_cancelled,
            )
            item.title = result.title

            local_start_seconds = 0.0
            local_duration_seconds = None

            if item.auto_loop:
                item.state = QueueState.ANALYZING
                item.operation = "Analyzing frames for a seamless loop..."
                item.percent = 0.0
                self._notify()

                def on_analysis_progress(phase, done, total):
                    item.percent = (done / total * 100.0) if total else 0.0
                    item.operation = (
                        "Extracting frames..." if phase == "extract" else "Comparing frames..."
                    )
                    self._notify()

                # search_start/stop are relative to the *original* video;
                # re-base them to the downloaded (possibly section-cut)
                # file's own timeline before handing off to loop_detector.
                analyze_start = max(0.0, processor.hms_to_seconds(item.search_start) - search_offset)
                analyze_stop = (
                    (processor.hms_to_seconds(item.search_stop) - search_offset)
                    if item.search_stop else None
                )

                try:
                    best = loop_detector.find_best_loop(
                        result.filepath,
                        start=analyze_start,
                        stop=analyze_stop,
                        method=item.loop_method,
                        similarity_threshold=item.similarity,
                        min_loop_seconds=item.min_loop_seconds,
                        max_loop_seconds=item.max_loop_seconds,
                        downsample=item.downsample,
                        progress_callback=on_analysis_progress,
                    )
                except loop_detector.LoopDetectionError as exc:
                    raise RuntimeError(str(exc)) from exc

                item.detected_loop = best
                local_start_seconds = best.start
                local_duration_seconds = best.duration

            # Manual trim: a search window was set but auto-loop detection
            # is off, so there's no loop analysis to derive a cut point
            # from - just cut exactly the window the user asked for. This
            # is what makes "Limit search window" alone (no loop detection)
            # actually produce a short clip instead of the full video.
            manual_trim = (
                not item.auto_loop
                and section_range is not None
                and search_stop_s is not None
            )
            if manual_trim:
                local_start_seconds = max(0.0, search_start_s - search_offset)
                local_duration_seconds = max(0.0, search_stop_s - search_start_s)

            trim_enabled = item.auto_loop or manual_trim
            needs_ffmpeg = trim_enabled or (
                not item.no_audio and item.audio_bitrate != "original"
            )

            if needs_ffmpeg:
                item.state = QueueState.TRIMMING
                item.operation = "Trimming..." if trim_enabled else "Processing audio..."
                self._notify()

                if trim_enabled:
                    out_name = processor.build_output_filename(result.title, local_duration_seconds)
                else:
                    out_name = f"{processor.sanitize_title(result.title)}.mp4"
                out_path = str(Path(item.output_folder) / out_name)

                processor.process_video(
                    input_path=result.filepath,
                    output_path=out_path,
                    trim_enabled=trim_enabled,
                    start_hms=processor.seconds_to_ffmpeg_time(local_start_seconds),
                    duration_hms=processor.seconds_to_ffmpeg_time(local_duration_seconds or 0),
                    audio_bitrate=item.audio_bitrate,
                    video_bitrate=item.video_bitrate,
                    no_audio=item.no_audio,
                    process_holder=self._current_process,
                    cancel_check=self._is_cancelled,
                )
            else:
                item.operation = "Saving..."
                self._notify()
                clean_title = processor.sanitize_title(result.title)
                out_path = str(Path(item.output_folder) / f"{clean_title}.mp4")
                if str(Path(result.filepath).resolve()) != str(Path(out_path).resolve()):
                    shutil.move(result.filepath, out_path)

            if self._is_cancelled():
                self._mark_cancelled(item, temp_dir, out_path)
                return

            if item.seamless_loop:
                item.state = QueueState.LOOPING
                item.operation = "Creating seamless loop..."
                self._notify()
                # Write to the item's own temp folder first, then swap it
                # into place - if this step fails or gets cancelled partway,
                # the temp folder (and this partial file with it) is
                # cleaned up automatically by the finally block below,
                # rather than leaving a half-written file in the output
                # folder.
                looped_path = str(temp_dir / "seamless_loop.mp4")

                def on_loop_warning(message: str, _item=item):
                    _item.warning_message = message

                processor.apply_seamless_loop(
                    input_path=out_path,
                    output_path=looped_path,
                    crossfade_seconds=float(item.crossfade_seconds),
                    video_bitrate=item.video_bitrate,
                    no_audio=item.no_audio,
                    process_holder=self._current_process,
                    cancel_check=self._is_cancelled,
                    on_warning=on_loop_warning,
                )
                os.remove(out_path)
                shutil.move(looped_path, out_path)

            if self._is_cancelled():
                self._mark_cancelled(item, temp_dir, out_path)
                return

            if item.delete_original:
                try:
                    if (
                        Path(result.filepath).exists()
                        and Path(result.filepath).resolve() != Path(out_path).resolve()
                    ):
                        os.remove(result.filepath)
                except OSError:
                    pass

            item.state = QueueState.FINISHED
            item.operation = "Finished" if not item.warning_message else "Finished (see note)"
            item.percent = 100.0
            item.output_name = Path(out_path).name

        except (downloader.CancelledError, processor.CancelledError):
            self._mark_cancelled(item, temp_dir, out_path)
            return
        except Exception as exc:  # noqa: BLE001 - never let a bad video kill the queue
            failed_stage = item.state  # capture before it's overwritten below
            item.state = QueueState.ERROR
            item.error_message = _friendly_error(str(exc), stage=failed_stage)
            item.operation = "Error"
        finally:
            self._cleanup_temp(temp_dir)
            self._notify()

    def _mark_cancelled(self, item: QueueItem, temp_dir: Path, out_path: Optional[str]):
        item.state = QueueState.CANCELLED
        item.operation = "Cancelled"
        # Remove any partially-written output file left behind by FFmpeg.
        try:
            if out_path and Path(out_path).exists():
                os.remove(out_path)
        except OSError:
            pass
        self._cleanup_temp(temp_dir)
        self._notify()

    def _cleanup_temp(self, temp_dir: Path):
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            pass

    def _notify(self):
        try:
            self._on_update()
        except Exception:
            pass

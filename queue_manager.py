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
import processor


class QueueState:
    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    TRIMMING = "Trimming"
    FINISHED = "Finished"
    ERROR = "Error"
    CANCELLED = "Cancelled"


@dataclass
class QueueItem:
    url: str
    quality: str
    no_audio: bool
    audio_bitrate: str
    trim_enabled: bool
    trim_start: str
    trim_duration: str
    output_folder: str
    delete_original: bool

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = QueueState.QUEUED
    operation: str = "Queued"
    percent: float = 0.0
    speed: str = "-"
    eta: str = "-"
    error_message: str = ""
    output_name: str = ""
    title: str = ""


def _friendly_error(message: str) -> str:
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
    return "The video could not be downloaded. Check the URL or try again."


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
            result = downloader.download_video(
                url=item.url,
                temp_dir=str(temp_dir),
                quality=item.quality,
                no_audio=item.no_audio,
                progress_callback=on_progress,
                process_holder=self._current_process,
                cancel_check=self._is_cancelled,
            )
            item.title = result.title

            needs_ffmpeg = item.trim_enabled or (
                not item.no_audio and item.audio_bitrate != "original"
            )

            if needs_ffmpeg:
                item.state = QueueState.TRIMMING
                item.operation = "Trimming..." if item.trim_enabled else "Processing audio..."
                self._notify()

                if item.trim_enabled:
                    out_name = processor.build_output_filename(
                        result.title, item.trim_duration
                    )
                else:
                    out_name = f"{processor.sanitize_title(result.title)}.mp4"
                out_path = str(Path(item.output_folder) / out_name)

                processor.process_video(
                    input_path=result.filepath,
                    output_path=out_path,
                    trim_enabled=item.trim_enabled,
                    start_hms=item.trim_start,
                    duration_hms=item.trim_duration,
                    audio_bitrate=item.audio_bitrate,
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
            item.operation = "Finished"
            item.percent = 100.0
            item.output_name = Path(out_path).name

        except (downloader.CancelledError, processor.CancelledError):
            self._mark_cancelled(item, temp_dir, out_path)
            return
        except Exception as exc:  # noqa: BLE001 - never let a bad video kill the queue
            item.state = QueueState.ERROR
            item.error_message = _friendly_error(str(exc))
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

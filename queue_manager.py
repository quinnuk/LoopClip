"""
queue_manager.py
-----------------
Owns the queue of QueueItems and runs them, one at a time, through the
full pipeline: download (downloader.py) -> optionally analyze for a loop
point (loop_detector.py) -> optionally trim to it (processor.py) ->
optionally crossfade into a seamless loop (processor.py) -> finished.

Replaces the old, download-only QueueManager + separate DownloadManager
pair. main.py only ever talks to this one object, on a single background
worker thread - matches the app's stated behaviour of processing videos
"one at a time", not concurrently.

ASSUMPTIONS made while rebuilding this (flag if wrong, easy to change):
  - auto_loop off  -> no ANALYZING/TRIMMING; the full downloaded clip is
    used as-is for the LOOPING step (or as the final file, if
    seamless_loop is also off).
  - seamless_loop off -> no LOOPING; whatever came out of the previous
    step (trimmed clip, or full download) becomes the final output as-is.
  - delete_original controls only whether the raw downloaded file is
    deleted after a successful run; purely-intermediate temp files
    (e.g. a trimmed-but-not-yet-looped clip) are always cleaned up.
  - Queue persistence (save/reload across app restarts) has been dropped
    entirely, since main.py never calls anything like persist()/
    load_persisted() - queue_store.py is now unused.
"""

import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

import downloader
import loop_detector
import processor
from models import QueueItem, QueueState


class QueueManager:
    def __init__(self, on_update: Callable[[], None], temp_root: Path):
        self.items: list[QueueItem] = []
        self.on_update = on_update
        self.temp_root = Path(temp_root)
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._cancel_requested = threading.Event()
        self._active_control: Optional[downloader.DownloadControl] = None

    # -- queue contents ------------------------------------------------------
    def add_item(self, item: QueueItem) -> None:
        with self._lock:
            self.items.append(item)
        self._notify()

    def has_url(self, url: str) -> bool:
        """Used by main.py to warn about likely duplicates before adding -
        matches the old find_duplicate()'s logic of ignoring tracking
        params and not counting items that already errored/cancelled."""
        normalized = downloader.normalize_url(url)
        return any(
            downloader.normalize_url(item.url) == normalized
            for item in self.items
            if item.state not in (QueueState.ERROR, QueueState.CANCELLED)
        )

    def remove_item(self, item_id: str) -> bool:
        with self._lock:
            for item in self.items:
                if item.id == item_id and item.state == QueueState.QUEUED:
                    self.items.remove(item)
                    self._notify()
                    return True
        return False

    def retry_item(self, item_id: str) -> bool:
        with self._lock:
            for item in self.items:
                if item.id == item_id and item.state in (QueueState.ERROR, QueueState.CANCELLED):
                    item.state = QueueState.QUEUED
                    item.error_message = None
                    item.warning_message = None
                    item.percent = 0.0
                    item.operation = ""
                    self._notify()
                    return True
        return False

    def clear_all(self) -> None:
        with self._lock:
            self.items = []
        self._notify()

    # -- running the queue ----------------------------------------------------
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._cancel_requested.clear()
        self._worker = threading.Thread(target=self._run_queue, daemon=True)
        self._worker.start()

    def cancel(self) -> None:
        """Stops the current item immediately and halts the queue - matches
        the docstring in main.py. Remaining QUEUED items are left alone so
        the user can hit Start Queue again."""
        self._cancel_requested.set()
        with self._lock:
            control = self._active_control
        if control:
            control.request_cancel()

    def _notify(self) -> None:
        self.on_update()

    def _cancel_check(self) -> bool:
        return self._cancel_requested.is_set()

    def _next_queued(self) -> Optional[QueueItem]:
        with self._lock:
            for item in self.items:
                if item.state == QueueState.QUEUED:
                    return item
        return None

    def _run_queue(self) -> None:
        self.temp_root.mkdir(parents=True, exist_ok=True)
        while not self._cancel_requested.is_set():
            item = self._next_queued()
            if item is None:
                break
            self._process_item(item)
        self._notify()

    # -- per-item pipeline ------------------------------------------------------
    def _process_item(self, item: QueueItem) -> None:
        downloaded_path = None
        trimmed_path = None
        try:
            downloaded_path = self._run_download(item)
            if self._cancel_requested.is_set():
                raise downloader.DownloadCancelled()

            if item.auto_loop:
                trimmed_path, duration_seconds = self._run_analyze_and_trim(item, downloaded_path)
                if self._cancel_requested.is_set():
                    raise downloader.DownloadCancelled()
                source_for_final = trimmed_path
            else:
                source_for_final = downloaded_path
                duration_seconds = processor.get_duration_seconds(downloaded_path)

            final_path = self._run_loop_or_finalize(item, source_for_final, duration_seconds)

            # Purely-intermediate temp file (trimmed-but-not-yet-looped) -
            # always clean it up if it's not itself the final file.
            if trimmed_path and Path(trimmed_path).exists() and Path(trimmed_path) != Path(final_path):
                try:
                    Path(trimmed_path).unlink()
                except OSError:
                    pass
            # Raw download - only cleaned up if the user asked for that.
            if (
                item.delete_original
                and downloaded_path
                and Path(downloaded_path).exists()
                and Path(downloaded_path) != Path(final_path)
            ):
                try:
                    Path(downloaded_path).unlink()
                except OSError:
                    pass

            item.output_name = Path(final_path).name
            item.state = QueueState.FINISHED
            self._notify()
        except downloader.DownloadCancelled:
            item.state = QueueState.CANCELLED
            self._notify()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as-is
            item.state = QueueState.ERROR
            item.error_message = str(exc)
            self._notify()

    def _run_download(self, item: QueueItem) -> str:
        item.state = QueueState.DOWNLOADING
        item.operation = "Downloading"
        item.percent = 0.0
        self._notify()

        control = downloader.DownloadControl()
        with self._lock:
            self._active_control = control
        if self._cancel_requested.is_set():
            control.request_cancel()

        def on_progress(info: dict):
            if info.get("percent") is not None:
                item.percent = info["percent"]
            if "speed" in info:
                item.speed = info["speed"]
            if "eta" in info:
                item.eta = info["eta"]
            self._notify()

        try:
            result = downloader.download_video(
                url=item.url,
                output_dir=str(self.temp_root),
                quality=item.quality,
                progress_callback=on_progress,
                include_audio=not item.no_audio,
                audio_bitrate=item.audio_bitrate,
                control=control,
                duplicate_mode="Overwrite",
            )
        finally:
            with self._lock:
                self._active_control = None

        item.title = result.title
        item.percent = 100.0
        self._notify()
        return result.filepath

    def _run_analyze_and_trim(self, item: QueueItem, source_path: str) -> tuple[str, float]:
        item.state = QueueState.ANALYZING
        item.operation = "Analyzing for a seamless loop point"
        item.percent = 0.0
        self._notify()

        def on_analyze_progress(stage: str, i: int, n: int):
            item.percent = (i / n * 100.0) if n else 0.0
            self._notify()

        start_seconds = processor.hms_to_seconds(item.search_start) if item.search_start else 0.0
        stop_seconds = processor.hms_to_seconds(item.search_stop) if item.search_stop else None

        candidate = loop_detector.find_best_loop(
            source_path,
            start=start_seconds,
            stop=stop_seconds,
            method=item.loop_method,
            similarity_threshold=item.similarity,
            min_loop_seconds=item.min_loop_seconds,
            max_loop_seconds=item.max_loop_seconds,
            downsample=item.downsample,
            progress_callback=on_analyze_progress,
        )

        item.state = QueueState.TRIMMING
        item.operation = "Trimming to the detected loop"
        item.percent = 0.0
        self._notify()

        trimmed_path = str(self.temp_root / f"{item.id}_trimmed.mp4")
        processor.process_video(
            input_path=source_path,
            output_path=trimmed_path,
            trim_enabled=True,
            start_hms=processor.seconds_to_ffmpeg_time(candidate.start),
            duration_hms=processor.seconds_to_ffmpeg_time(candidate.duration),
            audio_bitrate=item.audio_bitrate,
            video_bitrate=item.video_bitrate,
            no_audio=item.no_audio,
            process_holder={},
            cancel_check=self._cancel_check,
        )
        item.percent = 100.0
        self._notify()
        return trimmed_path, candidate.duration

    def _run_loop_or_finalize(self, item: QueueItem, source_path: str, duration_seconds: float) -> str:
        Path(item.output_folder).mkdir(parents=True, exist_ok=True)
        output_filename = processor.build_output_filename(item.title, duration_seconds)
        output_path = processor.unique_output_path(str(Path(item.output_folder) / output_filename))

        if item.seamless_loop:
            item.state = QueueState.LOOPING
            item.operation = "Building the seamless loop"
            item.percent = 0.0
            self._notify()

            warnings: list[str] = []
            processor.apply_seamless_loop(
                input_path=source_path,
                output_path=output_path,
                crossfade_seconds=item.crossfade_seconds,
                video_bitrate=item.video_bitrate,
                no_audio=item.no_audio,
                process_holder={},
                cancel_check=self._cancel_check,
                on_warning=warnings.append,
            )
            if warnings:
                item.warning_message = warnings[-1]
            item.percent = 100.0
            self._notify()
        else:
            # No crossfade requested - whatever we were handed (trimmed
            # clip, or the full download) is already the final output.
            shutil.move(source_path, output_path)

        return output_path

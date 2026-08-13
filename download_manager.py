"""
download_manager.py
--------------------
Runs up to N downloads concurrently against a QueueManager's items. Each
active download gets its own DownloadControl, so pausing or cancelling one
item never touches another. All UI-facing callbacks are delivered via
`root.after`, so the caller (ui.py) never has to worry about which thread
it's being called from.

Architecture this completes: UI -> Queue Manager -> Download Manager ->
Downloader -> yt-dlp.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import downloader
import history
from models import DownloadItem, Status
from queue_manager import QueueManager


@dataclass
class DownloadCallbacks:
    """Everything the download manager needs to report back to the UI.
    All are invoked on the Tk main thread via root.after.
    """
    on_item_start: Callable[[DownloadItem], None]
    on_progress: Callable[[DownloadItem, dict], None]
    on_item_finished: Callable[[DownloadItem], None]
    on_queue_finished: Callable[[bool], None]  # arg: was this a user-requested stop?
    ask_duplicate_action: Callable[[str], str]


class DownloadManager:
    def __init__(self, root, queue_manager: QueueManager, app_logger):
        self.root = root
        self.queue_manager = queue_manager
        self.app_logger = app_logger
        self.active_controls: dict[str, downloader.DownloadControl] = {}
        self._controls_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active_worker_count = 0

    def is_running(self) -> bool:
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

    def start(self, worker_count: int, callbacks: DownloadCallbacks) -> None:
        if self.is_running():
            return
        self.stop_requested.clear()
        self._active_worker_count = worker_count
        self._threads = [
            threading.Thread(target=self._worker_loop, args=(callbacks,), daemon=True)
            for _ in range(worker_count)
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        """Stop claiming new items and pause whatever is currently active."""
        self.stop_requested.set()
        for control in self._active_control_list():
            control.request_pause()

    def pause_item(self, item_id: str) -> bool:
        control = self._active_controls_get(item_id)
        if control:
            control.request_pause()
            return True
        return False

    def cancel_item(self, item_id: str) -> bool:
        control = self._active_controls_get(item_id)
        if control:
            control.request_cancel()
            return True
        return False

    def cancel_all_active(self) -> None:
        for control in self._active_control_list():
            control.request_cancel()

    def _active_controls_get(self, item_id: str):
        with self._controls_lock:
            return self.active_controls.get(item_id)

    def _active_control_list(self):
        with self._controls_lock:
            return list(self.active_controls.values())

    def _worker_loop(self, callbacks: DownloadCallbacks) -> None:
        while True:
            if self.stop_requested.is_set():
                break
            item = self.queue_manager.claim_next()
            if item is None:
                break
            control = downloader.DownloadControl()
            with self._controls_lock:
                self.active_controls[item.id] = control
            self.root.after(0, callbacks.on_item_start, item)
            try:
                result = downloader.download_video(
                    url=item.url, output_dir=item.output_folder, quality=item.quality,
                    include_audio=item.include_audio, keep_original=item.keep_original,
                    duplicate_mode=item.duplicate_mode, control=control,
                    duplicate_callback=callbacks.ask_duplicate_action, audio_bitrate=item.audio_bitrate,
                    format_container=item.format_container, subtitle_mode=item.subtitle_mode,
                    embed_subs=item.embed_subs, speed_limit_bytes=item.speed_limit_bytes,
                    cookies_from_browser=item.cookies_from_browser,
                    progress_callback=lambda info, queued_item=item: self.root.after(
                        0, callbacks.on_progress, queued_item, info
                    ),
                )
                item.title = result.title
                item.filepath = result.filepath
                item.status = Status.COMPLETED
                item.error = None
                item.error_detail = None
                self.app_logger.info("Download completed: %s", item.title)
                history.add_entry(
                    title=item.title, url=item.url, status=Status.COMPLETED,
                    quality=item.quality, filepath=item.filepath,
                )
            except downloader.DownloadPaused:
                item.status = Status.PAUSED
                self.app_logger.info("Download paused: %s", item.title)
            except downloader.DownloadCancelled:
                item.status = Status.CANCELLED
                self._cleanup_partial(control.current_filename)
                self.app_logger.info("Download cancelled: %s", item.title)
            except FileExistsError as exc:
                item.status = Status.CANCELLED
                item.error = str(exc)
            except Exception as exc:  # noqa: BLE001
                reason, detail = downloader.classify_error(exc)
                item.status = Status.FAILED
                item.error = reason
                item.error_detail = detail
                self.app_logger.warning("Download failed: %s (%s)", item.title, reason)
                history.add_entry(
                    title=item.title, url=item.url, status=Status.FAILED,
                    quality=item.quality, error=reason,
                )
            finally:
                with self._controls_lock:
                    self.active_controls.pop(item.id, None)
            self.root.after(0, callbacks.on_item_finished, item)
            self.queue_manager.persist()
        self.root.after(0, self._worker_done, callbacks)

    def _worker_done(self, callbacks: DownloadCallbacks) -> None:
        self._active_worker_count -= 1
        if self._active_worker_count <= 0:
            callbacks.on_queue_finished(self.stop_requested.is_set())

    @staticmethod
    def _cleanup_partial(filename: str | None) -> None:
        """Remove the partial file (and yt-dlp's .part sidecar) for a cancelled download."""
        if not filename:
            return
        for candidate in (Path(filename), Path(str(filename) + ".part")):
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass

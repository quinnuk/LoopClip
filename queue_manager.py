"""
queue_manager.py
-----------------
Owns the list of DownloadItems and the operations on it: add, remove,
reorder, clear, retry, duplicate detection, and persistence to disk. No Tk
dependency - the UI renders `queue_manager.items` and calls these methods;
the download manager calls `claim_next()` to pull work.
"""

import threading

import downloader
import queue_store
from models import DownloadItem, Status


class QueueManager:
    def __init__(self):
        self.items: list[DownloadItem] = []
        self._lock = threading.Lock()

    # -- adding / finding ------------------------------------------------
    def add(self, item: DownloadItem) -> None:
        self.items.append(item)

    def find_duplicate(self, url: str) -> DownloadItem | None:
        """Find an existing item pointing at the same video, ignoring
        tracking query parameters. Cancelled/Failed items don't count,
        since the user has already indicated those didn't succeed.
        """
        normalized = downloader.normalize_url(url)
        for item in self.items:
            if item.status in (Status.CANCELLED, Status.FAILED):
                continue
            if downloader.normalize_url(item.url) == normalized:
                return item
        return None

    # -- removing / reordering -------------------------------------------
    def remove(self, item: DownloadItem) -> None:
        if item in self.items:
            self.items.remove(item)

    def move(self, item: DownloadItem, direction: int) -> bool:
        """Swap `item` with its neighbour in `direction` (-1 up, +1 down).
        Returns False if the move isn't possible (already at an edge).
        """
        index = self.items.index(item)
        new_index = index + direction
        if not (0 <= new_index < len(self.items)):
            return False
        self.items[index], self.items[new_index] = self.items[new_index], self.items[index]
        return True

    def clear_completed(self) -> None:
        self.items = [item for item in self.items if item.status != Status.COMPLETED]

    def clear_failed(self) -> None:
        self.items = [item for item in self.items if item.status != Status.FAILED]

    # -- retrying ----------------------------------------------------------
    @staticmethod
    def retry(item: DownloadItem) -> None:
        item.status = Status.QUEUED
        item.error = None
        item.error_detail = None

    def retry_all_failed(self) -> int:
        count = 0
        for item in self.items:
            if item.status == Status.FAILED:
                self.retry(item)
                count += 1
        return count

    # -- stats / claiming work for the download manager --------------------
    def stats(self) -> dict:
        counts = {status: 0 for status in (
            Status.QUEUED, Status.DOWNLOADING, Status.PAUSED,
            Status.COMPLETED, Status.FAILED, Status.CANCELLED,
        )}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def has_runnable(self) -> bool:
        return any(item.status in Status.RUNNABLE for item in self.items)

    def claim_next(self) -> DownloadItem | None:
        """Thread-safely pick the next runnable item and mark it Downloading
        immediately, so two workers can never both claim the same item.
        """
        with self._lock:
            for item in self.items:
                if item.status in Status.RUNNABLE:
                    item.status = Status.DOWNLOADING
                    return item
        return None

    # -- persistence ---------------------------------------------------------
    def persist(self) -> None:
        queue_store.save_queue(self.items)

    def load_persisted(self) -> list[DownloadItem]:
        return queue_store.load_queue()

    def discard_persisted(self) -> None:
        queue_store.clear_queue_file()

    def replace_with(self, items: list[DownloadItem]) -> None:
        self.items = items

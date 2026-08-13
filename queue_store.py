"""
queue_store.py
--------------
Persists the download queue to disk so it can be restored if the app
closes (or crashes) with items still queued or paused.

Downloads are never auto-restarted on launch — the caller is expected to
ask the user "Resume queue?" before repopulating the live queue from what
this module loads.
"""

import json
from dataclasses import asdict
from pathlib import Path

from models import DownloadItem, Status
from settings import app_data_dir

_QUEUE_FILE_NAME = "queue.json"


def _queue_path() -> Path:
    return app_data_dir() / _QUEUE_FILE_NAME


def save_queue(queue: list[DownloadItem]) -> None:
    """Persist items that still need attention.

    Completed items are dropped - they don't need to be resumed. An item
    that was "Downloading" at save time is written as "Paused": we weren't
    asked to pause it, but on restart the only honest option is to offer
    to resume it, never to silently continue a download the user didn't
    explicitly restart.
    """
    to_save = []
    for item in queue:
        if item.status == Status.COMPLETED:
            continue
        data = asdict(item)
        if data["status"] == Status.DOWNLOADING:
            data["status"] = Status.PAUSED
        to_save.append(data)

    path = _queue_path()
    try:
        if to_save:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def load_queue() -> list[DownloadItem]:
    path = _queue_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    items = []
    for data in raw:
        try:
            items.append(DownloadItem(**data))
        except TypeError:
            # Saved by an older/newer version of the app with a different
            # DownloadItem shape; skip rather than crash on startup.
            continue
    return items


def clear_queue_file() -> None:
    path = _queue_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

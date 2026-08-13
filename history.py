"""
history.py
----------
Persistent download history, independent of the live queue, so completed
and failed downloads are still on record after the queue is cleared or the
app is restarted.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from settings import app_data_dir

_HISTORY_FILE_NAME = "history.json"
_MAX_ENTRIES = 500  # keep the file from growing without bound


@dataclass
class HistoryEntry:
    title: str
    url: str
    status: str  # "Completed" or "Failed"
    timestamp: str
    quality: str
    filepath: str | None = None
    error: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


def _history_path() -> Path:
    return app_data_dir() / _HISTORY_FILE_NAME


def _entry_to_dict(entry: HistoryEntry) -> dict:
    return {
        "title": entry.title, "url": entry.url, "status": entry.status,
        "timestamp": entry.timestamp, "quality": entry.quality,
        "filepath": entry.filepath, "error": entry.error, "id": entry.id,
    }


def load_history() -> list[HistoryEntry]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    entries = []
    for data in raw:
        try:
            entries.append(HistoryEntry(**data))
        except TypeError:
            continue
    return entries


def _save_history(entries: list[HistoryEntry]) -> None:
    path = _history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([_entry_to_dict(e) for e in entries], f, indent=2)
    except OSError:
        pass


def add_entry(
    title: str, url: str, status: str, quality: str,
    filepath: str | None = None, error: str | None = None,
) -> None:
    entries = load_history()
    entries.insert(0, HistoryEntry(
        title=title, url=url, status=status, quality=quality,
        filepath=filepath, error=error,
        timestamp=datetime.now().strftime("%d/%m/%Y %H:%M"),
    ))
    _save_history(entries[:_MAX_ENTRIES])


def remove_entry(entry_id: str) -> None:
    _save_history([e for e in load_history() if e.id != entry_id])


def clear_history() -> None:
    path = _history_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

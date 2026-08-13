"""
library.py
----------
Persistent record of finished LoopClip outputs, stored as JSON in
%APPDATA%\\LoopClip\\library.json (mirrors settings.py's approach).

This exists so the Library UI in main.py can show past videos (title,
output file, source URL, when it finished) even after the in-memory
queue has been cleared or the app has been restarted - the queue list
itself is not persisted, only this library is.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import settings as settings_module

# Capped so the library file can't grow forever on a machine that's been
# processing videos for years - oldest entries are dropped first.
_MAX_ENTRIES = 500


def _library_path() -> Path:
    base = settings_module._appdata_base(settings_module.APP_NAME)
    base.mkdir(parents=True, exist_ok=True)
    return base / "library.json"


def load_entries() -> list:
    """Returns all library entries, most-recently-finished first."""
    path = _library_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def add_entry(title: str, output_path: str, source_url: str, warning: str = "") -> None:
    """Records one finished item at the front of the library."""
    entries = load_entries()
    entries.insert(0, {
        "title": title,
        "output_path": output_path,
        "source_url": source_url,
        "warning": warning,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_entries(entries[:_MAX_ENTRIES])


def remove_entry(index: int) -> None:
    """Removes the entry at `index` (as returned by load_entries())."""
    entries = load_entries()
    if 0 <= index < len(entries):
        del entries[index]
        _save_entries(entries)


def _save_entries(entries: list) -> None:
    """Writes the library atomically, same pattern as settings.py uses -
    a crash or forced close mid-write can never corrupt the file."""
    path = _library_path()
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".library_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
            raise
    except OSError:
        pass  # Non-fatal - the app should keep working even if this fails.
